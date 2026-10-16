"""Fail-closed startup checks for shared and production installations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.dify_runtime import MountedFileSecretResolver, configured_secret_resolver
from app.models import LocalAccountModel, WorkerHeartbeatModel
from app.repository import now_utc


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def verify_deployed_startup(settings: Settings, session: Session) -> None:
    """Reject traffic startup until migrated identity storage and separate workers exist.

    A heartbeat proves only recent worker activity, not production fitness. Readiness
    remains separately gated while the authorization rollout is unfinished.
    """
    if settings.environment not in {"shared_test", "production"}:
        return
    settings.validate_production_boundaries()
    if not isinstance(configured_secret_resolver(settings), MountedFileSecretResolver):
        raise TypeError("Deployed external Secret resolver is unavailable.")

    cutoff = now_utc() - timedelta(seconds=settings.worker_heartbeat_max_age_seconds)
    try:
        # The first administrator may not exist yet; the migrated table must exist.
        session.execute(select(LocalAccountModel.id).limit(1))
        heartbeat_rows = session.execute(
            select(
                WorkerHeartbeatModel.worker_id,
                WorkerHeartbeatModel.state,
                WorkerHeartbeatModel.heartbeat_at,
            ).where(WorkerHeartbeatModel.heartbeat_at >= cutoff)
        ).all()
    except SQLAlchemyError as error:
        raise RuntimeError("Deployed PostgreSQL or identity schema is unavailable.") from error

    evaluation_workers = {
        worker_id for worker_id, state, heartbeat_at in heartbeat_rows
        if not state.startswith("candidate:") and _aware(heartbeat_at) >= cutoff
    }
    candidate_workers = {
        worker_id for worker_id, state, heartbeat_at in heartbeat_rows
        if state.startswith("candidate:") and _aware(heartbeat_at) >= cutoff
    }
    if not evaluation_workers or not candidate_workers:
        raise RuntimeError("Separate evaluation and candidate workers require recent heartbeats.")
