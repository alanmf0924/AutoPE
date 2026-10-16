"""Local identity bootstrap. No OA, email service, or default administrator is required."""

from __future__ import annotations

import hashlib
import re
import secrets
import sys
from datetime import UTC, timedelta

from cryptography.exceptions import InvalidKey
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from fastapi import HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import LocalAccountModel, LocalOneTimeCredentialModel, LocalRoleGrantModel
from app.repository import audit, new_id, now_utc

BOOTSTRAP_LOCK_ID = 2026101501
BOOTSTRAP_LIFETIME = timedelta(minutes=15)
LOGIN_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}\Z")


class BootstrapAdminRequest(BaseModel):
    bootstrap_token: str = Field(min_length=32, max_length=256)
    login_name: str = Field(min_length=3, max_length=64)
    display_name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=12, max_length=1024)

    @field_validator("login_name")
    @classmethod
    def normalize_login_name(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if LOGIN_NAME_PATTERN.fullmatch(normalized) is None:
            raise ValueError("Login name must use 3-64 letters, digits, dots, underscores or hyphens.")
        return normalized

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(ord(character) < 32 for character in normalized):
            raise ValueError("Display name must not be blank or contain control characters.")
        return normalized

    @field_validator("password")
    @classmethod
    def bound_password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 1024:
            raise ValueError("Password is too long.")
        return value


def _bootstrap_lock(session: Session) -> None:
    """Serialize issuance and consumption on deployed PostgreSQL."""
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": BOOTSTRAP_LOCK_ID})


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _hash_password(password: str) -> str:
    return Argon2id(
        salt=secrets.token_bytes(16),
        length=32,
        iterations=3,
        lanes=4,
        memory_cost=64 * 1024,
    ).derive_phc_encoded(password.encode("utf-8"))


def verify_password(password: str, password_hash: str) -> bool:
    try:
        Argon2id.verify_phc_encoded(password.encode("utf-8"), password_hash)
        return True
    except (InvalidKey, ValueError):
        return False


def is_initialized(session: Session) -> bool:
    return session.scalar(select(LocalAccountModel.id).limit(1)) is not None


def issue_bootstrap_token(session: Session) -> str:
    """Issue one short-lived token; a later issuance invalidates the previous token."""
    token = secrets.token_urlsafe(48)
    now = now_utc()
    with session.begin():
        _bootstrap_lock(session)
        if is_initialized(session):
            raise ValueError("Platform has already been initialized.")
        session.execute(
            update(LocalOneTimeCredentialModel)
            .where(
                LocalOneTimeCredentialModel.purpose == "bootstrap",
                LocalOneTimeCredentialModel.consumed_at.is_(None),
            )
            .values(consumed_at=now)
        )
        session.add(
            LocalOneTimeCredentialModel(
                id=new_id("credential"),
                purpose="bootstrap",
                account_id=None,
                token_digest=_token_digest(token),
                expires_at=now + BOOTSTRAP_LIFETIME,
                consumed_at=None,
                created_at=now,
            )
        )
    return token


def bootstrap_admin(session: Session, payload: BootstrapAdminRequest, request_id: str) -> str:
    """Consume a one-time server token and create the sole initial administrator."""
    password_hash = _hash_password(payload.password)
    now = now_utc()
    account_id = new_id("account")
    try:
        with session.begin():
            _bootstrap_lock(session)
            if is_initialized(session):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already initialized.")
            credential = session.scalar(
                select(LocalOneTimeCredentialModel)
                .where(
                    LocalOneTimeCredentialModel.purpose == "bootstrap",
                    LocalOneTimeCredentialModel.token_digest == _token_digest(payload.bootstrap_token),
                )
                .with_for_update()
            )
            expires_at = None if credential is None else credential.expires_at
            if expires_at is not None and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if credential is None or credential.consumed_at is not None or expires_at is None or expires_at <= now:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="Invalid setup credential."
                )
            credential.consumed_at = now
            session.add(
                LocalAccountModel(
                    id=account_id,
                    login_name=payload.login_name,
                    display_name=payload.display_name,
                    password_hash=password_hash,
                    status="active",
                    is_platform_admin=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                LocalRoleGrantModel(
                    id=new_id("role"), account_id=account_id, role="operator", created_at=now
                )
            )
            audit(
                session,
                actor_id=account_id,
                action="identity.bootstrap_completed",
                resource_type="local_account",
                resource_id=account_id,
                request_id=request_id,
                detail={"source": "local_verified", "method": "one_time_server_token"},
                identity_source="local_verified", actor_roles=["operator"],
                object_version=account_id,
            )
    except IntegrityError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Setup conflict.") from error
    return account_id


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] != "bootstrap-token":
        raise SystemExit("Usage: python -m app.local_identity bootstrap-token")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit("Run interactively on the server terminal; token output is not logged.")
    with SessionLocal() as session:
        try:
            token = issue_bootstrap_token(session)
        except ValueError as error:
            raise SystemExit(str(error)) from error
    print("Open the platform setup page, enter this one-time token within 15 minutes:")
    print(token)


if __name__ == "__main__":
    main()
