"""Database-backed local login, throttling, revocable sessions and CSRF tokens."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import SessionLocal
from app.local_identity import LOGIN_NAME_PATTERN, verify_password
from app.models import (
    LocalAccountModel,
    LocalLoginAttemptModel,
    LocalRoleGrantModel,
    LocalSessionModel,
)
from app.repository import new_id, now_utc

SESSION_COOKIE = "autope_session"
SESSION_IDLE = timedelta(minutes=30)
SESSION_ABSOLUTE = timedelta(hours=8)
LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_BLOCK = timedelta(minutes=15)
DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$YXV0b3BlLWR1bW15LXNhbHQ$"
    "TZfxIPhlobSvzbV5GVQzPav71KoqC7omKaeoq32iYtc"
)


class LocalLoginRequest(BaseModel):
    login_name: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("login_name")
    @classmethod
    def normalize_login_name(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if LOGIN_NAME_PATTERN.fullmatch(normalized) is None:
            raise ValueError("Invalid login name format.")
        return normalized


@dataclass(frozen=True)
class LocalSessionIdentity:
    account_id: str
    display_name: str
    roles: frozenset[str]
    is_platform_admin: bool
    csrf_token: str


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _digest(secret: str, purpose: str, value: str) -> str:
    return hmac.new(secret.encode(), f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()


def _csrf_token(secret: str, session_token: str) -> str:
    return _digest(secret, "csrf", session_token)


def _throttle_keys(settings: Settings, login_name: str, source: str) -> tuple[str, str]:
    return (
        _digest(settings.session_secret, "login", login_name),
        _digest(settings.session_secret, "source", source),
    )


def _lock_throttle_keys(session: Session, keys: tuple[str, str]) -> None:
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return
    for key in sorted(keys):
        lock_id = int.from_bytes(bytes.fromhex(key)[:8], "big", signed=True)
        session.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})


def _blocked(bucket: LocalLoginAttemptModel | None, now: datetime) -> bool:
    return bucket is not None and bucket.blocked_until is not None and _aware(bucket.blocked_until) > now


def _record_failure(
    session: Session, key: str, threshold: int, now: datetime
) -> None:
    bucket = session.get(LocalLoginAttemptModel, key)
    if bucket is None:
        bucket = LocalLoginAttemptModel(
            key_digest=key, failure_count=0, window_started_at=now, blocked_until=None
        )
        session.add(bucket)
    if now - _aware(bucket.window_started_at) >= LOGIN_WINDOW:
        bucket.failure_count = 0
        bucket.window_started_at = now
        bucket.blocked_until = None
    bucket.failure_count += 1
    if bucket.failure_count >= threshold:
        bucket.blocked_until = now + LOGIN_BLOCK


def login_local_account(
    session: Session, settings: Settings, login_name: str, password: str, source: str
) -> str:
    if len(settings.session_secret) < 32:
        raise HTTPException(status_code=503, detail="Local session configuration is unavailable.")
    now = now_utc()
    login_key, source_key = _throttle_keys(settings, login_name, source)
    try:
        with session.begin():
            _lock_throttle_keys(session, (login_key, source_key))
            if _blocked(session.get(LocalLoginAttemptModel, login_key), now) or _blocked(
                session.get(LocalLoginAttemptModel, source_key), now
            ):
                raise HTTPException(status_code=429, detail="Too many login attempts. Try later.")
            account = session.scalar(
                select(LocalAccountModel).where(LocalAccountModel.login_name == login_name)
            )
            stored_hash = (
                account.password_hash
                if account is not None and account.status == "active" and account.password_hash
                else DUMMY_PASSWORD_HASH
            )
            verified = verify_password(password, stored_hash)
            if account is None or account.status != "active" or not account.password_hash or not verified:
                _record_failure(session, login_key, 5, now)
                _record_failure(session, source_key, 100, now)
                return ""
            login_bucket = session.get(LocalLoginAttemptModel, login_key)
            if login_bucket is not None:
                login_bucket.failure_count = 0
                login_bucket.blocked_until = None
                login_bucket.window_started_at = now
            token = secrets.token_urlsafe(48)
            csrf_token = _csrf_token(settings.session_secret, token)
            session.add(
                LocalSessionModel(
                    id=new_id("session"),
                    account_id=account.id,
                    token_digest=_digest(settings.session_secret, "session", token),
                    csrf_digest=hashlib.sha256(csrf_token.encode()).hexdigest(),
                    created_at=now,
                    last_seen_at=now,
                    expires_at=now + SESSION_ABSOLUTE,
                    revoked_at=None,
                )
            )
            return token
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Identity storage is unavailable.",
        ) from error


def authenticate_local_request(request: Request, settings: Settings) -> LocalSessionIdentity:
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token or len(settings.session_secret) < 32:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required.")
    now = now_utc()
    try:
        with SessionLocal() as session:
            stored = session.scalar(
                select(LocalSessionModel).where(
                    LocalSessionModel.token_digest == _digest(settings.session_secret, "session", token)
                )
            )
            if (
                stored is None
                or stored.revoked_at is not None
                or _aware(stored.expires_at) <= now
                or _aware(stored.last_seen_at) + SESSION_IDLE <= now
            ):
                raise HTTPException(status_code=401, detail="Session expired. Log in again.")
            account = session.get(LocalAccountModel, stored.account_id)
            if account is None or account.status != "active":
                raise HTTPException(status_code=401, detail="Session expired. Log in again.")
            csrf_token = _csrf_token(settings.session_secret, token)
            if not hmac.compare_digest(
                stored.csrf_digest, hashlib.sha256(csrf_token.encode()).hexdigest()
            ):
                raise HTTPException(status_code=401, detail="Session expired. Log in again.")
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                origin = request.headers.get("Origin", "")
                supplied_csrf = request.headers.get("X-CSRF-Token", "")
                if origin not in settings.cors_origins or not hmac.compare_digest(
                    supplied_csrf, csrf_token
                ):
                    raise HTTPException(status_code=403, detail="CSRF check failed.")
            roles = frozenset(
                session.scalars(
                    select(LocalRoleGrantModel.role).where(LocalRoleGrantModel.account_id == account.id)
                )
            )
            if now - _aware(stored.last_seen_at) >= timedelta(seconds=60):
                stored.last_seen_at = now
                session.commit()
            return LocalSessionIdentity(
                account_id=account.id,
                display_name=account.display_name,
                roles=roles,
                is_platform_admin=account.is_platform_admin,
                csrf_token=csrf_token,
            )
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Identity storage is unavailable.",
        ) from error


def revoke_local_session(token: str, settings: Settings) -> None:
    if not token:
        return
    try:
        with SessionLocal.begin() as session:
            stored = session.scalar(
                select(LocalSessionModel).where(
                    LocalSessionModel.token_digest == _digest(settings.session_secret, "session", token)
                )
            )
            if stored is not None:
                stored.revoked_at = now_utc()
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Identity storage is unavailable.",
        ) from error
