from __future__ import annotations

import io
import sys
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import local_recovery
from app.local_identity import _hash_password, _token_digest, verify_password
from app.local_recovery import (
    ChangePasswordRequest,
    ResetPasswordRequest,
    change_password,
    issue_recovery_token,
    reset_password,
)
from app.models import (
    AuditEventModel,
    Base,
    LocalAccountModel,
    LocalOneTimeCredentialModel,
    LocalSessionModel,
)
from app.repository import now_utc


@pytest.fixture
def recovery_store():  # type: ignore[no-untyped-def]
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    now = now_utc()
    with factory.begin() as session:
        session.add_all([
            LocalAccountModel(
                id="admin-test", login_name="admin.test", display_name="Admin",
                password_hash=_hash_password("original-password"), status="active",
                is_platform_admin=True, created_at=now, updated_at=now,
            ),
            LocalAccountModel(
                id="user-test", login_name="user.test", display_name="User",
                password_hash=_hash_password("original-password"), status="active",
                is_platform_admin=False, created_at=now, updated_at=now,
            ),
            LocalSessionModel(
                id="session-test", account_id="user-test", token_digest="d" * 64,
                csrf_digest="c" * 64, created_at=now, last_seen_at=now,
                expires_at=now + timedelta(hours=1), revoked_at=None,
            ),
        ])
    yield factory
    engine.dispose()


def test_change_password_requires_old_password_and_revokes_sessions(recovery_store) -> None:  # type: ignore[no-untyped-def]
    factory: sessionmaker[Session] = recovery_store
    with factory() as session, pytest.raises(HTTPException) as denied:
        change_password(
            session, "user-test",
            ChangePasswordRequest(current_password="wrong-password", new_password="new-password-123"),
            "request-test",
        )
    assert denied.value.status_code == 403
    with factory() as session:
        change_password(
            session, "user-test",
            ChangePasswordRequest(
                current_password="original-password", new_password="new-password-123"
            ),
            "request-test",
        )
    with factory() as session:
        account = session.get(LocalAccountModel, "user-test")
        stored_session = session.get(LocalSessionModel, "session-test")
        assert account is not None and stored_session is not None
        assert verify_password("new-password-123", account.password_hash or "")
        assert not verify_password("original-password", account.password_hash or "")
        assert stored_session.revoked_at is not None


def test_admin_reset_is_one_time_and_audited_without_token_leak(recovery_store) -> None:  # type: ignore[no-untyped-def]
    factory: sessionmaker[Session] = recovery_store
    with factory() as session:
        token = issue_recovery_token(
            session, "user-test", "admin-test", "request-test", server_issued=False
        )
    with factory() as session:
        reset_password(
            session,
            ResetPasswordRequest(reset_token=token, new_password="reset-password-123"),
            "request-test",
        )
    with factory() as session:
        account = session.get(LocalAccountModel, "user-test")
        stored_session = session.get(LocalSessionModel, "session-test")
        credential = session.scalar(
            select(LocalOneTimeCredentialModel).where(
                LocalOneTimeCredentialModel.token_digest == _token_digest(token)
            )
        )
        events = session.scalars(select(AuditEventModel)).all()
        assert account is not None and stored_session is not None and credential is not None
        assert verify_password("reset-password-123", account.password_hash or "")
        assert stored_session.revoked_at is not None
        assert credential.consumed_at is not None
        assert all(token not in str(event.detail) for event in events)
        assert {event.action for event in events} == {
            "identity.recovery_issued", "identity.password_recovered"
        }
    with factory() as session, pytest.raises(HTTPException) as reused:
        reset_password(
            session,
            ResetPasswordRequest(reset_token=token, new_password="another-password-123"),
            "request-test",
        )
    assert reused.value.status_code == 403


def test_expired_reset_and_unauthorized_issuer_are_rejected(recovery_store) -> None:  # type: ignore[no-untyped-def]
    factory: sessionmaker[Session] = recovery_store
    with factory() as session, pytest.raises(HTTPException) as unauthorized:
        issue_recovery_token(
            session, "admin-test", "user-test", "request-test", server_issued=False
        )
    assert unauthorized.value.status_code == 403
    with factory() as session:
        token = issue_recovery_token(
            session, "user-test", "admin-test", "request-test", server_issued=False
        )
    with factory.begin() as session:
        credential = session.scalar(
            select(LocalOneTimeCredentialModel).where(
                LocalOneTimeCredentialModel.token_digest == _token_digest(token)
            )
        )
        assert credential is not None
        credential.expires_at = now_utc() - timedelta(seconds=1)
    with factory() as session, pytest.raises(HTTPException) as expired:
        reset_password(
            session,
            ResetPasswordRequest(reset_token=token, new_password="another-password-123"),
            "request-test",
        )
    assert expired.value.status_code == 403


def test_server_recovery_only_targets_admin_and_invalidates_old_token(recovery_store) -> None:  # type: ignore[no-untyped-def]
    factory: sessionmaker[Session] = recovery_store
    with factory() as session, pytest.raises(HTTPException) as non_admin:
        issue_recovery_token(
            session, "user-test", "server_operator", "request-test", server_issued=True
        )
    assert non_admin.value.status_code == 403
    with factory() as session:
        first = issue_recovery_token(
            session, "admin-test", "server_operator", "request-test", server_issued=True
        )
    with factory() as session:
        second = issue_recovery_token(
            session, "admin-test", "server_operator", "request-test", server_issued=True
        )
    with factory() as session, pytest.raises(HTTPException) as invalidated:
        reset_password(
            session,
            ResetPasswordRequest(reset_token=first, new_password="recovered-password-123"),
            "request-test",
        )
    assert invalidated.value.status_code == 403
    with factory() as session:
        reset_password(
            session,
            ResetPasswordRequest(reset_token=second, new_password="recovered-password-123"),
            "request-test",
        )
    with factory() as session:
        account = session.get(LocalAccountModel, "admin-test")
        assert account is not None
        assert verify_password("recovered-password-123", account.password_hash or "")


def test_server_recovery_cli_requires_tty_and_audits_issuance(
    recovery_store, monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    factory: sessionmaker[Session] = recovery_store
    monkeypatch.setattr(local_recovery, "SessionLocal", factory)
    monkeypatch.setattr(sys, "argv", ["local_recovery", "recover-admin", "admin.test"])
    with pytest.raises(SystemExit, match="interactively"):
        local_recovery.main()

    class InteractiveBuffer(io.StringIO):
        def isatty(self) -> bool:
            return True

    output = InteractiveBuffer()
    monkeypatch.setattr(sys, "stdin", InteractiveBuffer())
    monkeypatch.setattr(sys, "stdout", output)
    local_recovery.main()
    token = output.getvalue().splitlines()[-1]
    with factory() as session:
        credential = session.scalar(select(LocalOneTimeCredentialModel).where(
            LocalOneTimeCredentialModel.token_digest == _token_digest(token)
        ))
        event = session.scalar(select(AuditEventModel).where(
            AuditEventModel.action == "identity.recovery_issued"
        ))
        assert credential is not None and event is not None
        assert credential.purpose == "server_recovery"
        assert event.detail["source"] == "server_operator"
        assert token not in str(event.detail)
