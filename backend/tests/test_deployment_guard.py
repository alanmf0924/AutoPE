from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app import credential_vault, local_secrets, main
from app.config import Settings
from app.deployment_guard import verify_deployed_startup
from app.dify_runtime import MountedFileSecretResolver, configured_secret_resolver
from app.models import Base, DifyTargetModel, WorkerHeartbeatModel
from app.repository import now_utc
from app.schemas import DifyTargetCreate
from app.security import Actor


@pytest.fixture
def deployed_settings(tmp_path):  # type: ignore[no-untyped-def]
    return Settings(
        environment="shared_test",
        database_url="postgresql+psycopg://autope:example@db/autope",
        allowed_origins="https://autope.example.test",
        auth_disabled=False,
        seed_demo_data=False,
        jwt_hs256_secret="",
        secret_resolver="external",
        external_secret_dir=str(tmp_path),
        worker_lease_seconds=180,
        worker_heartbeat_max_age_seconds=90,
        require_worker_heartbeat=True,
        session_secret="s" * 32,
    )


@pytest.fixture
def isolated_session():  # type: ignore[no-untyped-def]
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.mark.parametrize("environment", ["shared_test", "production"])
def test_deployed_startup_requires_both_recent_worker_heartbeats(
    deployed_settings: Settings, isolated_session: Session, environment: str,
) -> None:
    settings = replace(deployed_settings, environment=environment)
    assert isinstance(configured_secret_resolver(settings), MountedFileSecretResolver)
    with pytest.raises(RuntimeError, match="recent heartbeats"):
        verify_deployed_startup(settings, isolated_session)

    now = now_utc()
    isolated_session.add(WorkerHeartbeatModel(
        worker_id="evaluation-test", state="idle", heartbeat_at=now,
    ))
    isolated_session.commit()
    with pytest.raises(RuntimeError, match="recent heartbeats"):
        verify_deployed_startup(settings, isolated_session)

    isolated_session.add(WorkerHeartbeatModel(
        worker_id="candidate-test", state="candidate:idle", heartbeat_at=now,
    ))
    isolated_session.commit()
    verify_deployed_startup(settings, isolated_session)

    evaluation = isolated_session.get(WorkerHeartbeatModel, "evaluation-test")
    assert evaluation is not None
    evaluation.heartbeat_at = now - timedelta(minutes=3)
    isolated_session.commit()
    with pytest.raises(RuntimeError, match="recent heartbeats"):
        verify_deployed_startup(settings, isolated_session)


def test_deployed_startup_rejects_missing_migration(
    deployed_settings: Settings,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with Session(engine) as session, pytest.raises(RuntimeError, match="identity schema"):
        verify_deployed_startup(deployed_settings, session)
    engine.dispose()


def test_shared_test_does_not_fall_back_to_local_secrets(
    deployed_settings: Settings, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(local_secrets, "get_settings", lambda: deployed_settings)
    monkeypatch.setattr(credential_vault, "get_settings", lambda: deployed_settings)
    monkeypatch.setenv("AUTOPE_DEV_SECRET_SAMPLE", "test-only-secret")
    assert local_secrets.LocalEnvironmentSecretResolver().resolve("sample") == ""
    with pytest.raises(credential_vault.CredentialVaultError, match="deployed"):
        credential_vault.encrypt_local_api_key("test-only-key")


def test_local_development_does_not_require_deployed_worker_checks(
    deployed_settings: Settings, isolated_session: Session,
) -> None:
    verify_deployed_startup(
        replace(deployed_settings, environment="development", secret_resolver="disabled"),
        isolated_session,
    )


def test_api_startup_rejects_missing_independent_workers(
    deployed_settings: Settings, monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(main, "settings", deployed_settings)
    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=engine))
    with pytest.raises(RuntimeError, match="recent heartbeats"), TestClient(main.app):
        pass
    engine.dispose()


def test_shared_test_rejects_inline_target_secret_without_writing(
    deployed_settings: Settings, isolated_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "settings", deployed_settings)
    payload = DifyTargetCreate(
        name="Local Target", base_url="http://127.0.0.1:3000",
        api_key="test-only-key", version_fingerprint="version-test",
    )
    request = Request({
        "type": "http", "method": "POST", "path": "/api/targets", "headers": [],
    })
    with pytest.raises(HTTPException) as denied:
        main.create_target(
            payload, request, isolated_session,
            Actor(subject="operator-test", roles=frozenset({"operator"})),
        )
    assert denied.value.status_code == 400
    assert isolated_session.scalar(select(DifyTargetModel.id)) is None
