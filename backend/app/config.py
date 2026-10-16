from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlparse

from dotenv import load_dotenv


def load_local_environment() -> None:
    """Load the local developer configuration without replacing deployed settings.

    Workers and one-off candidate-generation commands are not started through a shell
    that sources ``.env.local``.  Loading it here makes those entrypoints consistent
    with the API process, while explicit process environment remains authoritative.
    Production intentionally relies on injected/mounted configuration only.
    """

    if os.getenv("AUTOPE_ENVIRONMENT", "").strip().lower() in {"shared_test", "production"}:
        return
    load_dotenv(Path(__file__).resolve().parents[1] / ".env.local", override=False)


load_local_environment()


def _environment_value(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _boolean_value(name: str, default: bool) -> bool:
    value = _environment_value(name, "true" if default else "false").lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false.")
    return value == "true"


@dataclass(frozen=True)
class Settings:
    """Runtime configuration. Production defaults fail closed by validation below."""

    environment: Literal["development", "test", "shared_test", "production"]
    database_url: str
    allowed_origins: str
    auth_disabled: bool
    seed_demo_data: bool
    jwt_hs256_secret: str
    secret_resolver: Literal["disabled", "external"]
    external_secret_dir: str
    worker_lease_seconds: int
    worker_heartbeat_max_age_seconds: int
    require_worker_heartbeat: bool
    session_secret: str = ""

    @classmethod
    def load(cls) -> Settings:
        environment = _environment_value("AUTOPE_ENVIRONMENT", "development")
        resolver = _environment_value("AUTOPE_SECRET_RESOLVER", "disabled")
        if environment not in {"development", "test", "shared_test", "production"}:
            raise ValueError(
                "AUTOPE_ENVIRONMENT must be development, test, shared_test, or production."
            )
        if resolver not in {"disabled", "external"}:
            raise ValueError("AUTOPE_SECRET_RESOLVER must be disabled or external.")
        settings = cls(
            environment=cast(
                Literal["development", "test", "shared_test", "production"], environment
            ),
            database_url=_environment_value(
                "AUTOPE_DATABASE_URL", "sqlite+pysqlite:///./autope.db"
            ),
            allowed_origins=_environment_value(
                "AUTOPE_ALLOWED_ORIGINS", "http://127.0.0.1:5174,http://localhost:5174"
            ),
            auth_disabled=_boolean_value("AUTOPE_AUTH_DISABLED", True),
            seed_demo_data=_boolean_value("AUTOPE_SEED_DEMO_DATA", False),
            jwt_hs256_secret=_environment_value("AUTOPE_JWT_HS256_SECRET", ""),
            secret_resolver=cast(Literal["disabled", "external"], resolver),
            external_secret_dir=_environment_value("AUTOPE_EXTERNAL_SECRET_DIR", ""),
            worker_lease_seconds=int(_environment_value("AUTOPE_WORKER_LEASE_SECONDS", "180")),
            worker_heartbeat_max_age_seconds=int(
                _environment_value("AUTOPE_WORKER_HEARTBEAT_MAX_AGE_SECONDS", "90")
            ),
            require_worker_heartbeat=_boolean_value(
                "AUTOPE_REQUIRE_WORKER_HEARTBEAT",
                environment in {"shared_test", "production"},
            ),
            session_secret=_environment_value("AUTOPE_SESSION_SECRET", ""),
        )
        settings.validate_production_boundaries()
        return settings

    def validate_production_boundaries(self) -> None:
        if self.environment not in {"shared_test", "production"}:
            return
        if not self.database_url.startswith(("postgresql://", "postgresql+")):
            raise ValueError("AUTOPE_DATABASE_URL must point to PostgreSQL in deployed environments.")
        if self.auth_disabled:
            raise ValueError("AUTOPE_AUTH_DISABLED must be false in deployed environments.")
        if len(self.session_secret) < 32:
            raise ValueError(
                "AUTOPE_SESSION_SECRET must contain at least 32 characters in deployed environments."
            )
        if self.seed_demo_data:
            raise ValueError("AUTOPE_SEED_DEMO_DATA must be false in deployed environments.")
        if self.secret_resolver != "external":
            raise ValueError("AUTOPE_SECRET_RESOLVER=external is required in deployed environments.")
        if not Path(self.external_secret_dir).is_absolute():
            raise ValueError("AUTOPE_EXTERNAL_SECRET_DIR must be an absolute mounted secret directory.")
        if not Path(self.external_secret_dir).is_dir():
            raise ValueError("AUTOPE_EXTERNAL_SECRET_DIR must exist as a mounted directory.")
        if not self.allowed_origins:
            raise ValueError("AUTOPE_ALLOWED_ORIGINS is required in deployed environments.")
        if len(self.cors_origins) != 1:
            raise ValueError("AUTOPE_ALLOWED_ORIGINS must contain one same-origin HTTPS entry.")
        origin = urlparse(self.cors_origins[0])
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.username is not None
            or origin.password is not None
            or origin.path
            or origin.params
            or origin.query
            or origin.fragment
        ):
            raise ValueError("AUTOPE_ALLOWED_ORIGINS must contain one same-origin HTTPS entry.")
        if not self.require_worker_heartbeat:
            raise ValueError("AUTOPE_REQUIRE_WORKER_HEARTBEAT must be true in deployed environments.")
        if self.worker_lease_seconds < 30:
            raise ValueError("AUTOPE_WORKER_LEASE_SECONDS must be at least 30 seconds.")
        if self.worker_heartbeat_max_age_seconds < 30:
            raise ValueError("AUTOPE_WORKER_HEARTBEAT_MAX_AGE_SECONDS must be at least 30 seconds.")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings.load()
