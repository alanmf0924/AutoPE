from __future__ import annotations

import io
import sys
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import local_identity
from app.local_identity import (
    BootstrapAdminRequest,
    bootstrap_admin,
    is_initialized,
    issue_bootstrap_token,
)
from app.models import (
    AuditEventModel,
    Base,
    LocalAccountModel,
    LocalOneTimeCredentialModel,
    LocalRoleGrantModel,
)
from app.repository import now_utc


@pytest.fixture
def session():  # type: ignore[no-untyped-def]
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as database_session:
        yield database_session
    engine.dispose()


def _payload(token: str) -> BootstrapAdminRequest:
    return BootstrapAdminRequest(
        bootstrap_token=token,
        login_name=" First.Admin ",
        display_name=" First Admin ",
        password="safe-test-password-123",
    )


def test_initial_setup_uses_one_time_token_without_default_admin(session: Session) -> None:
    assert not is_initialized(session)
    session.rollback()
    token = issue_bootstrap_token(session)
    stored = session.scalar(select(LocalOneTimeCredentialModel))
    assert stored is not None
    assert token not in stored.token_digest
    session.rollback()

    account_id = bootstrap_admin(session, _payload(token), "test-request")
    account = session.get(LocalAccountModel, account_id)
    assert account is not None
    assert account.login_name == "first.admin"
    assert account.display_name == "First Admin"
    assert account.password_hash is not None
    assert "safe-test-password-123" not in account.password_hash
    assert account.is_platform_admin is True
    assert session.scalar(select(LocalRoleGrantModel.role)) == "operator"
    event = session.scalar(select(AuditEventModel).where(AuditEventModel.action == "identity.bootstrap_completed"))
    assert event is not None
    assert event.actor_id == account_id
    assert "safe-test-password-123" not in str(event.detail)
    session.rollback()

    with pytest.raises(HTTPException) as error:
        bootstrap_admin(session, _payload(token), "repeat-request")
    assert error.value.status_code == 409
    with pytest.raises(ValueError, match="already been initialized"):
        issue_bootstrap_token(session)


def test_new_token_revokes_prior_token_and_invalid_token_is_rejected(session: Session) -> None:
    previous = issue_bootstrap_token(session)
    current = issue_bootstrap_token(session)
    assert previous != current
    for invalid in (previous, "x" * 40):
        with pytest.raises(HTTPException) as error:
            bootstrap_admin(session, _payload(invalid), "test-request")
        assert error.value.status_code == 403
    assert not is_initialized(session)


def test_expired_token_cannot_create_admin(session: Session) -> None:
    token = issue_bootstrap_token(session)
    session.execute(
        LocalOneTimeCredentialModel.__table__.update().values(
            expires_at=now_utc() - timedelta(seconds=1)
        )
    )
    session.commit()
    with pytest.raises(HTTPException) as error:
        bootstrap_admin(session, _payload(token), "test-request")
    assert error.value.status_code == 403
    assert session.scalar(select(LocalAccountModel.id)) is None


def test_bootstrap_cli_requires_interactive_terminal(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    class TerminalBuffer(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(local_identity, "SessionLocal", sessionmaker(bind=session.bind))
    monkeypatch.setattr(sys, "argv", ["app.local_identity", "bootstrap-token"])
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    with pytest.raises(SystemExit, match="Run interactively"):
        local_identity.main()

    output = TerminalBuffer()
    monkeypatch.setattr(sys, "stdin", TerminalBuffer())
    monkeypatch.setattr(sys, "stdout", output)
    local_identity.main()
    token = output.getvalue().splitlines()[-1]
    assert len(token) >= 32
    session.rollback()
    assert session.scalar(select(LocalOneTimeCredentialModel.token_digest)) is not None
