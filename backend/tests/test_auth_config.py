from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import security
from app.config import Settings


@pytest.fixture
def deployed_settings(tmp_path):  # type: ignore[no-untyped-def]
    return Settings(
        environment="production",
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


@pytest.mark.parametrize("environment", ["shared_test", "production"])
def test_deployed_config_accepts_local_session_secret_without_legacy_jwt(
    deployed_settings: Settings, environment: str
) -> None:
    replace(deployed_settings, environment=environment).validate_production_boundaries()


@pytest.mark.parametrize(
    ("override", "error"),
    [
        ({"database_url": "sqlite+pysqlite:///./autope.db"}, "PostgreSQL"),
        ({"auth_disabled": True}, "AUTOPE_AUTH_DISABLED"),
        ({"session_secret": ""}, "AUTOPE_SESSION_SECRET"),
        ({"seed_demo_data": True}, "AUTOPE_SEED_DEMO_DATA"),
        ({"secret_resolver": "disabled"}, "AUTOPE_SECRET_RESOLVER"),
        ({"external_secret_dir": "/missing/autope-secret-dir"}, "AUTOPE_EXTERNAL_SECRET_DIR"),
        ({"allowed_origins": "http://autope.example.test"}, "AUTOPE_ALLOWED_ORIGINS"),
        ({"allowed_origins": "https://autope.example.test,https://other.example.test"}, "AUTOPE_ALLOWED_ORIGINS"),
        ({"allowed_origins": "https://autope.example.test/path"}, "AUTOPE_ALLOWED_ORIGINS"),
        ({"require_worker_heartbeat": False}, "AUTOPE_REQUIRE_WORKER_HEARTBEAT"),
    ],
)
def test_deployed_config_rejects_missing_boundary(
    deployed_settings: Settings, override: dict[str, object], error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        replace(deployed_settings, **override).validate_production_boundaries()


def test_legacy_bearer_auth_fails_closed_in_deployed_environment(
    monkeypatch: pytest.MonkeyPatch, deployed_settings: Settings
) -> None:
    monkeypatch.setattr(security, "get_settings", lambda: deployed_settings)
    request = Request({
        "type": "http", "method": "GET", "path": "/api/session",
        "headers": [(b"authorization", b"Bearer old-jwt")],
    })
    with pytest.raises(HTTPException) as error:
        security.current_actor(request)
    assert error.value.status_code == 401
