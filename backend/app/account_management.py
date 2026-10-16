"""Platform-admin account lifecycle, separate from business-role authorization."""

from __future__ import annotations

import secrets

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.local_identity import LOGIN_NAME_PATTERN, _token_digest
from app.local_recovery import RECOVERY_LIFETIME, _revoke_sessions
from app.models import LocalAccountModel, LocalOneTimeCredentialModel, LocalRoleGrantModel
from app.repository import audit, new_id, now_utc, roles_for_account
from app.security import ALL_ROLES

ACCOUNT_LOCK_ID = 2026101503


class CreateAccountRequest(BaseModel):
    login_name: str = Field(min_length=3, max_length=64)
    display_name: str = Field(min_length=1, max_length=160)
    roles: list[str] = Field(default_factory=list)
    is_platform_admin: bool = False

    @field_validator("login_name")
    @classmethod
    def normalize_login_name(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if LOGIN_NAME_PATTERN.fullmatch(normalized) is None:
            raise ValueError("Invalid login name format.")
        return normalized

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(ord(character) < 32 for character in normalized):
            raise ValueError("Invalid display name.")
        return normalized

    @field_validator("roles")
    @classmethod
    def validate_roles(cls, value: list[str]) -> list[str]:
        roles = sorted(set(value))
        if len(roles) != len(value) or not set(roles).issubset(ALL_ROLES):
            raise ValueError("Roles must be unique known business roles.")
        return roles


class UpdateAccountRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    status: str | None = None
    is_platform_admin: bool | None = None
    roles: list[str] | None = None

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        return CreateAccountRequest.normalize_display_name(value) if value is not None else None

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        if value is not None and value not in {"active", "disabled"}:
            raise ValueError("Status must be active or disabled.")
        return value

    @field_validator("roles")
    @classmethod
    def validate_roles(cls, value: list[str] | None) -> list[str] | None:
        return CreateAccountRequest.validate_roles(value) if value is not None else None


def _account_lock(session: Session) -> None:
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": ACCOUNT_LOCK_ID})


def _require_admin(session: Session, actor_id: str) -> None:
    actor = session.scalar(
        select(LocalAccountModel).where(LocalAccountModel.id == actor_id).with_for_update()
    )
    if actor is None or actor.status != "active" or not actor.is_platform_admin:
        raise HTTPException(status_code=403, detail="Platform administrator required.")


def account_summary(session: Session, account: LocalAccountModel) -> dict[str, object]:
    return {
        "id": account.id,
        "login_name": account.login_name,
        "display_name": account.display_name,
        "status": account.status,
        "is_platform_admin": account.is_platform_admin,
        "roles": sorted(session.scalars(
            select(LocalRoleGrantModel.role).where(LocalRoleGrantModel.account_id == account.id)
        )),
    }


def list_accounts(session: Session, actor_id: str) -> list[dict[str, object]]:
    try:
        _require_admin(session, actor_id)
        accounts = session.scalars(
            select(LocalAccountModel).order_by(LocalAccountModel.login_name)
        ).all()
        return [account_summary(session, account) for account in accounts]
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Identity storage is unavailable.") from error


def create_account(
    session: Session, actor_id: str, payload: CreateAccountRequest, request_id: str
) -> dict[str, object]:
    now = now_utc()
    account_id = new_id("account")
    invite_token = secrets.token_urlsafe(48)
    try:
        with session.begin():
            _require_admin(session, actor_id)
            account = LocalAccountModel(
                id=account_id,
                login_name=payload.login_name,
                display_name=payload.display_name,
                password_hash=None,
                status="active",
                is_platform_admin=payload.is_platform_admin,
                created_at=now,
                updated_at=now,
            )
            session.add(account)
            session.flush()
            for role in payload.roles:
                session.add(LocalRoleGrantModel(
                    id=new_id("role"), account_id=account_id, role=role, created_at=now
                ))
            session.add(LocalOneTimeCredentialModel(
                id=new_id("credential"), purpose="password_reset", account_id=account_id,
                token_digest=_token_digest(invite_token), expires_at=now + RECOVERY_LIFETIME,
                consumed_at=None, created_at=now,
            ))
            audit(
                session, actor_id=actor_id, action="identity.account_created",
                resource_type="local_account", resource_id=account_id, request_id=request_id,
                detail={"source": "local_verified", "result": "success", "roles": payload.roles,
                        "platform_admin": payload.is_platform_admin},
                identity_source="local_verified", actor_roles=roles_for_account(session, actor_id),
                object_version=account.updated_at.isoformat(),
            )
        return {"account_id": account_id, "invite_token": invite_token}
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="Login name already exists.") from error
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Identity storage is unavailable.") from error


def update_account(
    session: Session, actor_id: str, target_id: str, payload: UpdateAccountRequest,
    request_id: str,
) -> dict[str, object]:
    now = now_utc()
    try:
        with session.begin():
            _account_lock(session)
            _require_admin(session, actor_id)
            target = session.scalar(
                select(LocalAccountModel).where(LocalAccountModel.id == target_id).with_for_update()
            )
            if target is None:
                raise HTTPException(status_code=404, detail="Account not found.")
            if target.id == actor_id and (
                payload.status == "disabled" or payload.is_platform_admin is False
            ):
                raise HTTPException(status_code=409, detail="Cannot disable or demote yourself.")
            if target.is_platform_admin and (
                payload.status == "disabled" or payload.is_platform_admin is False
            ):
                active_admins = session.scalar(select(func.count()).select_from(LocalAccountModel).where(
                    LocalAccountModel.status == "active",
                    LocalAccountModel.is_platform_admin.is_(True),
                )) or 0
                if active_admins <= 1:
                    raise HTTPException(status_code=409, detail="Cannot remove the last administrator.")
            if payload.display_name is not None:
                target.display_name = payload.display_name
            if payload.status is not None:
                target.status = payload.status
            if payload.is_platform_admin is not None:
                target.is_platform_admin = payload.is_platform_admin
            if payload.roles is not None:
                session.execute(delete(LocalRoleGrantModel).where(
                    LocalRoleGrantModel.account_id == target_id
                ))
                for role in payload.roles:
                    session.add(LocalRoleGrantModel(
                        id=new_id("role"), account_id=target_id, role=role, created_at=now
                    ))
            if target.status == "disabled":
                _revoke_sessions(session, target_id)
            target.updated_at = now
            session.flush()
            response = account_summary(session, target)
            audit(
                session, actor_id=actor_id, action="identity.account_updated",
                resource_type="local_account", resource_id=target_id, request_id=request_id,
                detail={"source": "local_verified", "result": "success", "status": target.status,
                        "roles": response["roles"], "platform_admin": target.is_platform_admin},
                identity_source="local_verified", actor_roles=roles_for_account(session, actor_id),
                object_version=target.updated_at.isoformat(),
            )
        return response
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Identity storage is unavailable.") from error
