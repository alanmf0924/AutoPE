"""Password changes and one-time recovery without OA, SMTP, or default passwords."""

from __future__ import annotations

import secrets
import sys
from datetime import UTC, timedelta

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.local_identity import _hash_password, _token_digest, verify_password
from app.models import LocalAccountModel, LocalOneTimeCredentialModel, LocalSessionModel
from app.repository import audit, new_id, now_utc, roles_for_account

RECOVERY_LIFETIME = timedelta(minutes=15)
RECOVERY_LOCK_ID = 2026101502


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)

    @field_validator("new_password")
    @classmethod
    def bound_password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 1024:
            raise ValueError("Password is too long.")
        return value


class ResetPasswordRequest(BaseModel):
    reset_token: str = Field(min_length=32, max_length=256)
    new_password: str = Field(min_length=12, max_length=1024)

    @field_validator("new_password")
    @classmethod
    def bound_password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 1024:
            raise ValueError("Password is too long.")
        return value


def _recovery_lock(session: Session) -> None:
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": RECOVERY_LOCK_ID})


def _revoke_sessions(session: Session, account_id: str) -> None:
    session.execute(
        update(LocalSessionModel)
        .where(LocalSessionModel.account_id == account_id, LocalSessionModel.revoked_at.is_(None))
        .values(revoked_at=now_utc())
    )


def change_password(
    session: Session, account_id: str, payload: ChangePasswordRequest, request_id: str
) -> None:
    now = now_utc()
    new_hash = _hash_password(payload.new_password)
    try:
        with session.begin():
            account = session.scalar(
                select(LocalAccountModel)
                .where(LocalAccountModel.id == account_id)
                .with_for_update()
            )
            if (
                account is None
                or account.status != "active"
                or account.password_hash is None
                or not verify_password(payload.current_password, account.password_hash)
            ):
                raise HTTPException(status_code=403, detail="Current password is incorrect.")
            account.password_hash = new_hash
            account.updated_at = now
            _revoke_sessions(session, account_id)
            audit(
                session,
                actor_id=account_id,
                action="identity.password_changed",
                resource_type="local_account",
                resource_id=account_id,
                request_id=request_id,
                detail={"source": "local_verified", "result": "success"},
                identity_source="local_verified", actor_roles=roles_for_account(session, account_id),
                object_version=account.updated_at.isoformat(),
            )
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Identity storage is unavailable.") from error


def issue_recovery_token(
    session: Session, account_id: str, issuer_id: str, request_id: str, *, server_issued: bool
) -> str:
    token = secrets.token_urlsafe(48)
    now = now_utc()
    purpose = "server_recovery" if server_issued else "password_reset"
    try:
        with session.begin():
            _recovery_lock(session)
            account = session.scalar(
                select(LocalAccountModel)
                .where(LocalAccountModel.id == account_id)
                .with_for_update()
            )
            if account is None or account.status != "active":
                raise HTTPException(status_code=404, detail="Active account not found.")
            if server_issued and not account.is_platform_admin:
                raise HTTPException(status_code=403, detail="Server recovery requires an administrator.")
            if not server_issued:
                issuer = session.scalar(
                    select(LocalAccountModel)
                    .where(LocalAccountModel.id == issuer_id)
                    .with_for_update()
                )
                if issuer is None or issuer.status != "active" or not issuer.is_platform_admin:
                    raise HTTPException(status_code=403, detail="Platform administrator required.")
            session.execute(
                update(LocalOneTimeCredentialModel)
                .where(
                    LocalOneTimeCredentialModel.account_id == account_id,
                    LocalOneTimeCredentialModel.purpose.in_(["password_reset", "server_recovery"]),
                    LocalOneTimeCredentialModel.consumed_at.is_(None),
                )
                .values(consumed_at=now)
            )
            session.add(
                LocalOneTimeCredentialModel(
                    id=new_id("credential"),
                    purpose=purpose,
                    account_id=account_id,
                    token_digest=_token_digest(token),
                    expires_at=now + RECOVERY_LIFETIME,
                    consumed_at=None,
                    created_at=now,
                )
            )
            audit(
                session,
                actor_id=issuer_id,
                action="identity.recovery_issued",
                resource_type="local_account",
                resource_id=account_id,
                request_id=request_id,
                detail={
                    "source": "server_operator" if server_issued else "local_verified",
                    "method": purpose,
                    "result": "success",
                },
                identity_source="server_operator" if server_issued else "local_verified",
                actor_roles=[] if server_issued else roles_for_account(session, issuer_id),
                object_version=account.updated_at.isoformat(),
            )
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Identity storage is unavailable.") from error
    return token


def reset_password(session: Session, payload: ResetPasswordRequest, request_id: str) -> None:
    now = now_utc()
    new_hash = _hash_password(payload.new_password)
    try:
        with session.begin():
            _recovery_lock(session)
            credential = session.scalar(
                select(LocalOneTimeCredentialModel)
                .where(
                    LocalOneTimeCredentialModel.token_digest == _token_digest(payload.reset_token),
                    LocalOneTimeCredentialModel.purpose.in_(["password_reset", "server_recovery"]),
                )
                .with_for_update()
            )
            expires_at = None if credential is None else credential.expires_at
            if expires_at is not None and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if (
                credential is None
                or credential.account_id is None
                or credential.consumed_at is not None
                or expires_at is None
                or expires_at <= now
            ):
                raise HTTPException(status_code=403, detail="Invalid recovery credential.")
            account = session.scalar(
                select(LocalAccountModel)
                .where(LocalAccountModel.id == credential.account_id)
                .with_for_update()
            )
            if account is None or account.status != "active":
                raise HTTPException(status_code=403, detail="Invalid recovery credential.")
            credential.consumed_at = now
            account.password_hash = new_hash
            account.updated_at = now
            _revoke_sessions(session, account.id)
            audit(
                session,
                actor_id=account.id,
                action="identity.password_recovered",
                resource_type="local_account",
                resource_id=account.id,
                request_id=request_id,
                detail={
                    "source": "server_operator" if credential.purpose == "server_recovery" else "one_time_credential",
                    "method": credential.purpose,
                    "result": "success",
                },
                identity_source="server_operator" if credential.purpose == "server_recovery" else "one_time_credential",
                object_version=account.updated_at.isoformat(),
            )
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Identity storage is unavailable.") from error


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] != "recover-admin":
        raise SystemExit("Usage: python -m app.local_recovery recover-admin LOGIN_NAME")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit("Run interactively on the server terminal; token output is not logged.")
    login_name = sys.argv[2].strip().casefold()
    with SessionLocal() as session:
        account_id = session.scalar(
            select(LocalAccountModel.id).where(LocalAccountModel.login_name == login_name)
        )
    if account_id is None:
        raise SystemExit("Active platform administrator not found.")
    with SessionLocal() as session:
        try:
            token = issue_recovery_token(
                session, account_id, "server_operator", new_id("req"), server_issued=True
            )
        except HTTPException as error:
            raise SystemExit(str(error.detail)) from error
    print("Enter this one-time token on the platform recovery page within 15 minutes:")
    print(token)


if __name__ == "__main__":
    main()
