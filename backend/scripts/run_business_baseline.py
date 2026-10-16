from __future__ import annotations

import argparse
import json

from sqlalchemy import select

from app.db import SessionLocal
from app.evaluation_worker import EvaluationWorker
from app.models import DatasetModel, DifyTargetModel, EvaluationReportModel, EvaluationRunModel
from app.repository import audit, new_id, now_utc

BASELINE_TARGET_ID = "target_c82a5b06972c"


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute the frozen Dify business-contract baseline.")
    parser.add_argument(
        "--idempotency-key",
        default="business-contract-v2-baseline-20261013",
        help="Stable key for a single auditable run.",
    )
    args = parser.parse_args()
    with SessionLocal() as session:
        existing = session.scalar(
            select(EvaluationRunModel).where(EvaluationRunModel.idempotency_key == args.idempotency_key)
        )
        if existing is not None:
            run_id = existing.id
        else:
            dataset = session.scalar(
                select(DatasetModel).where(DatasetModel.description.contains("dify-business-contract-v2"))
            )
            target = session.get(DifyTargetModel, BASELINE_TARGET_ID)
            if dataset is None or target is None:
                raise RuntimeError("Business dataset or published Dify baseline target is unavailable.")
            if not dataset.is_frozen or dataset.review_status != "approved":
                raise RuntimeError("Business dataset must be frozen and approved before executing.")
            run = EvaluationRunModel(
                id=new_id("run"),
                dataset_id=dataset.id,
                baseline_target_id=target.id,
                run_mode="baseline",
                candidate_target_id=None,
                candidate_id=None,
                status="queued",
                idempotency_key=args.idempotency_key,
                snapshot={
                    "dataset_hash": dataset.content_hash,
                    "dataset_version": dataset.version,
                    "baseline_version": target.version_fingerprint,
                    "mode": "baseline",
                    "candidate_version": "",
                    "prompt_sha256": "",
                },
                created_by="system:business-baseline-executor",
                created_at=now_utc(),
                started_at=None,
                completed_at=None,
            )
            session.add(run)
            audit(
                session,
                actor_id="system:business-baseline-executor",
                action="evaluation.queued",
                resource_type="evaluation_run",
                resource_id=run.id,
                request_id=new_id("baseline_request"),
                detail={
                    "dataset_id": dataset.id,
                    "mode": "baseline",
                    "idempotency_key": args.idempotency_key,
                },
            )
            session.commit()
            run_id = run.id

    worker = EvaluationWorker(worker_id="business-baseline-executor")
    worker.process_once()
    with SessionLocal() as session:
        run = session.get(EvaluationRunModel, run_id)
        report = session.get(EvaluationReportModel, run_id)
        if run is None:
            raise RuntimeError("Baseline run disappeared.")
        print(
            json.dumps(
                {
                    "run_id": run.id,
                    "status": run.status,
                    "last_error": run.last_error,
                    "gate_status": report.gate_status if report else None,
                    "failed_cases": report.failed_cases if report else None,
                    "manual_review_cases": report.manual_review_cases if report else None,
                    "execution_error_cases": report.execution_error_cases if report else None,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
