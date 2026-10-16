from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.account_management import (
    CreateAccountRequest,
    UpdateAccountRequest,
    create_account,
    list_accounts,
    update_account,
)
from app.local_identity import _hash_password, _token_digest
from app.local_recovery import ResetPasswordRequest, reset_password
from app.models import (
    AuditEventModel,
    Base,
    LocalAccountModel,
    LocalOneTimeCredentialModel,
    LocalRoleGrantModel,
    LocalSessionModel,
)
from app.repository import now_utc


@pytest.fixture
def account_store():  # type: ignore[no-untyped-def]
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    now = now_utc()
    with factory.begin() as session:
        session.add(LocalAccountModel(
            id="admin-test", login_name="admin.test", display_name="Admin",
            password_hash=_hash_password("original-password"), status="active",
            is_platform_admin=True, created_at=now, updated_at=now,
        ))
    yield factory
    engine.dispose()


def test_admin_creates_invited_account_and_assigns_roles(account_store) -> None:  # type: ignore[no-untyped-def]
    factory: sessionmaker[Session] = account_store
    with factory() as session:
        created = create_account(
            session, "admin-test",
            CreateAccountRequest(login_name="new.user", display_name="New User", roles=["annotator"]),
            "request-test",
        )
    account_id = str(created["account_id"])
    token = str(created["invite_token"])
    with factory() as session:
        account = session.get(LocalAccountModel, account_id)
        credential = session.scalar(select(LocalOneTimeCredentialModel).where(
            LocalOneTimeCredentialModel.token_digest == _token_digest(token)
        ))
        assert account is not None and credential is not None
        assert account.password_hash is None
        assert credential.purpose == "password_reset"
        assert list_accounts(session, "admin-test")[1]["roles"] == ["annotator"]
    with factory() as session:
        reset_password(
            session,
            ResetPasswordRequest(reset_token=token, new_password="invited-password-123"),
            "request-test",
        )
    with factory() as session:
        updated = update_account(
            session, "admin-test", account_id,
            UpdateAccountRequest(roles=["auditor"], status="disabled"), "request-test",
        )
    assert updated["roles"] == ["auditor"]
    assert updated["status"] == "disabled"
    with factory() as session:
        roles = session.scalars(select(LocalRoleGrantModel.role).where(
            LocalRoleGrantModel.account_id == account_id
        )).all()
        assert roles == ["auditor"]
        events = session.scalars(select(AuditEventModel).where(
            AuditEventModel.resource_id == account_id
        )).all()
        assert all(token not in str(event.detail) for event in events)


def test_non_admin_cannot_create_or_manage_accounts(account_store) -> None:  # type: ignore[no-untyped-def]
    factory: sessionmaker[Session] = account_store
    with factory() as session:
        created = create_account(
            session, "admin-test",
            CreateAccountRequest(login_name="not.admin", display_name="Not Admin"),
            "request-test",
        )
    actor_id = str(created["account_id"])
    with factory() as session, pytest.raises(HTTPException) as denied:
        create_account(
            session, actor_id,
            CreateAccountRequest(login_name="another.user", display_name="Another"),
            "request-test",
        )
    assert denied.value.status_code == 403
    with factory() as session, pytest.raises(HTTPException) as denied_list:
        list_accounts(session, actor_id)
    assert denied_list.value.status_code == 403


def test_disable_revokes_session_and_last_admin_is_protected(account_store) -> None:  # type: ignore[no-untyped-def]
    factory: sessionmaker[Session] = account_store
    with factory() as session:
        created = create_account(
            session, "admin-test",
            CreateAccountRequest(login_name="active.user", display_name="Active User"),
            "request-test",
        )
    account_id = str(created["account_id"])
    now = now_utc()
    with factory.begin() as session:
        session.add(LocalSessionModel(
            id="user-session", account_id=account_id, token_digest="d" * 64,
            csrf_digest="c" * 64, created_at=now, last_seen_at=now,
            expires_at=now + timedelta(hours=1), revoked_at=None,
        ))
    with factory() as session:
        update_account(
            session, "admin-test", account_id,
            UpdateAccountRequest(status="disabled"), "request-test",
        )
    with factory() as session:
        stored = session.get(LocalSessionModel, "user-session")
        assert stored is not None and stored.revoked_at is not None
    with factory() as session, pytest.raises(HTTPException) as self_disable:
        update_account(
            session, "admin-test", "admin-test",
            UpdateAccountRequest(status="disabled"), "request-test",
        )
    assert self_disable.value.status_code == 409


def test_invalid_roles_and_duplicate_login_are_rejected(account_store) -> None:  # type: ignore[no-untyped-def]
    factory: sessionmaker[Session] = account_store
    with pytest.raises(ValueError):
        CreateAccountRequest(login_name="bad.role", display_name="Bad", roles=["platform_admin"])
    with factory() as session, pytest.raises(HTTPException) as duplicate:
        create_account(
            session, "admin-test",
            CreateAccountRequest(login_name="admin.test", display_name="Duplicate"),
            "request-test",
        )
    assert duplicate.value.status_code == 409
