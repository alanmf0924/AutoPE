from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from threading import Event, Thread
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.contract_assessment import assess_case, valid_dimension_rule
from app.db import SessionLocal
from app.dify_runtime import DifyRuntimeClient, DifyRuntimeError, configured_secret_resolver
from app.models import (
    DatasetCaseModel,
    DatasetModel,
    DifyTargetModel,
    EvaluationCaseResultModel,
    EvaluationReportModel,
    EvaluationRunModel,
    ReviewCaseModel,
    WorkerHeartbeatModel,
)
from app.reporting import (
    SCORE_DIMENSIONS,
    apply_report_summary,
    calculate_report_summary,
    has_think_leak,
)
from app.repository import audit, new_id, now_utc

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AutoScore:
    scores: dict[str, int]
    complete: bool

    @property
    def total(self) -> int:
        return sum(self.scores.values())


def extract_dify_text(payload: dict[str, Any]) -> str:
    """Return a stable, human-readable Dify result without assuming a workflow schema."""

    answer = payload.get("answer")
    if isinstance(answer, str) and answer.strip():
        return answer.strip()
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("outputs", "answer", "text"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict) and value:
                return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def score_response(response_text: str, rules: dict[str, Any]) -> AutoScore:
    """Score only explicit machine assertions; incomplete rules stay review-required."""

    text = response_text.casefold()
    scores: dict[str, int] = {}
    complete = True
    for key in SCORE_DIMENSIONS:
        raw_rule = rules.get(key)
        if not isinstance(raw_rule, dict):
            complete = False
            scores[key] = 0
            continue
        required = raw_rule.get("required_terms", [])
        prohibited = raw_rule.get("prohibited_terms", [])
        if not valid_dimension_rule(raw_rule):
            complete = False
            scores[key] = 0
            continue
        has_all_required = all(term.casefold() in text for term in required)
        has_prohibited = any(term.casefold() in text for term in prohibited)
        scores[key] = 25 if has_all_required and not has_prohibited else 0
    if has_think_leak(response_text):
        scores["safety"] = 0
    return AutoScore(scores=scores, complete=complete)


def build_dify_inputs(target: DifyTargetModel, case: DatasetCaseModel) -> dict[str, str]:
    case_fields = {"prompt": case.prompt, "title": case.title, "risk_level": case.risk_level}
    if not target.input_mapping:
        return {"query": case.prompt}
    return {
        dify_field: case_fields[case_field]
        for case_field, dify_field in target.input_mapping.items()
        if case_field in case_fields and dify_field
    }


