from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.models import Base


def _engine_options(database_url: str) -> dict[str, object]:
    if database_url == "sqlite+pysqlite:///:memory:":
        return {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}
    if database_url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True, "pool_recycle": 1800}


settings = get_settings()
engine = create_engine(settings.database_url, future=True, **_engine_options(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def initialize_database() -> None:
    """Development/test convenience only; production must apply Alembic migrations."""
    if settings.environment in {"development", "test"}:
        Base.metadata.create_all(bind=engine)
