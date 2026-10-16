"""Persistent worker for explicitly authorized DSPy candidate generation."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from datetime import timedelta
from threading import Event, Thread

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.approval_evidence import record_contribution
from app.config import get_settings
from app.db import SessionLocal
from app.dspy_candidate import generate_candidate
from app.models import (
    CandidateGenerationJobModel,
    DatasetModel,
    DifyTargetModel,
    LocalAccountModel,
    WorkerHeartbeatModel,
)
from app.repository import audit, new_id, now_utc

logger = logging.getLogger(__name__)


class CandidateGenerationWorker:
    def __init__(self, *, worker_id: str | None = None, session_factory: Callable[[], Session] = SessionLocal) -> None:
        self.worker_id = worker_id or f"candidate-worker-{os.getpid()}"
        self.session_factory = session_factory

    def heartbeat(self, state: str) -> None:
        with self.session_factory() as session:
            heartbeat = session.get(WorkerHeartbeatModel, self.worker_id)
            if heartbeat is None:
                heartbeat = WorkerHeartbeatModel(worker_id=self.worker_id, state=f"candidate:{state}", heartbeat_at=now_utc())
                session.add(heartbeat)
            else:
                heartbeat.state = f"candidate:{state}"
                heartbeat.heartbeat_at = now_utc()
            session.commit()

    def claim_next_job(self) -> str | None:
        now = now_utc()
        claimable = or_(
            CandidateGenerationJobModel.status == "queued",
            and_(CandidateGenerationJobModel.status == "running", CandidateGenerationJobModel.lease_expires_at < now),
        )
        with self.session_factory() as session:
            ids = list(session.scalars(select(CandidateGenerationJobModel.id).where(claimable).order_by(CandidateGenerationJobModel.created_at).limit(10)))
            for job_id in ids:
                changed = session.execute(
                    update(CandidateGenerationJobModel)
                    .where(CandidateGenerationJobModel.id == job_id, claimable)
                    .values(status="running", stage="model_generating", started_at=now,
                            claimed_by=self.worker_id, lease_expires_at=now + timedelta(hours=2), error_message="")
                )
                if changed.rowcount:
                    session.commit()
                    return job_id
            session.rollback()
        return None

    def process_once(self) -> bool:
        self.heartbeat("polling")
        job_id = self.claim_next_job()
        if job_id is None:
            self.heartbeat("idle")
            return False
        self.heartbeat("running")
        stop_pulse = Event()
        pulse = Thread(target=self._pulse_while_running, args=(stop_pulse, job_id), daemon=True)
        pulse.start()
        try:
            with self.session_factory() as session:
                job = session.get(CandidateGenerationJobModel, job_id)
                if job is None:
                    raise RuntimeError("Generation job disappeared.")
                if job.consented_model != os.getenv("AUTOPE_DSPY_MODEL", "").strip() or job.consented_endpoint != os.getenv("AUTOPE_DSPY_BASE_URL", "").strip():
                    raise RuntimeError("DSPy model or endpoint changed after consent; submit a new generation request.")
                dataset = session.get(DatasetModel, job.dataset_id)
                baseline = session.get(DifyTargetModel, job.baseline_target_id)
                if dataset is None or baseline is None or not dataset.is_frozen or dataset.review_status != "approved":
                    raise RuntimeError("Approved frozen dataset or baseline target is unavailable.")
                generated = generate_candidate(session, dataset=dataset, baseline=baseline)
                job = session.get(CandidateGenerationJobModel, job_id)
                if job is None or job.claimed_by != self.worker_id or job.status != "running":
                    raise RuntimeError("Generation job ownership was lost.")
                if not get_settings().auth_disabled and session.get(LocalAccountModel, job.created_by):
                    record_contribution(
                        session, resource_type="candidate", resource_id=generated.candidate.id,
                        version_key=generated.candidate.prompt_sha256,
                        actor_id=job.created_by, action="generation_requested", verified=True,
                    )
                job.candidate_id = generated.candidate.id
                job.status = "completed"
                job.stage = "completed"
                job.completed_at = now_utc()
                job.claimed_by = None
                job.lease_expires_at = None
                audit(session, actor_id=f"worker:{self.worker_id}", action="candidate.generation_completed", resource_type="candidate_generation_job", resource_id=job_id, request_id=new_id("worker_request"), detail={"candidate_id": generated.candidate.id}, identity_source="service", object_version=generated.candidate.prompt_sha256)
                session.commit()
        except Exception as error:
            logger.exception("Candidate generation failed for job %s", job_id)
            with self.session_factory() as session:
                job = session.get(CandidateGenerationJobModel, job_id)
                if job is not None and job.claimed_by == self.worker_id and job.status == "running":
                    job.status = "failed"
                    job.stage = "failed"
                    job.error_message = f"{type(error).__name__}: candidate generation failed; inspect worker logs."
                    job.completed_at = now_utc()
                    job.claimed_by = None
                    job.lease_expires_at = None
                    session.commit()
        finally:
            stop_pulse.set()
            pulse.join(timeout=1)
            self.heartbeat("idle")
        return True

    def _pulse_while_running(self, stop: Event, job_id: str) -> None:
        while not stop.wait(15):
            try:
                self.heartbeat("running")
                with self.session_factory() as session:
                    session.execute(update(CandidateGenerationJobModel).where(
                        CandidateGenerationJobModel.id == job_id,
                        CandidateGenerationJobModel.status == "running",
                        CandidateGenerationJobModel.claimed_by == self.worker_id,
                    ).values(lease_expires_at=now_utc() + timedelta(hours=2)))
                    session.commit()
            except Exception:
                logger.exception("Candidate worker heartbeat failed")


def main() -> None:
    logging.basicConfig(level=os.getenv("AUTOPE_LOG_LEVEL", "INFO"))
    worker = CandidateGenerationWorker()
    poll_seconds = max(1.0, float(os.getenv("AUTOPE_WORKER_POLL_SECONDS", "2")))
    while True:
        worker.process_once()
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
