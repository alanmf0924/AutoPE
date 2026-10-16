from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app import local_session
from app.config import Settings
from app.local_identity import _hash_password
from app.models import Base, LocalAccountModel, LocalSessionModel
from app.repository import now_utc


@pytest.fixture
def identity_store(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(local_session, "SessionLocal", session_factory)
    settings = Settings(
        environment="test",
        database_url="sqlite+pysqlite:///:memory:",
        allowed_origins="http://127.0.0.1:5174",
        auth_disabled=False,
        seed_demo_data=False,
        jwt_hs256_secret="",
        secret_resolver="disabled",
        external_secret_dir="",
        worker_lease_seconds=180,
        worker_heartbeat_max_age_seconds=90,
        require_worker_heartbeat=False,
        session_secret="s" * 32,
    )
    now = now_utc()
    with session_factory.begin() as session:
        session.add(LocalAccountModel(
            id="account-test", login_name="real.user", display_name="Real User",
            password_hash=_hash_password("valid-password-123"), status="active",
            is_platform_admin=False, created_at=now, updated_at=now,
        ))
    yield session_factory, settings
    engine.dispose()


def request_for(token: str, method: str = "GET", origin: str = "", csrf: str = "") -> Request:
    headers = [(b"cookie", f"autope_session={token}".encode())]
    if origin:
        headers.append((b"origin", origin.encode()))
    if csrf:
        headers.append((b"x-csrf-token", csrf.encode()))
    return Request({"type": "http", "method": method, "path": "/api/session", "headers": headers})


def login(session_factory: sessionmaker[Session], settings: Settings) -> str:
    with session_factory() as session:
        return local_session.login_local_account(
            session, settings, "real.user", "valid-password-123", "127.0.0.1"
        )


def test_local_session_requires_valid_cookie_and_csrf(identity_store) -> None:  # type: ignore[no-untyped-def]
    session_factory, settings = identity_store
    token = login(session_factory, settings)
    assert token
    identity = local_session.authenticate_local_request(request_for(token), settings)
    assert identity.account_id == "account-test"
    assert identity.csrf_token
    assert "valid-password-123" not in identity.csrf_token
    with pytest.raises(HTTPException) as missing:
        local_session.authenticate_local_request(request_for(token, "POST", settings.cors_origins[0]), settings)
    assert missing.value.status_code == 403
    with pytest.raises(HTTPException) as other_origin:
        local_session.authenticate_local_request(
            request_for(token, "POST", "https://evil.example", identity.csrf_token), settings
        )
    assert other_origin.value.status_code == 403
    assert local_session.authenticate_local_request(
        request_for(token, "POST", settings.cors_origins[0], identity.csrf_token), settings
    ).account_id == "account-test"
    with pytest.raises(HTTPException) as forged:
        local_session.authenticate_local_request(request_for("forged-cookie"), settings)
    assert forged.value.status_code == 401


def test_logout_idle_expiry_and_disabled_account_revoke_access(identity_store) -> None:  # type: ignore[no-untyped-def]
    session_factory, settings = identity_store
    token = login(session_factory, settings)
    local_session.revoke_local_session(token, settings)
    with pytest.raises(HTTPException) as revoked:
        local_session.authenticate_local_request(request_for(token), settings)
    assert revoked.value.status_code == 401

    token = login(session_factory, settings)
    with session_factory.begin() as session:
        stored = session.scalar(select(LocalSessionModel).order_by(LocalSessionModel.created_at.desc()))
        assert stored is not None
        stored.last_seen_at = now_utc() - timedelta(minutes=31)
    with pytest.raises(HTTPException) as expired:
        local_session.authenticate_local_request(request_for(token), settings)
    assert expired.value.status_code == 401

    token = login(session_factory, settings)
    with session_factory.begin() as session:
        stored = session.scalar(
            select(LocalSessionModel).where(
                LocalSessionModel.token_digest == local_session._digest(
                    settings.session_secret, "session", token
                )
            )
        )
        assert stored is not None
        stored.expires_at = now_utc() - timedelta(seconds=1)
    with pytest.raises(HTTPException) as absolute_expired:
        local_session.authenticate_local_request(request_for(token), settings)
    assert absolute_expired.value.status_code == 401

    token = login(session_factory, settings)
    with session_factory.begin() as session:
        account = session.get(LocalAccountModel, "account-test")
        assert account is not None
        account.status = "disabled"
    with pytest.raises(HTTPException) as disabled:
        local_session.authenticate_local_request(request_for(token), settings)
    assert disabled.value.status_code == 401


def test_login_throttling_and_unknown_account_are_fail_closed(identity_store) -> None:  # type: ignore[no-untyped-def]
    session_factory, settings = identity_store
    with session_factory() as session:
        assert local_session.login_local_account(
            session, settings, "real.user", "wrong-password", "127.0.0.1"
        ) == ""
    for _ in range(5):
        with session_factory() as session:
            assert local_session.login_local_account(
                session, settings, "unknown.user", "wrong-password", "127.0.0.1"
            ) == ""
    with session_factory() as session, pytest.raises(HTTPException) as blocked:
        local_session.login_local_account(
            session, settings, "unknown.user", "wrong-password", "127.0.0.1"
        )
    assert blocked.value.status_code == 429


def test_session_storage_fault_is_not_treated_as_login(identity_store, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    session_factory, settings = identity_store
    token = login(session_factory, settings)

    def unavailable():  # type: ignore[no-untyped-def]
        raise SQLAlchemyError("storage unavailable")

    monkeypatch.setattr(local_session, "SessionLocal", unavailable)
    with pytest.raises(HTTPException) as error:
        local_session.authenticate_local_request(request_for(token), settings)
    assert error.value.status_code == 503