class EvaluationWorker:
    """DB-backed, lease-based worker for Dify calls.

    A worker never holds a transaction while making a network request. Result rows are unique per
    (run, dataset case), so a replacement worker can safely resume an expired lease.
    """

    def __init__(
        self,
        *,
        worker_id: str | None = None,
        runtime: DifyRuntimeClient | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.worker_id = worker_id or os.getenv("AUTOPE_WORKER_ID", f"worker-{os.getpid()}")
        self.runtime = runtime or DifyRuntimeClient(configured_secret_resolver(self.settings))
        self.session_factory = session_factory

    def heartbeat(self, state: str) -> None:
        with self.session_factory() as session:
            heartbeat = session.get(WorkerHeartbeatModel, self.worker_id)
            if heartbeat is None:
                heartbeat = WorkerHeartbeatModel(
                    worker_id=self.worker_id, state=state, heartbeat_at=now_utc()
                )
                session.add(heartbeat)
            else:
                heartbeat.state = state
                heartbeat.heartbeat_at = now_utc()
            session.commit()

    def claim_next_run(self) -> EvaluationRunModel | None:
        now = now_utc()
        reclaimable = or_(
            EvaluationRunModel.status == "queued",
            and_(
                EvaluationRunModel.status == "running",
                EvaluationRunModel.lease_expires_at.is_not(None),
                EvaluationRunModel.lease_expires_at < now,
            ),
        )
        with self.session_factory() as session:
            candidate_ids = list(
                session.scalars(
                    select(EvaluationRunModel.id)
                    .where(reclaimable)
                    .order_by(EvaluationRunModel.created_at.asc())
                    .limit(10)
                )
            )
            for run_id in candidate_ids:
                result = session.execute(
                    update(EvaluationRunModel)
                    .where(EvaluationRunModel.id == run_id, reclaimable)
                    .values(
                        status="running",
                        claimed_by=self.worker_id,
                        lease_expires_at=now
                        + timedelta(seconds=self.settings.worker_lease_seconds),
                        started_at=func.coalesce(EvaluationRunModel.started_at, now),
                        attempt_count=EvaluationRunModel.attempt_count + 1,
                        last_error="",
                    )
                )
                if result.rowcount:
                    session.commit()
                    return session.get(EvaluationRunModel, run_id)
            session.rollback()
        return None

    def _renew_lease(self, run_id: str) -> bool:
        with self.session_factory() as session:
            result = session.execute(
                update(EvaluationRunModel)
                .where(
                    EvaluationRunModel.id == run_id,
                    EvaluationRunModel.status == "running",
                    EvaluationRunModel.claimed_by == self.worker_id,
                )
                .values(
                    lease_expires_at=now_utc()
                    + timedelta(seconds=self.settings.worker_lease_seconds)
                )
            )
            if not result.rowcount:
                run = session.get(EvaluationRunModel, run_id)
                if run is not None and run.status == "cancelled":
                    run.claimed_by = None
                    run.lease_expires_at = None
                    session.commit()
                    return False
                session.rollback()
                raise RuntimeError("Evaluation run lease ownership was lost.")
            session.commit()
        return True

    def process_once(self) -> bool:
        self.heartbeat("polling")
        run = self.claim_next_run()
        if run is None:
            self.heartbeat("idle")
            return False
        self.heartbeat("running")
        stop_pulse = Event()
        pulse = Thread(target=self._pulse_while_running, args=(stop_pulse,), daemon=True)
        pulse.start()
        try:
            self._execute_run(run.id)
        except Exception as error:  # preserve the task and make a retry/requeue decision observable
            logger.exception("evaluation worker failed run %s", run.id)
            self._fail_run(run.id, str(error))
        finally:
            stop_pulse.set()
            pulse.join(timeout=1)
            self.heartbeat("idle")
        return True

    def _pulse_while_running(self, stop: Event) -> None:
        while not stop.wait(15):
            try:
                self.heartbeat("running")
            except Exception:
                logger.exception("Evaluation worker heartbeat failed")

    def _execute_run(self, run_id: str) -> None:
        with self.session_factory() as session:
            run = session.get(EvaluationRunModel, run_id)
            if run is None:
                raise RuntimeError("Evaluation run disappeared before execution.")
            dataset = session.get(DatasetModel, run.dataset_id)
            baseline = session.get(DifyTargetModel, run.baseline_target_id)
            candidate = (
                session.get(DifyTargetModel, run.candidate_target_id)
                if run.run_mode == "comparison" and run.candidate_target_id
                else None
            )
            if dataset is None or baseline is None or (run.run_mode == "comparison" and candidate is None):
                raise RuntimeError("Evaluation run references a missing target or dataset.")
            baseline_run_id = run.snapshot.get("baseline_reference_run_id") or None
            cases = list(
                session.scalars(
                    select(DatasetCaseModel)
                    .where(DatasetCaseModel.dataset_id == dataset.id)
                    .order_by(DatasetCaseModel.position.asc())
                )
            )
        if not cases:
            raise RuntimeError("Frozen dataset has no executable cases.")
        for case in cases:
            if not self._renew_lease(run_id):
                return
            if self._result_exists(run_id, case.id):
                continue
            self._execute_case(run_id, case, baseline, candidate, baseline_run_id)
        self._complete_run(run_id)

    def _result_exists(self, run_id: str, case_id: str) -> bool:
        with self.session_factory() as session:
            return (
                session.scalar(
                    select(EvaluationCaseResultModel.id).where(
                        EvaluationCaseResultModel.run_id == run_id,
                        EvaluationCaseResultModel.dataset_case_id == case_id,
                    )
                )
                is not None
            )

    def _execute_case(
        self,
        run_id: str,
        case: DatasetCaseModel,
        baseline: DifyTargetModel,
        candidate: DifyTargetModel | None,
        baseline_run_id: str | None = None,
    ) -> None:
        baseline_response = ""
        candidate_response = ""
        baseline_latency_ms = 0
        candidate_latency_ms = 0
        error_code = ""
        error_message = ""
        runtime_evidence: dict[str, Any] = {}
        try:
            if baseline_run_id:
                with self.session_factory() as session:
                    prior_result = session.scalar(select(EvaluationCaseResultModel).where(
                        EvaluationCaseResultModel.run_id == baseline_run_id,
                        EvaluationCaseResultModel.dataset_case_id == case.id,
                    ))
                if prior_result is None or prior_result.error_code or not prior_result.baseline_response.strip():
                    raise RuntimeError("Reusable baseline case result is missing or invalid.")
                baseline_response = prior_result.baseline_response
                baseline_latency_ms = prior_result.baseline_latency_ms
            else:
                baseline_execution = self.runtime.execute(
                    baseline,
                    inputs=build_dify_inputs(baseline, case),
                    query=case.prompt,
                    user=baseline.dify_user,
                )
                baseline_response = extract_dify_text(baseline_execution.payload)
                baseline_latency_ms = baseline_execution.latency_ms
            if candidate is None:
                candidate_response = baseline_response
                candidate_latency_ms = baseline_latency_ms
            else:
                if not self._renew_lease(run_id):
                    return
                candidate_execution = self.runtime.execute(
                    candidate,
                    inputs=build_dify_inputs(candidate, case),
                    query=case.prompt,
                    user=candidate.dify_user,
                )
                candidate_response = extract_dify_text(candidate_execution.payload)
                candidate_latency_ms = candidate_execution.latency_ms
                runtime_evidence = candidate_execution.evidence
            if candidate is None and not baseline_run_id:
                runtime_evidence = baseline_execution.evidence
        except DifyRuntimeError as error:
            error_code, error_message = error.code, error.safe_message

        baseline_score = score_response(baseline_response, case.evaluation_rules)
        candidate_score = score_response(candidate_response, case.evaluation_rules)
        automatic_complete = (
            not error_code and baseline_score.complete and candidate_score.complete
        )
        assessment = assess_case(
            response_text=candidate_response,
            expected_contract=case.expected_contract,
            score_complete=automatic_complete,
            scores=candidate_score.scores,
            runtime_evidence=runtime_evidence,
            error_code=error_code,
            expected_workflow_id=(candidate or baseline).published_workflow_id,
        )
        passed = assessment["verdict"] == "passed"
        result_status = (
            "passed" if passed else "manual_review" if assessment["verdict"] == "needs_review" else "failed"
        )
        result = EvaluationCaseResultModel(
            id=new_id("case_result"),
            run_id=run_id,
            dataset_case_id=case.id,
            status=result_status,
            baseline_response=baseline_response,
            candidate_response=candidate_response,
            baseline_scores=baseline_score.scores,
            candidate_scores=candidate_score.scores,
            automatic_score_complete=automatic_complete,
            passed=passed,
            baseline_latency_ms=baseline_latency_ms,
            candidate_latency_ms=candidate_latency_ms,
            error_code=error_code,
            error_message=error_message,
            runtime_evidence=runtime_evidence,
            automatic_assessment=assessment,
            completed_at=now_utc(),
        )
        with self.session_factory() as session:
            active_run = session.get(EvaluationRunModel, run_id)
            if (
                active_run is None
                or active_run.status != "running"
                or active_run.claimed_by != self.worker_id
            ):
                return
            exists = session.scalar(
                select(EvaluationCaseResultModel.id).where(
                    EvaluationCaseResultModel.run_id == run_id,
                    EvaluationCaseResultModel.dataset_case_id == case.id,
                )
            )
            if exists is not None:
                return
            session.add(result)
            if not passed:
                session.add(
                    ReviewCaseModel(
                        id=new_id("review"),
                        dataset_id=case.dataset_id,
                        title=case.title,
                        risk_level=case.risk_level,
                        review_status="pending",
                        score=candidate_score.total if automatic_complete else 0,
                        source_run_id=run_id,
                        dataset_case_id=case.id,
                        request_text=case.prompt,
                        response_text=candidate_response,
                        expected_behavior=case.expected_behavior,
                        updated_at=now_utc(),
                    )
                )
            session.commit()

    def _complete_run(self, run_id: str) -> None:
        with self.session_factory() as session:
            run = session.get(EvaluationRunModel, run_id)
            if run is None or run.status == "cancelled":
                return
            if run.claimed_by != self.worker_id or run.status != "running":
                raise RuntimeError("Evaluation run lease ownership was lost before completion.")
            cases = list(
                session.scalars(
                    select(DatasetCaseModel).where(DatasetCaseModel.dataset_id == run.dataset_id)
                )
            )
            results = list(
                session.scalars(
                    select(EvaluationCaseResultModel).where(EvaluationCaseResultModel.run_id == run_id)
                )
            )
            if len(cases) != len(results):
                raise RuntimeError("Evaluation run is missing case results.")
            summary = calculate_report_summary(cases, results)
            report = session.get(EvaluationReportModel, run_id)
            if report is None:
                report = EvaluationReportModel(run_id=run_id, average_cost_usd=0.0)
                session.add(report)
            else:
                report.average_cost_usd = 0.0
            apply_report_summary(report, summary)
            run.status = "completed"
            run.completed_at = now_utc()
            run.claimed_by = None
            run.lease_expires_at = None
            audit(
                session,
                actor_id=f"worker:{self.worker_id}",
                action="evaluation.completed",
                resource_type="evaluation_run",
                resource_id=run_id,
                request_id=new_id("worker_request"),
                detail={
                    "failed_cases": summary.failed_cases,
                    "manual_review_cases": summary.manual_review_cases,
                    "execution_error_cases": summary.execution_error_cases,
                    "gate_status": summary.gate_status,
                },
                identity_source="service", object_version=str(run.completed_at),
            )
            session.commit()

    def _fail_run(self, run_id: str, error_message: str) -> None:
        with self.session_factory() as session:
            result = session.execute(
                update(EvaluationRunModel)
                .where(
                    EvaluationRunModel.id == run_id,
                    EvaluationRunModel.status == "running",
                    EvaluationRunModel.claimed_by == self.worker_id,
                )
                .values(
                    status="failed",
                    completed_at=now_utc(),
                    lease_expires_at=None,
                    claimed_by=None,
                    last_error=error_message[:1000],
                )
            )
            if result.rowcount:
                session.commit()


def main() -> None:
    logging.basicConfig(level=os.getenv("AUTOPE_LOG_LEVEL", "INFO"))
    worker = EvaluationWorker()
    poll_seconds = max(1.0, float(os.getenv("AUTOPE_WORKER_POLL_SECONDS", "2")))
    while True:
        worker.process_once()
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
