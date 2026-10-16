from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.local_identity import BootstrapAdminRequest, bootstrap_admin, issue_bootstrap_token


def isolated_postgres_url() -> str:
    url = os.environ["AUTOPE_TEST_POSTGRES_URL"]
    parsed = make_url(url)
    if not parsed.drivername.startswith("postgresql"):
        pytest.fail("AUTOPE_TEST_POSTGRES_URL must point to PostgreSQL")
    if parsed.host not in {"127.0.0.1", "localhost"} or parsed.database != "autope_auth_test":
        pytest.fail("Auth storage tests require the dedicated localhost autope_auth_test database")
    return url


@pytest.mark.skipif(
    not os.getenv("AUTOPE_TEST_POSTGRES_URL"), reason="isolated PostgreSQL test URL not provided"
)
def test_concurrent_local_login_name_is_unique() -> None:
    """Run only against a throwaway PostgreSQL database migrated to head."""
    url = isolated_postgres_url()
    engine = create_engine(url, pool_pre_ping=True)
    login_name = f"concurrency-{uuid4().hex}"
    account_ids = [f"account-{uuid4().hex}" for _ in range(2)]
    barrier = Barrier(2)

    def attempt(account_id: str) -> bool:
        try:
            with engine.begin() as connection:
                barrier.wait(timeout=5)
                connection.execute(
                    text(
                        "INSERT INTO local_accounts "
                        "(id, login_name, display_name, password_hash, status, "
                        "is_platform_admin, created_at, updated_at) "
                        "VALUES (:id, :login_name, 'fixture', NULL, 'pending', false, "
                        ":created_at, :updated_at)"
                    ),
                    {
                        "id": account_id,
                        "login_name": login_name,
                        "created_at": datetime.now(UTC),
                        "updated_at": datetime.now(UTC),
                    },
                )
            return True
        except IntegrityError:
            return False

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, account_ids))
        assert sorted(outcomes) == [False, True]
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT count(*) FROM local_accounts WHERE login_name=:login_name"),
                {"login_name": login_name},
            ) == 1
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM local_accounts WHERE id IN (:first, :second)"),
                {"first": account_ids[0], "second": account_ids[1]},
            )
        engine.dispose()


@pytest.mark.skipif(
    not os.getenv("AUTOPE_TEST_POSTGRES_URL"), reason="isolated PostgreSQL test URL not provided"
)
def test_concurrent_bootstrap_creates_only_one_admin() -> None:
    url = isolated_postgres_url()
    engine = create_engine(url, pool_pre_ping=True)
    with Session(engine) as session:
        token = issue_bootstrap_token(session)
    payload = BootstrapAdminRequest(
        bootstrap_token=token,
        login_name=f"bootstrap-{uuid4().hex[:12]}",
        display_name="Bootstrap Test",
        password="isolated-test-password-123",
    )
    token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    barrier = Barrier(2)

    def attempt() -> int:
        with Session(engine) as session:
            barrier.wait(timeout=5)
            try:
                bootstrap_admin(session, payload, f"test-{uuid4().hex}")
                return 201
            except HTTPException as error:
                return error.status_code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: attempt(), range(2)))
        assert sorted(outcomes) == [201, 409]
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM local_accounts")) == 1
            assert connection.scalar(text("SELECT count(*) FROM local_role_grants")) == 1
    finally:
        with engine.begin() as connection:
            account_id = connection.scalar(
                text("SELECT id FROM local_accounts WHERE login_name=:login_name"),
                {"login_name": payload.login_name},
            )
            if account_id is not None:
                connection.execute(
                    text("DELETE FROM local_role_grants WHERE account_id=:account_id"),
                    {"account_id": account_id},
                )
                connection.execute(
                    text("DELETE FROM audit_events WHERE action='identity.bootstrap_completed' "
                         "AND resource_id=:account_id"),
                    {"account_id": account_id},
                )
                connection.execute(
                    text("DELETE FROM local_accounts WHERE id=:account_id"),
                    {"account_id": account_id},
                )
            connection.execute(
                text("DELETE FROM local_one_time_credentials WHERE token_digest=:token_digest"),
                {"token_digest": token_digest},
            )
        engine.dispose()
