from __future__ import annotations

import hashlib
import json
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.account_management import (
    CreateAccountRequest,
    UpdateAccountRequest,
    create_account,
    list_accounts,
    update_account,
)
from app.approval_evidence import (
    annotation_version_key,
    contribution_rows,
    dataset_contributors,
    dataset_evidence_digest,
    digest_json,
    record_contribution,
    require_if_match,
    require_trusted_contributors,
)
from app.config import get_settings
from app.contract_assessment import automation_readiness
from app.credential_vault import CredentialVaultError, encrypt_local_api_key
from app.db import SessionLocal, get_session, initialize_database
from app.deployment_guard import verify_deployed_startup
from app.local_identity import BootstrapAdminRequest, bootstrap_admin, is_initialized
from app.local_recovery import (
    ChangePasswordRequest,
    ResetPasswordRequest,
    change_password,
    issue_recovery_token,
    reset_password,
)
from app.local_session import (
    SESSION_ABSOLUTE,
    SESSION_COOKIE,
    LocalLoginRequest,
    login_local_account,
    revoke_local_session,
)
from app.models import (
    AuditEventModel,
    CandidateGenerationJobModel,
    DatasetCaseModel,
    DatasetModel,
    DifyCredentialModel,
    DifyTargetModel,
    EvaluationCaseResultModel,
    EvaluationReportModel,
    EvaluationRunModel,
    LocalAccountModel,
    LocalRoleGrantModel,
    PromptCandidateModel,
    ReleaseDecisionModel,
    ReleaseHandoffModel,
    ReviewAISuggestionModel,
    ReviewAnnotationModel,
    ReviewAnnotationRevisionModel,
    ReviewCaseAssignmentModel,
    ReviewCaseModel,
    WorkerHeartbeatModel,
)
from app.reporting import (
    SCORE_DIMENSIONS,
    ReportSummary,
    apply_report_summary,
    hard_contract_failure,
    has_think_leak,
    report_summary_from_results,
)
from app.repository import audit as write_audit
from app.repository import new_id, now_utc, seed_development_data
from app.review_ai_worker import evidence_digest, evidence_for_case
from app.schemas import (
    CandidateBindingAttestationCreate,
    CandidateEvaluationLink,
    CandidateGenerationConfig,
    CandidateGenerationJob,
    CaseAutomationReadiness,
    DashboardReport,
    Dataset,
    DatasetAutomationReadiness,
    DatasetCase,
    DatasetCaseBulkCreate,
    DatasetCreate,
    DatasetDetail,
    DifyTarget,
    DifyTargetCreate,
    DifyTargetDetail,
    DifyTargetUpdate,
    EvaluationMode,
    EvaluationReportDetail,
    EvaluationRun,
    EvaluationRunCreate,
    PromptCandidate,
    PromptCandidateBind,
    PromptCandidateCreate,
    PromptCandidateDetail,
    PromptCandidateGenerate,
    ReleaseDecision,
    ReleaseDecisionCreate,
    ReleaseHandoff,
    ReleaseHandoffConfirm,
    ReleaseHandoffCreate,
    ReviewAISuggestion,
    ReviewAnnotationRevision,
    ReviewCase,
    ReviewCaseAssignmentsUpdate,
    ReviewCaseDetail,
    ReviewLabelCreate,
    ReviewLabelValues,
    ReviewReopenCreate,
    ReviewSuggestionBatchCreate,
    ReviewSuggestionBatchRetry,
    SessionIdentity,
)
from app.security import Actor, current_actor, require_roles

settings = get_settings()


def audit(
    session: Session, *, actor_id: str, action: str, resource_type: str,
    resource_id: str, request_id: str, detail: dict[str, object],
) -> None:
    """Capture the authenticated account's roles and the affected object version."""
    verified = not settings.auth_disabled and session.get(LocalAccountModel, actor_id) is not None
    roles = sorted(session.scalars(select(LocalRoleGrantModel.role).where(
        LocalRoleGrantModel.account_id == actor_id,
    ))) if verified else []
    version = next((str(detail[key]) for key in (
        "report_digest", "prompt_sha256", "content_hash", "candidate_target_version", "version",
    ) if detail.get(key) is not None), "")
    if resource_type == "dataset":
        dataset = session.get(DatasetModel, resource_id)
        if dataset is not None:
            version = str(dataset.version)
    elif resource_type == "review_case":
        review = session.get(ReviewCaseModel, resource_id)
        if review is not None:
            version = review.updated_at.isoformat()
    elif resource_type == "prompt_candidate":
        candidate = session.get(PromptCandidateModel, resource_id)
        if candidate is not None:
            version = candidate.prompt_sha256
    elif resource_type == "dify_target":
        target = session.get(DifyTargetModel, resource_id)
        if target is not None:
            version = target.version_fingerprint[:128]
    write_audit(
        session, actor_id=actor_id, action=action, resource_type=resource_type,
        resource_id=resource_id, request_id=request_id, detail=detail,
        identity_source="local_verified" if verified else "local_unverified",
        outcome="denied" if action.endswith("_denied") else "succeeded",
        actor_roles=roles, object_version=version[:128],
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_database()
    if settings.environment in {"shared_test", "production"}:
        with SessionLocal() as session:
            verify_deployed_startup(settings, session)
    if settings.environment in {"development", "test"} and settings.seed_demo_data:
        with SessionLocal() as session:
            seed_development_data(session)
    yield


app = FastAPI(title="Dify_AutoPE API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=not settings.auth_disabled,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "If-Match", "X-Request-ID", "X-CSRF-Token"],
)


def resource_or_404(
    session: Session, model: type[object], resource_id: str, resource_name: str
) -> object:
    resource = session.get(model, resource_id)
    if resource is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{resource_name} '{resource_id}' was not found.",
        )
    return resource


def require_review_assignment(session: Session, case_id: str, actor: Actor, request: Request) -> None:
    """Deny verified annotators before any restricted review evidence is read."""
    if settings.auth_disabled:
        return  # Local demo identity is intentionally unverified and has no account row.
    assigned = session.scalar(
        select(ReviewCaseAssignmentModel.id).where(
            ReviewCaseAssignmentModel.case_id == case_id,
            ReviewCaseAssignmentModel.account_id == actor.subject,
        )
    )
    if assigned is None:
        audit(session, actor_id=actor.subject, action="review.assignment_denied",
              resource_type="review_case", resource_id=case_id,
              request_id=request_id(request), detail={"reason_status": 403})
        session.commit()
        raise HTTPException(status_code=403, detail="Review case is not assigned to this account.")


def request_id(request: Request) -> str:
    value = request.headers.get("X-Request-ID", "").strip()
    return value[:64] if value else new_id("req")


def require_platform_admin(actor: Actor, request: Request, session: Session) -> None:
    if actor.is_platform_admin:
        return
    audit(session, actor_id=actor.subject, action="security.admin_denied",
          resource_type="http_route", resource_id=request.url.path[:64],
          request_id=request_id(request), detail={"reason_status": 403})
    session.commit()
    raise HTTPException(status_code=403, detail="Platform administrator required.")


def audit_anonymous_denial(
    session: Session, request: Request, *, action: str, status_code: int,
) -> None:
    write_audit(
        session, actor_id="", action=action, resource_type="http_route",
        resource_id=request.url.path[:64], request_id=request_id(request),
        detail={"reason_status": status_code}, identity_source="anonymous", outcome="denied",
    )
    session.commit()


def target_response(target: DifyTargetModel, *, credential_stored: bool) -> DifyTarget:
    return DifyTarget(
        id=target.id,
        name=target.name,
        base_url=target.base_url,
        runtime_path=target.runtime_path,
        target_kind=target.target_kind,
        application_id=target.application_id,
        input_mapping=target.input_mapping,
        dify_user=target.dify_user,
        credential_status="stored" if credential_stored else "external_reference",
        version_fingerprint=target.version_fingerprint,
        published_workflow_id=target.published_workflow_id,
        created_at=target.created_at,
    )


def target_detail_response(
    target: DifyTargetModel, *, credential_stored: bool, usage_count: int,
    candidate_binding_count: int,
) -> DifyTargetDetail:
    return DifyTargetDetail(
        **target_response(target, credential_stored=credential_stored).model_dump(),
        usage_count=usage_count,
        candidate_binding_count=candidate_binding_count,
        can_delete=usage_count == 0 and candidate_binding_count == 0,
    )


def dataset_response(dataset: DatasetModel) -> Dataset:
    return Dataset(
        id=dataset.id,
        name=dataset.name,
        description=dataset.description,
        case_count=dataset.case_count,
        review_status=dataset.review_status,
        version=dataset.version,
        is_frozen=dataset.is_frozen,
        created_at=dataset.created_at,
    )


def dataset_case_response(dataset_case: DatasetCaseModel) -> DatasetCase:
    return DatasetCase(
        id=dataset_case.id,
        position=dataset_case.position,
        title=dataset_case.title,
        prompt=dataset_case.prompt,
        expected_behavior=dataset_case.expected_behavior,
        expected_contract=dataset_case.expected_contract,
        evaluation_rules=dataset_case.evaluation_rules,
        risk_level=dataset_case.risk_level,
        created_at=dataset_case.created_at,
    )


def dataset_detail_response(
    session: Session, dataset: DatasetModel, cases: list[DatasetCaseModel]
) -> DatasetDetail:
    readiness_cases = [
        CaseAutomationReadiness(
            case_id=item.id, title=item.title,
            **automation_readiness(
                evaluation_rules=item.evaluation_rules,
                expected_contract=item.expected_contract,
            ),
        )
        for item in cases
    ]
    return DatasetDetail(
        **dataset_response(dataset).model_dump(),
        evidence_digest=dataset_evidence_digest(session, dataset),
        cases=[dataset_case_response(item) for item in cases],
        can_edit=not dataset.is_frozen,
        can_freeze=not dataset.is_frozen and bool(cases),
        automation_readiness=DatasetAutomationReadiness(
            eligible_cases=sum(1 for item in readiness_cases if item.eligible),
            total_cases=len(readiness_cases),
            cases=readiness_cases,
        ),
    )


def review_ai_suggestion_response(item: ReviewAISuggestionModel) -> ReviewAISuggestion:
    return ReviewAISuggestion(
        id=item.id, status=item.status, score_breakdown=item.score_breakdown,
        risk_level=item.risk_level, rationale=item.rationale, model=item.model,
        requested_at=item.requested_at, started_at=item.started_at,
        completed_at=item.completed_at, attempt_count=item.attempt_count,
        error_message=item.error_message,
    )


def review_case_response(review_case: ReviewCaseModel, suggestion: ReviewAISuggestionModel | None = None) -> ReviewCase:
    return ReviewCase(
        id=review_case.id,
        dataset_id=review_case.dataset_id,
        title=review_case.title,
        risk_level=review_case.risk_level,
        review_status=review_case.review_status,
        score=review_case.score,
        source_run_id=review_case.source_run_id or "",
        updated_at=review_case.updated_at,
        suggestion_status=suggestion.status if suggestion is not None else None,
        suggested_score=sum(suggestion.score_breakdown.values()) if suggestion is not None and suggestion.status == "completed" else None,
        suggestion_requested_at=suggestion.requested_at if suggestion is not None else None,
        suggestion_started_at=suggestion.started_at if suggestion is not None else None,
        suggestion_completed_at=suggestion.completed_at if suggestion is not None else None,
        suggestion_attempt_count=suggestion.attempt_count if suggestion is not None else 0,
    )


def review_case_detail_response(
    review_case: ReviewCaseModel,
    expected_contract: dict[str, object],
    *,
    annotation: ReviewAnnotationModel | None = None,
    annotation_history: list[ReviewAnnotationRevisionModel] | None = None,
    result: EvaluationCaseResultModel | None = None,
    suggestion: ReviewAISuggestionModel | None = None,
) -> ReviewCaseDetail:
    return ReviewCaseDetail(
        **review_case_response(review_case, suggestion).model_dump(),
        request_text=review_case.request_text,
        response_text=review_case.response_text,
        expected_behavior=review_case.expected_behavior,
        expected_contract=expected_contract,
        baseline_response=result.baseline_response if result is not None else "",
        candidate_response=result.candidate_response if result is not None else "",
        error_code=result.error_code if result is not None else "",
        error_message=result.error_message if result is not None else "",
        automatic_assessment=result.automatic_assessment if result is not None else {},
        runtime_evidence={
            "source": result.runtime_evidence.get("source", ""),
            "workflow_run_id": result.runtime_evidence.get("workflow_run_id", ""),
            "workflow_id": result.runtime_evidence.get("workflow_id", ""),
            "nodes": [
                node for node in result.runtime_evidence.get("nodes", [])
                if isinstance(node, dict) and node.get("node_type") == "tool"
            ],
        } if result is not None else {},
        annotation=(
            ReviewLabelValues(**annotation.score_breakdown, risk_level=annotation.risk_level, rationale=annotation.rationale)
            if annotation is not None else None
        ),
        annotated_by=annotation.reviewer_id if annotation is not None else None,
        annotation_history=[
            ReviewAnnotationRevision(
                id=item.id, reviewer_id=item.reviewer_id, event_kind=item.event_kind,
                score_breakdown=item.score_breakdown, risk_level=item.risk_level,
                rationale=item.rationale, created_at=item.created_at,
            )
            for item in (annotation_history or [])
        ],
        ai_suggestion=review_ai_suggestion_response(suggestion) if suggestion is not None else None,
    )


def hash_dataset_cases(cases: list[DatasetCaseModel]) -> str:
    normalized = [
        {
            "position": item.position,
            "title": item.title,
            "prompt": item.prompt,
            "expected_behavior": item.expected_behavior,
            "expected_contract": item.expected_contract,
            "evaluation_rules": item.evaluation_rules,
            "risk_level": item.risk_level,
        }
        for item in cases
    ]
    return hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def report_narrative(summary: ReportSummary, mode: EvaluationMode, risk_coverage: dict[str, dict[str, int]]) -> list[str]:
    normal_rate = summary.pass_rates["normal"] * 100
    high_risk_rate = summary.pass_rates["high_risk"] * 100
    red_line_rate = summary.pass_rates["red_line"] * 100
    if summary.gate_status == "pending_review":
        gate_sentence = "门禁待人工复核：未判定用例不被视为模型失败，也不能进入发布审批。"
    elif summary.execution_error_cases and not summary.failed_cases:
        gate_sentence = "门禁未通过：存在 Dify 调用错误，本轮不能发布；请先排查运行时故障，再在获得调用授权后重新评测。"
    elif mode == "comparison":
        gate_sentence = (
            "门禁通过：可进入人工发布评审，但不会自动修改 Dify 生产 Prompt。"
            if summary.gate_status == "go"
            else "门禁未通过：候选不能发布，需先处理失败用例后重新评测。"
        )
    else:
        gate_sentence = (
            "回归门禁通过：基线符合当前验收门槛。"
            if summary.gate_status == "go"
            else "回归门禁未通过：请先完成失败用例的业务复核或修复后重新评测。"
        )
    automatic_outcome_count = summary.passed_cases + summary.failed_cases
    all_assessed = all(group["assessed"] == group["total"] for group in risk_coverage.values())
    rate_sentence = (
        f"普通用例通过率 {normal_rate:.0f}%（要求不低于 85%）；高风险 {high_risk_rate:.0f}%、红线 {red_line_rate:.0f}%（均要求 100%）。"
        if automatic_outcome_count and all_assessed
        else "各风险级别的判定覆盖尚不完整，未判定用例不能作为 0% 或模型失败解释；请逐条完成业务审核。"
    )
    return [
        gate_sentence,
        rate_sentence,
        f"本次有 {summary.failed_cases} 条真实失败、{summary.manual_review_cases} 条待人工复核、{summary.execution_error_cases} 条调用错误；P95 延迟 {summary.p95_latency_ms} ms。",
    ]


def candidate_response(candidate: PromptCandidateModel) -> PromptCandidate:
    return PromptCandidate(
        id=candidate.id,
        name=candidate.name,
        base_version_fingerprint=candidate.base_version_fingerprint,
        prompt_sha256=candidate.prompt_sha256,
        status=candidate.status,
        bound_target_id=candidate.bound_target_id,
        binding_attested=bool(candidate.binding_attestation),
        created_at=candidate.created_at,
    )


def ensure_independent_candidate_target(
    session: Session, *, baseline_version: str, candidate_target: DifyTargetModel
) -> None:
    baseline = session.scalar(
        select(DifyTargetModel).where(DifyTargetModel.version_fingerprint == baseline_version)
    )
    if baseline is None or not baseline.application_id or not candidate_target.application_id:
        raise HTTPException(status_code=409, detail="Both baseline and candidate Dify application IDs are required before binding.")
    if candidate_target.id == baseline.id or candidate_target.application_id == baseline.application_id:
        raise HTTPException(status_code=409, detail="Candidate must use an independent cloned Dify application, not the baseline application.")
    if candidate_target.version_fingerprint == baseline_version:
        raise HTTPException(status_code=409, detail="Candidate target needs a distinct version fingerprint.")


def ensure_unclaimed_candidate_target(
    session: Session, *, target_id: str, candidate_id: str | None = None
) -> None:
    query = select(PromptCandidateModel).where(PromptCandidateModel.bound_target_id == target_id)
    if candidate_id is not None:
        query = query.where(PromptCandidateModel.id != candidate_id)
    claimed_by = session.scalar(query)
    if claimed_by is not None:
        raise HTTPException(
            status_code=409,
            detail="Candidate Dify target is already bound to another prompt candidate. Create and publish a distinct isolated version before binding.",
        )


def candidate_detail_response(session: Session, candidate: PromptCandidateModel) -> PromptCandidateDetail:
    generation_job = session.scalar(
        select(CandidateGenerationJobModel)
        .where(CandidateGenerationJobModel.candidate_id == candidate.id)
        .order_by(CandidateGenerationJobModel.created_at.desc())
    )
    target = session.get(DifyTargetModel, candidate.bound_target_id) if candidate.bound_target_id else None
    runs = list(session.scalars(
        select(EvaluationRunModel)
        .where(EvaluationRunModel.candidate_id == candidate.id)
        .order_by(EvaluationRunModel.created_at.desc())
        .limit(100)
    ))
    run_count = session.scalar(
        select(func.count(EvaluationRunModel.id)).where(EvaluationRunModel.candidate_id == candidate.id)
    ) or 0
    return PromptCandidateDetail(
        **candidate_response(candidate).model_dump(),
        prompt_block=candidate.prompt_block,
        binding_attestation=candidate.binding_attestation or {},
        generation_job=candidate_job_response(generation_job) if generation_job else None,
        bound_target_name=target.name if target else None,
        bound_target_version_fingerprint=target.version_fingerprint if target else None,
        evaluation_runs=[CandidateEvaluationLink(
            id=run.id, dataset_id=run.dataset_id, status=run.status, created_at=run.created_at,
        ) for run in runs],
        evaluation_run_count=run_count,
    )


def candidate_job_response(job: CandidateGenerationJobModel) -> CandidateGenerationJob:
    return CandidateGenerationJob(
        id=job.id,
        dataset_id=job.dataset_id,
        baseline_target_id=job.baseline_target_id,
        candidate_id=job.candidate_id,
        status=job.status,
        stage=job.stage,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


def run_response(
    run: EvaluationRunModel,
    *,
    completed_cases: int = 0,
    total_cases: int = 0,
    has_annotations: bool = False,
) -> EvaluationRun:
    return EvaluationRun(
        id=run.id,
        dataset_id=run.dataset_id,
        baseline_target_id=run.baseline_target_id,
        mode=run.run_mode,
        candidate_target_id=run.candidate_target_id,
        candidate_id=run.candidate_id,
        baseline_run_id=run.snapshot.get("baseline_reference_run_id") or None,
        rerun_scope=run.snapshot.get("rerun_scope", "all"),
        retry_source_run_id=run.snapshot.get("retry_source_run_id") or None,
        reused_cases=run.snapshot.get("reused_cases", 0),
        rerun_cases=run.snapshot.get("rerun_cases", total_cases),
        status=run.status,
        attempt_count=run.attempt_count,
        last_error=run.last_error,
        completed_cases=completed_cases,
        total_cases=total_cases,
        can_cancel=run.status in {"queued", "running"},
        can_delete=(
            run.status in {"cancelled", "failed"}
            and run.claimed_by is None
            and not has_annotations
        ),
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


def report_summary_for_run(
    session: Session, run: EvaluationRunModel, report: EvaluationReportModel
) -> ReportSummary:
    cases = list(
        session.scalars(
            select(DatasetCaseModel)
            .where(DatasetCaseModel.dataset_id == run.dataset_id)
            .order_by(DatasetCaseModel.position.asc())
        )
    )
    results = list(
        session.scalars(
            select(EvaluationCaseResultModel)
            .where(EvaluationCaseResultModel.run_id == run.id)
            .order_by(EvaluationCaseResultModel.completed_at.asc())
        )
    )
    summary = report_summary_from_results(cases, results, report)
    baseline_results = baseline_reference_results(session, run)
    if not baseline_results or not results:
        return summary
    dimensions: dict[str, dict[str, float]] = {}
    for key, label in SCORE_DIMENSIONS.items():
        baseline_total = sum(
            int(baseline_results[result.dataset_case_id].candidate_scores.get(key, 0))
            for result in results if result.dataset_case_id in baseline_results
        )
        dimensions[label] = {
            "baseline": round(baseline_total / len(results), 2),
            "candidate": summary.score_dimensions[label]["candidate"],
        }
    return replace(summary, score_dimensions=dimensions)


def baseline_reference_results(
    session: Session, run: EvaluationRunModel
) -> dict[str, EvaluationCaseResultModel]:
    reference_id = run.snapshot.get("baseline_reference_run_id") or ""
    if not reference_id or run.run_mode != "comparison":
        return {}
    return {
        result.dataset_case_id: result
        for result in session.scalars(select(EvaluationCaseResultModel).where(
            EvaluationCaseResultModel.run_id == reference_id
        ))
    }


def report_coverage_for_run(session: Session, run: EvaluationRunModel) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    cases = list(session.scalars(select(DatasetCaseModel).where(DatasetCaseModel.dataset_id == run.dataset_id)))
    results = list(session.scalars(select(EvaluationCaseResultModel).where(EvaluationCaseResultModel.run_id == run.id)))
    risks = {case.id: case.risk_level for case in cases}
    baseline_results = baseline_reference_results(session, run)
    risk_coverage = {
        risk: {
            "assessed": sum(1 for result in results if risks.get(result.dataset_case_id) == risk and (result.status != "manual_review" or has_think_leak(result.candidate_response) or hard_contract_failure(result)) and not result.error_code),
            "total": sum(1 for case in cases if case.risk_level == risk),
        }
        for risk in ("normal", "high_risk", "red_line")
    }
    baseline_assessed = sum(1 for result in results if result.automatic_score_complete)
    if baseline_results:
        baseline_assessed = 0
        for result in results:
            baseline_result = baseline_results.get(result.dataset_case_id)
            if baseline_result is not None and not baseline_result.error_code and (
                baseline_result.status != "manual_review"
                or has_think_leak(baseline_result.candidate_response)
                or hard_contract_failure(baseline_result)
            ):
                baseline_assessed += 1
    score_coverage = {
        "baseline": baseline_assessed,
        "candidate": sum(1 for result in results if result.automatic_score_complete or result.status in {"human_approved", "human_rejected"}),
        "total": len(results),
    }
    return risk_coverage, score_coverage


def release_report_digest(session: Session, run: EvaluationRunModel, report: EvaluationReportModel) -> str:
    summary = report_summary_for_run(session, run, report)
    results = list(session.scalars(select(EvaluationCaseResultModel).where(EvaluationCaseResultModel.run_id == run.id).order_by(EvaluationCaseResultModel.dataset_case_id)))
    reviews = list(session.scalars(select(ReviewCaseModel).where(ReviewCaseModel.source_run_id == run.id).order_by(ReviewCaseModel.id)))
    annotations = list(session.scalars(select(ReviewAnnotationModel).where(ReviewAnnotationModel.case_id.in_([item.id for item in reviews])).order_by(ReviewAnnotationModel.id))) if reviews else []
    baseline_evidence = ""
    baseline_id = run.snapshot.get("baseline_reference_run_id") or ""
    if baseline_id:
        baseline_run = session.get(EvaluationRunModel, baseline_id)
        baseline_report = session.get(EvaluationReportModel, baseline_id)
        if baseline_run and baseline_report and baseline_run.run_mode == "baseline":
            baseline_evidence = release_report_digest(session, baseline_run, baseline_report)
    evidence = {
        "snapshot": run.snapshot,
        "baseline_reference_evidence": baseline_evidence,
        "gate": summary.gate_status,
        "rates": summary.pass_rates,
        "counts": {"passed": summary.passed_cases, "failed": summary.failed_cases, "manual_review": summary.manual_review_cases, "execution_error": summary.execution_error_cases},
        "results": [{"case_id": item.dataset_case_id, "status": item.status, "passed": item.passed, "baseline_scores": item.baseline_scores, "candidate_scores": item.candidate_scores, "error_code": item.error_code, "runtime_evidence": item.runtime_evidence, "automatic_assessment": item.automatic_assessment} for item in results],
        "reviews": [{"id": item.id, "status": item.review_status, "score": item.score, "risk": item.risk_level, "updated_at": item.updated_at.isoformat()} for item in reviews],
        "annotations": [{"case_id": item.case_id, "reviewer_id": item.reviewer_id, "scores": item.score_breakdown, "risk": item.risk_level, "rationale": item.rationale} for item in annotations],
    }
    return hashlib.sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def approval_evidence_digest(
    session: Session, run: EvaluationRunModel, report: EvaluationReportModel
) -> str:
    dataset = session.get(DatasetModel, run.dataset_id)
    candidate = session.get(PromptCandidateModel, run.candidate_id) if run.candidate_id else None
    target = session.get(DifyTargetModel, run.candidate_target_id) if run.candidate_target_id else None
    baseline = session.get(DifyTargetModel, run.baseline_target_id)
    candidate_rows = contribution_rows(
        session, resource_type="candidate", resource_id=candidate.id,
        version_key=candidate.prompt_sha256,
    ) if candidate else []
    reviews = list(session.scalars(select(ReviewCaseModel).where(
        ReviewCaseModel.source_run_id == run.id,
    ).order_by(ReviewCaseModel.id)))
    review_rows = []
    for review in reviews:
        annotation = session.scalar(select(ReviewAnnotationModel).where(
            ReviewAnnotationModel.case_id == review.id,
        ).order_by(ReviewAnnotationModel.created_at.desc(), ReviewAnnotationModel.id.desc()))
        if annotation is not None:
            review_rows.extend(contribution_rows(
                session, resource_type="review_case", resource_id=review.id,
                version_key=annotation_version_key(annotation),
            ))
    return digest_json({
        "report_digest": release_report_digest(session, run, report),
        "dataset_evidence": dataset_evidence_digest(session, dataset) if dataset else None,
        "candidate": {
            "id": candidate.id, "prompt_sha256": candidate.prompt_sha256,
            "bound_target_id": candidate.bound_target_id,
            "binding_attestation": candidate.binding_attestation,
        } if candidate else None,
        "candidate_contributions": [(item.id, item.actor_id, item.action) for item in candidate_rows],
        "review_contributions": [(item.id, item.actor_id, item.action) for item in review_rows],
        "target": (target.id, target.version_fingerprint, target.published_workflow_id) if target else None,
        "baseline": (baseline.id, baseline.version_fingerprint, baseline.published_workflow_id) if baseline else None,
    })


def require_current_release_evidence(
    session: Session, run: EvaluationRunModel, report: EvaluationReportModel, actor_id: str
) -> None:
    dataset = session.get(DatasetModel, run.dataset_id)
    candidate = session.get(PromptCandidateModel, run.candidate_id) if run.candidate_id else None
    target = session.get(DifyTargetModel, run.candidate_target_id) if run.candidate_target_id else None
    baseline = session.get(DifyTargetModel, run.baseline_target_id)
    if (dataset is None or candidate is None or target is None or baseline is None
            or not dataset.is_frozen or dataset.review_status != "approved"
            or dataset.version != run.snapshot.get("dataset_version")
            or dataset.content_hash != run.snapshot.get("dataset_hash")
            or candidate.prompt_sha256 != run.snapshot.get("prompt_sha256")
            or candidate.bound_target_id != target.id
            or candidate.binding_attestation.get("prompt_sha256") != run.snapshot.get("prompt_sha256")
            or candidate.binding_attestation.get("published_workflow_id") != run.snapshot.get("candidate_published_workflow_id")
            or target.version_fingerprint != run.snapshot.get("candidate_version")
            or baseline.version_fingerprint != run.snapshot.get("baseline_version")
            or target.published_workflow_id != run.snapshot.get("candidate_published_workflow_id")
            or baseline.published_workflow_id != run.snapshot.get("baseline_published_workflow_id")):
        raise HTTPException(status_code=409, detail="Current release evidence differs from the evaluated snapshot.")
    cases = list(session.scalars(select(DatasetCaseModel).where(
        DatasetCaseModel.dataset_id == dataset.id,
    ).order_by(DatasetCaseModel.position)))
    if dataset.content_hash != hash_dataset_cases(cases):
        raise HTTPException(status_code=409, detail="Dataset content differs from its frozen snapshot.")
    require_trusted_contributors(dataset_contributors(session, dataset), actor_id)
    candidate_rows = contribution_rows(
        session, resource_type="candidate", resource_id=candidate.id,
        version_key=candidate.prompt_sha256,
    )
    if not any(item.action == "binding_attested" for item in candidate_rows):
        raise HTTPException(status_code=409, detail="Candidate binding needs verified re-attestation.")
    require_trusted_contributors({item.actor_id for item in candidate_rows}, actor_id)
    reviews = list(session.scalars(select(ReviewCaseModel).where(
        ReviewCaseModel.source_run_id == run.id,
    )))
    for review in reviews:
        annotation = session.scalar(select(ReviewAnnotationModel).where(
            ReviewAnnotationModel.case_id == review.id,
        ).order_by(ReviewAnnotationModel.created_at.desc(), ReviewAnnotationModel.id.desc()))
        if annotation is None or review.review_status == "pending":
            raise HTTPException(status_code=409, detail="Review evidence needs verified submission.")
        rows = contribution_rows(
            session, resource_type="review_case", resource_id=review.id,
            version_key=annotation_version_key(annotation),
        )
        if not any(item.actor_id == annotation.reviewer_id and item.action == "labeled" for item in rows):
            raise HTTPException(status_code=409, detail="Review evidence needs verified re-submission.")
        require_trusted_contributors({item.actor_id for item in rows}, actor_id)


def guard_release_submission(
    session: Session, request: Request, actor: Actor, run: EvaluationRunModel,
    report: EvaluationReportModel, *, resource_type: str, resource_id: str,
    require_independent: bool = True,
) -> str:
    # Keep the checked snapshot stable until this transaction commits. Writers update
    # these rows before committing evidence changes; PostgreSQL will serialize them.
    session.scalars(select(DatasetModel).where(DatasetModel.id == run.dataset_id).with_for_update()).all()
    if run.candidate_id:
        session.scalars(select(PromptCandidateModel).where(
            PromptCandidateModel.id == run.candidate_id,
        ).with_for_update()).all()
    target_ids = [item for item in (run.baseline_target_id, run.candidate_target_id) if item]
    session.scalars(select(DifyTargetModel).where(DifyTargetModel.id.in_(target_ids)).with_for_update()).all()
    session.scalars(select(ReviewCaseModel).where(
        ReviewCaseModel.source_run_id == run.id,
    ).with_for_update()).all()
    session.scalars(select(EvaluationCaseResultModel).where(
        EvaluationCaseResultModel.run_id == run.id,
    ).with_for_update()).all()
    session.scalars(select(EvaluationRunModel).where(EvaluationRunModel.id == run.id).with_for_update()).all()
    session.scalars(select(EvaluationReportModel).where(
        EvaluationReportModel.run_id == run.id,
    ).with_for_update()).all()
    digest = approval_evidence_digest(session, run, report)
    if settings.auth_disabled:
        return digest
    try:
        require_if_match(request, digest, verified=True)
        if require_independent:
            require_current_release_evidence(session, run, report, actor.subject)
    except HTTPException as error:
        audit(session, actor_id=actor.subject, action="release.submission_denied",
              resource_type=resource_type, resource_id=resource_id,
              request_id=request_id(request),
              detail={"reason_status": error.status_code, "run_id": run.id})
        session.commit()
        raise
    return digest


def release_decision_response(session: Session, decision: ReleaseDecisionModel) -> ReleaseDecision:
    run = session.get(EvaluationRunModel, decision.run_id)
    report = session.get(EvaluationReportModel, decision.run_id)
    current = bool(run and report and approval_evidence_digest(session, run, report) == decision.report_digest)
    if (current and decision.decision == "approved" and not settings.auth_disabled
            and run is not None and report is not None):
        try:
            require_current_release_evidence(session, run, report, decision.actor_id)
        except HTTPException:
            current = False
    if current and decision.decision == "approved" and run is not None and report is not None:
        summary = report_summary_for_run(session, run, report)
        coverage, _ = report_coverage_for_run(session, run)
        current = summary.gate_status == "go" and not (summary.failed_cases or summary.manual_review_cases or summary.execution_error_cases) and bool(sum(group["total"] for group in coverage.values())) and all(group["assessed"] == group["total"] for group in coverage.values())
    return ReleaseDecision(id=decision.id, run_id=decision.run_id, decision=decision.decision, rationale=decision.rationale, actor_id=decision.actor_id, created_at=decision.created_at, is_current=current)


def release_handoff_response(session: Session, handoff: ReleaseHandoffModel) -> ReleaseHandoff:
    decision = session.get(ReleaseDecisionModel, handoff.decision_id)
    is_current = bool(decision and release_decision_response(session, decision).is_current)
    return ReleaseHandoff(
        id=handoff.id, decision_id=handoff.decision_id, action=handoff.action,
        dify_app_id=handoff.dify_app_id, dify_console_url=handoff.dify_console_url,
        candidate_app_id=handoff.candidate_app_id,
        candidate_console_url=handoff.candidate_console_url,
        source_workflow_id=handoff.source_workflow_id,
        published_workflow_id=handoff.published_workflow_id, status=handoff.status,
        notes=handoff.notes, actor_id=handoff.actor_id, confirmed_by=handoff.confirmed_by,
        created_at=handoff.created_at, confirmed_at=handoff.confirmed_at,
        is_decision_current=is_current,
    )


def valid_dify_workflow_id(value: str) -> bool:
    try:
        return str(UUID(value)) == value.lower()
    except ValueError:
        return False


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "dify-autope"}


@app.get("/api/session", response_model=SessionIdentity)
def current_session(actor: Actor = Depends(current_actor)) -> SessionIdentity:
    return SessionIdentity(
        subject=actor.subject,
        display_name=actor.display_name,
        roles=sorted(actor.roles),
        is_platform_admin=actor.is_platform_admin,
        allowed_actions=actor.allowed_actions,
        session_state="active",
        csrf_token=actor.csrf_token,
        environment=settings.environment,
        authentication="disabled_local" if settings.auth_disabled else "local_session",
    )


@app.get("/api/audit-events")
def list_audit_events(
    _actor: Actor = Depends(require_roles("auditor")),
    session: Session = Depends(get_session),
    limit: int = 100,
) -> list[dict[str, object]]:
    """Global auditor view: only fixed metadata, never legacy free-form detail."""
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="Limit must be between 1 and 200.")
    events = session.scalars(select(AuditEventModel).order_by(
        AuditEventModel.created_at.desc(), AuditEventModel.id.desc(),
    ).limit(limit)).all()
    return [{
        "id": event.id, "actor_id": event.actor_id,
        "identity_source": event.identity_source, "actor_roles": event.actor_roles,
        "action": event.action, "outcome": event.outcome,
        "resource_type": event.resource_type, "resource_id": event.resource_id,
        "object_version": event.object_version, "request_id": event.request_id,
        "created_at": event.created_at.isoformat(),
    } for event in events]


@app.get("/api/setup/status")
def setup_status(session: Session = Depends(get_session)) -> dict[str, bool]:
    return {"initialized": is_initialized(session)}


@app.post("/api/setup", status_code=status.HTTP_201_CREATED)
def create_initial_admin(
    payload: BootstrapAdminRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, str]:
    origin = request.headers.get("Origin", "")
    if origin not in settings.cors_origins:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Origin is not allowed.")
    try:
        account_id = bootstrap_admin(session, payload, request_id(request))
    except HTTPException as error:
        audit_anonymous_denial(session, request, action="identity.bootstrap_denied",
                               status_code=error.status_code)
        raise
    return {"account_id": account_id}


@app.post("/api/login")
def login_local(
    payload: LocalLoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> dict[str, str]:
    if request.headers.get("Origin", "") not in settings.cors_origins:
        raise HTTPException(status_code=403, detail="Origin is not allowed.")
    source = request.client.host if request.client is not None else "unknown"
    try:
        token = login_local_account(session, settings, payload.login_name, payload.password, source)
    except HTTPException as error:
        if error.status_code in {401, 403, 429}:
            audit_anonymous_denial(session, request, action="security.login_denied",
                                   status_code=error.status_code)
        raise
    if not token:
        audit_anonymous_denial(session, request, action="security.login_denied", status_code=401)
        raise HTTPException(status_code=401, detail="Invalid login credentials.")
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_ABSOLUTE.total_seconds()),
        httponly=True,
        secure=settings.environment in {"shared_test", "production"},
        samesite="lax",
        path="/",
    )
    return {"status": "ok"}


@app.post("/api/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout_local(request: Request, response: Response, _actor: Actor = Depends(current_actor)) -> None:
    revoke_local_session(request.cookies.get(SESSION_COOKIE, ""), settings)
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="lax")


@app.post("/api/password/change", status_code=status.HTTP_204_NO_CONTENT)
def change_own_password(
    payload: ChangePasswordRequest,
    request: Request,
    actor: Actor = Depends(current_actor),
    session: Session = Depends(get_session),
) -> None:
    change_password(session, actor.subject, payload, request_id(request))


@app.post("/api/password/reset", status_code=status.HTTP_204_NO_CONTENT)
def consume_password_reset(
    payload: ResetPasswordRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> None:
    if request.headers.get("Origin", "") not in settings.cors_origins:
        raise HTTPException(status_code=403, detail="Origin is not allowed.")
    try:
        reset_password(session, payload, request_id(request))
    except HTTPException as error:
        if error.status_code in {401, 403}:
            audit_anonymous_denial(session, request, action="identity.recovery_denied",
                                   status_code=error.status_code)
        raise


@app.post("/api/accounts/{account_id}/reset-token")
def issue_account_reset_token(
    account_id: str,
    request: Request,
    actor: Actor = Depends(current_actor),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    require_platform_admin(actor, request, session)
    token = issue_recovery_token(
        session, account_id, actor.subject, request_id(request), server_issued=False
    )
    return {"reset_token": token}


@app.get("/api/accounts")
def list_platform_accounts(
    request: Request,
    actor: Actor = Depends(current_actor),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    require_platform_admin(actor, request, session)
    return list_accounts(session, actor.subject)


@app.post("/api/accounts", status_code=status.HTTP_201_CREATED)
def create_platform_account(
    payload: CreateAccountRequest,
    request: Request,
    actor: Actor = Depends(current_actor),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    require_platform_admin(actor, request, session)
    return create_account(session, actor.subject, payload, request_id(request))


@app.patch("/api/accounts/{account_id}")
def update_platform_account(
    account_id: str,
    payload: UpdateAccountRequest,
    request: Request,
    actor: Actor = Depends(current_actor),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    require_platform_admin(actor, request, session)
    return update_account(session, actor.subject, account_id, payload, request_id(request))


@app.get("/api/readyz")
def ready(session: Session = Depends(get_session)) -> dict[str, str]:
    if settings.environment in {"shared_test", "production"}:
        # Keep deployed traffic gated until authorization and audit work is complete.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Platform authorization rollout is not complete.",
        )
    try:
        session.execute(text("SELECT 1"))
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database is not ready."
        ) from error
    cutoff = now_utc() - timedelta(seconds=settings.worker_heartbeat_max_age_seconds)
    evaluation_heartbeat = session.scalar(select(func.max(WorkerHeartbeatModel.heartbeat_at)).where(WorkerHeartbeatModel.state.not_like("candidate:%")))
    candidate_heartbeat = session.scalar(select(func.max(WorkerHeartbeatModel.heartbeat_at)).where(WorkerHeartbeatModel.state.like("candidate:%")))
    def is_recent(heartbeat: datetime | None) -> bool:
        if heartbeat is None:
            return False
        return (heartbeat.replace(tzinfo=UTC) if heartbeat.tzinfo is None else heartbeat) >= cutoff

    evaluation_status = "reachable" if is_recent(evaluation_heartbeat) else "stale_or_missing"
    candidate_status = "reachable" if is_recent(candidate_heartbeat) else "stale_or_missing"
    if settings.require_worker_heartbeat and (evaluation_status != "reachable" or candidate_status != "reachable"):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Evaluation or candidate worker heartbeat is stale.")
    return {"status": "ready", "database": "reachable", "worker": evaluation_status, "candidate_worker": candidate_status}


@app.get("/api/targets", response_model=list[DifyTarget])
def list_targets(
    session: Session = Depends(get_session),
    _actor: Actor = Depends(require_roles("operator", "prompt_owner", "approver", "auditor")),
) -> list[DifyTarget]:
    stored_refs = set(session.scalars(select(DifyCredentialModel.secret_ref)))
    return [
        target_response(item, credential_stored=item.secret_ref in stored_refs)
        for item in session.scalars(
            select(DifyTargetModel).order_by(DifyTargetModel.created_at.desc())
        )
    ]


@app.post("/api/targets", response_model=DifyTarget, status_code=status.HTTP_201_CREATED)
def create_target(
    payload: DifyTargetCreate,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("operator")),
) -> DifyTarget:
    has_inline_api_key = bool(payload.api_key.strip())
    if has_inline_api_key and settings.environment in {"shared_test", "production"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inline Dify API keys are disabled in deployed environments. Use an external secret reference.",
        )
    if has_inline_api_key and payload.base_url.host not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inline Dify API keys are only accepted for a local Dify address.",
        )
    target_id = new_id("target")
    secret_ref = payload.secret_ref.strip() or f"local/dify/{target_id}"
    existing_credential = session.get(DifyCredentialModel, secret_ref)
    if has_inline_api_key and existing_credential is not None:
        raise HTTPException(status_code=409, detail="This secret reference already has a stored credential. Reuse it without an inline API key or choose a new reference.")
    target = DifyTargetModel(
        id=target_id,
        name=payload.name,
        base_url=str(payload.base_url).rstrip("/"),
        runtime_path=payload.runtime_path,
        target_kind=payload.target_kind,
        secret_ref=secret_ref,
        application_id=payload.application_id,
        input_mapping=payload.input_mapping,
        dify_user=payload.dify_user,
        version_fingerprint=payload.version_fingerprint,
        published_workflow_id=payload.published_workflow_id,
        created_at=now_utc(),
    )
    session.add(target)
    if has_inline_api_key:
        try:
            encrypted_api_key = encrypt_local_api_key(payload.api_key)
        except CredentialVaultError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The local credential vault is unavailable.",
            ) from error
        stored_at = now_utc()
        session.add(
            DifyCredentialModel(
                secret_ref=secret_ref,
                encrypted_api_key=encrypted_api_key,
                created_at=stored_at,
                updated_at=stored_at,
            )
        )
    audit(
        session,
        actor_id=actor.subject,
        action="target.created",
        resource_type="dify_target",
        resource_id=target.id,
        request_id=request_id(request),
        detail={
            "application_id": target.application_id,
            "input_mapping_keys": sorted(target.input_mapping),
            "credential_status": "stored" if has_inline_api_key or existing_credential is not None else "external_reference",
            "version_fingerprint": target.version_fingerprint,
            "published_workflow_id": target.published_workflow_id,
        },
    )
    session.commit()
    return target_response(
        target,
        credential_stored=session.get(DifyCredentialModel, secret_ref) is not None,
    )


@app.get("/api/targets/{target_id}", response_model=DifyTargetDetail)
def get_target(
    target_id: str,
    session: Session = Depends(get_session),
    _actor: Actor = Depends(require_roles("operator", "prompt_owner", "approver", "auditor")),
) -> DifyTargetDetail:
    target = resource_or_404(session, DifyTargetModel, target_id, "Dify target")
    assert isinstance(target, DifyTargetModel)
    usage_count = session.scalar(
        select(func.count())
        .select_from(EvaluationRunModel)
        .where(
            or_(
                EvaluationRunModel.baseline_target_id == target.id,
                EvaluationRunModel.candidate_target_id == target.id,
            )
        )
    )
    credential_stored = session.get(DifyCredentialModel, target.secret_ref) is not None
    candidate_binding_count = session.scalar(
        select(func.count()).select_from(PromptCandidateModel).where(
            PromptCandidateModel.bound_target_id == target.id
        )
    ) or 0
    return target_detail_response(
        target, credential_stored=credential_stored, usage_count=usage_count or 0,
        candidate_binding_count=candidate_binding_count,
    )


@app.patch("/api/targets/{target_id}", response_model=DifyTarget)
def update_target(
    target_id: str,
    payload: DifyTargetUpdate,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("operator")),
) -> DifyTarget:
    target = resource_or_404(session, DifyTargetModel, target_id, "Dify target")
    assert isinstance(target, DifyTargetModel)
    usage_count = session.scalar(select(func.count()).select_from(EvaluationRunModel).where(or_(
        EvaluationRunModel.baseline_target_id == target.id,
        EvaluationRunModel.candidate_target_id == target.id,
    ))) or 0
    if usage_count:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This target is referenced by evaluation runs and is immutable. Create a new versioned target instead.")
    if session.scalar(select(PromptCandidateModel.id).where(
        PromptCandidateModel.bound_target_id == target.id
    )) is not None:
        raise HTTPException(status_code=409, detail="This target is bound to a prompt candidate and is immutable. Create a new versioned target instead.")
    has_new_api_key = bool(payload.api_key.strip())
    if has_new_api_key and session.scalar(select(DifyTargetModel.id).where(
        DifyTargetModel.secret_ref == target.secret_ref,
        DifyTargetModel.id != target.id,
    )) is not None:
        raise HTTPException(status_code=409, detail="This credential is shared by another versioned target and cannot be rotated here.")
    if has_new_api_key and settings.environment in {"shared_test", "production"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inline Dify API keys are disabled in deployed environments. Use an external secret reference.",
        )
    if has_new_api_key and payload.base_url.host not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inline Dify API keys are only accepted for a local Dify address.",
        )
    target.name = payload.name
    target.base_url = str(payload.base_url).rstrip("/")
    target.runtime_path = payload.runtime_path
    target.target_kind = payload.target_kind
    target.application_id = payload.application_id
    target.input_mapping = payload.input_mapping
    target.dify_user = payload.dify_user
    target.version_fingerprint = payload.version_fingerprint
    target.published_workflow_id = payload.published_workflow_id
    if has_new_api_key:
        try:
            encrypted_api_key = encrypt_local_api_key(payload.api_key)
        except CredentialVaultError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The local credential vault is unavailable.",
            ) from error
        credential = session.get(DifyCredentialModel, target.secret_ref)
        if credential is None:
            credential = DifyCredentialModel(
                secret_ref=target.secret_ref,
                encrypted_api_key=encrypted_api_key,
                created_at=now_utc(),
                updated_at=now_utc(),
            )
            session.add(credential)
        else:
            credential.encrypted_api_key = encrypted_api_key
            credential.updated_at = now_utc()
    credential_stored = (
        session.get(DifyCredentialModel, target.secret_ref) is not None or has_new_api_key
    )
    audit(
        session,
        actor_id=actor.subject,
        action="target.updated",
        resource_type="dify_target",
        resource_id=target.id,
        request_id=request_id(request),
        detail={
            "application_id": target.application_id,
            "input_mapping_keys": sorted(target.input_mapping),
            "rotated_local_credential": has_new_api_key,
            "version_fingerprint": target.version_fingerprint,
            "published_workflow_id": target.published_workflow_id,
        },
    )
    session.commit()
    return target_response(target, credential_stored=credential_stored)


@app.delete("/api/targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_target(
    target_id: str,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("operator")),
) -> None:
    target = resource_or_404(session, DifyTargetModel, target_id, "Dify target")
    assert isinstance(target, DifyTargetModel)
    usage_count = (
        session.scalar(
            select(func.count())
            .select_from(EvaluationRunModel)
            .where(
                or_(
                    EvaluationRunModel.baseline_target_id == target.id,
                    EvaluationRunModel.candidate_target_id == target.id,
                )
            )
        )
        or 0
    )
    if usage_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This Dify target is referenced by evaluation runs and must be retained for auditability.",
        )
    if session.scalar(select(PromptCandidateModel.id).where(
        PromptCandidateModel.bound_target_id == target.id
    )) is not None:
        raise HTTPException(status_code=409, detail="This Dify target is bound to a prompt candidate and must be retained.")
    credential = session.get(DifyCredentialModel, target.secret_ref)
    shared_credential = session.scalar(select(DifyTargetModel.id).where(
        DifyTargetModel.secret_ref == target.secret_ref,
        DifyTargetModel.id != target.id,
    )) is not None
    if credential is not None and not shared_credential:
        session.delete(credential)
    audit(
        session,
        actor_id=actor.subject,
        action="target.deleted",
        resource_type="dify_target",
        resource_id=target.id,
        request_id=request_id(request),
        detail={"credential_removed": credential is not None and not shared_credential},
    )
    session.delete(target)
    session.commit()


@app.get("/api/datasets", response_model=list[Dataset])
def list_datasets(
    session: Session = Depends(get_session),
    _actor: Actor = Depends(require_roles("operator", "prompt_owner", "approver", "auditor")),
) -> list[Dataset]:
    return [
        dataset_response(item)
        for item in session.scalars(select(DatasetModel).order_by(DatasetModel.created_at.desc()))
    ]


@app.post("/api/datasets", response_model=Dataset, status_code=status.HTTP_201_CREATED)
def create_dataset(
    payload: DatasetCreate,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("operator")),
) -> Dataset:
    dataset = DatasetModel(
        id=new_id("dataset"),
        name=payload.name,
        description=payload.description,
        case_count=payload.case_count,
        review_status="draft",
        version=1,
        is_frozen=False,
        content_hash=hashlib.sha256(
            f"{payload.name}:{payload.description}:{payload.case_count}".encode()
        ).hexdigest(),
        created_at=now_utc(),
    )
    session.add(dataset)
    record_contribution(
        session, resource_type="dataset", resource_id=dataset.id,
        version_key=str(dataset.version), actor_id=actor.subject,
        action="created", verified=not settings.auth_disabled,
    )
    audit(
        session,
        actor_id=actor.subject,
        action="dataset.created",
        resource_type="dataset",
        resource_id=dataset.id,
        request_id=request_id(request),
        detail={"version": 1, "case_count": dataset.case_count},
    )
    session.commit()
    return dataset_response(dataset)


@app.get("/api/datasets/{dataset_id}", response_model=DatasetDetail)
def get_dataset(
    dataset_id: str,
    response: Response,
    session: Session = Depends(get_session),
    _actor: Actor = Depends(require_roles("operator", "prompt_owner", "approver", "auditor")),
) -> DatasetDetail:
    dataset = resource_or_404(session, DatasetModel, dataset_id, "Dataset")
    assert isinstance(dataset, DatasetModel)
    cases = list(
        session.scalars(
            select(DatasetCaseModel)
            .where(DatasetCaseModel.dataset_id == dataset.id)
            .order_by(DatasetCaseModel.position.asc())
        )
    )
    detail = dataset_detail_response(session, dataset, cases)
    response.headers["ETag"] = f'"{detail.evidence_digest}"'
    return detail


@app.post("/api/datasets/{dataset_id}/cases", response_model=DatasetDetail)
def upload_dataset_cases(
    dataset_id: str,
    payload: DatasetCaseBulkCreate,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("operator")),
) -> DatasetDetail:
    dataset = resource_or_404(session, DatasetModel, dataset_id, "Dataset")
    assert isinstance(dataset, DatasetModel)
    if dataset.is_frozen:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Frozen datasets cannot be changed. Create a new draft version instead.",
        )
    existing_cases = list(
        session.scalars(
            select(DatasetCaseModel)
            .where(DatasetCaseModel.dataset_id == dataset.id)
            .order_by(DatasetCaseModel.position.asc())
        )
    )
    if len(existing_cases) + len(payload.cases) > 500:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A dataset can contain at most 500 cases.",
        )
    created_at = now_utc()
    new_cases = [
        DatasetCaseModel(
            id=new_id("dataset_case"),
            dataset_id=dataset.id,
            position=len(existing_cases) + index,
            title=item.title,
            prompt=item.prompt,
            expected_behavior=item.expected_behavior,
            expected_contract=item.expected_contract,
            evaluation_rules=item.evaluation_rules,
            risk_level=item.risk_level,
            created_at=created_at,
        )
        for index, item in enumerate(payload.cases, start=1)
    ]
    session.add_all(new_cases)
    all_cases = [*existing_cases, *new_cases]
    dataset.case_count = len(all_cases)
    dataset.content_hash = hash_dataset_cases(all_cases)
    record_contribution(
        session, resource_type="dataset", resource_id=dataset.id,
        version_key=str(dataset.version), actor_id=actor.subject,
        action="cases_uploaded", verified=not settings.auth_disabled,
    )
    audit(
        session,
        actor_id=actor.subject,
        action="dataset.cases_uploaded",
        resource_type="dataset",
        resource_id=dataset.id,
        request_id=request_id(request),
        detail={"added_case_count": len(new_cases), "case_count": dataset.case_count},
    )
    session.commit()
    return dataset_detail_response(session, dataset, all_cases)


@app.post("/api/datasets/{dataset_id}/freeze", response_model=DatasetDetail)
def freeze_dataset(
    dataset_id: str,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("operator")),
) -> DatasetDetail:
    dataset = resource_or_404(session, DatasetModel, dataset_id, "Dataset")
    assert isinstance(dataset, DatasetModel)
    cases = list(
        session.scalars(
            select(DatasetCaseModel)
            .where(DatasetCaseModel.dataset_id == dataset.id)
            .order_by(DatasetCaseModel.position.asc())
        )
    )
    if dataset.is_frozen:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This dataset is already frozen.",
        )
    if not cases:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Upload at least one case before freezing a dataset.",
        )
    dataset.case_count = len(cases)
    dataset.content_hash = hash_dataset_cases(cases)
    dataset.is_frozen = True
    dataset.review_status = "in_review"
    record_contribution(
        session, resource_type="dataset", resource_id=dataset.id,
        version_key=str(dataset.version), actor_id=actor.subject,
        action="frozen", verified=not settings.auth_disabled,
    )
    audit(
        session,
        actor_id=actor.subject,
        action="dataset.frozen",
        resource_type="dataset",
        resource_id=dataset.id,
        request_id=request_id(request),
        detail={"case_count": dataset.case_count, "content_hash": dataset.content_hash},
    )
    session.commit()
    return dataset_detail_response(session, dataset, cases)


@app.post("/api/datasets/{dataset_id}/attest", response_model=DatasetDetail)
def attest_dataset_version(
    dataset_id: str,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("operator")),
) -> DatasetDetail:
    if settings.auth_disabled:
        raise HTTPException(status_code=403, detail="Verified local identity is required to attest evidence.")
    dataset = session.scalar(select(DatasetModel).where(DatasetModel.id == dataset_id).with_for_update())
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset was not found.")
    cases = list(session.scalars(select(DatasetCaseModel).where(
        DatasetCaseModel.dataset_id == dataset.id,
    ).order_by(DatasetCaseModel.position)))
    if not dataset.is_frozen or not cases or dataset.content_hash != hash_dataset_cases(cases):
        raise HTTPException(status_code=409, detail="Only an intact frozen dataset can be attested.")
    require_if_match(request, dataset_evidence_digest(session, dataset), verified=True)
    record_contribution(
        session, resource_type="dataset", resource_id=dataset.id,
        version_key=str(dataset.version), actor_id=actor.subject,
        action="attested", verified=True,
    )
    audit(session, actor_id=actor.subject, action="dataset.attested",
          resource_type="dataset", resource_id=dataset.id, request_id=request_id(request),
          detail={"version": dataset.version, "content_hash": dataset.content_hash})
    session.commit()
    return dataset_detail_response(session, dataset, cases)


@app.post("/api/datasets/{dataset_id}/approve", response_model=DatasetDetail)
def approve_dataset(
    dataset_id: str,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("approver")),
) -> DatasetDetail:
    dataset = session.scalar(select(DatasetModel).where(DatasetModel.id == dataset_id).with_for_update())
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset was not found.")
    if not dataset.is_frozen or dataset.review_status != "in_review":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only a frozen dataset awaiting approval can be approved.")
    cases = list(session.scalars(select(DatasetCaseModel).where(DatasetCaseModel.dataset_id == dataset.id).order_by(DatasetCaseModel.position)))
    if not cases or dataset.content_hash != hash_dataset_cases(cases):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Dataset content does not match the frozen snapshot.")
    if not settings.auth_disabled:
        current_digest = dataset_evidence_digest(session, dataset)
        try:
            require_if_match(request, current_digest, verified=True)
            require_trusted_contributors(dataset_contributors(session, dataset), actor.subject)
        except HTTPException as error:
            audit(session, actor_id=actor.subject, action="dataset.approval_denied",
                  resource_type="dataset", resource_id=dataset.id,
                  request_id=request_id(request),
                  detail={"reason_status": error.status_code, "version": dataset.version})
            session.commit()
            raise
    dataset.review_status = "approved"
    audit(session, actor_id=actor.subject, action="dataset.approved", resource_type="dataset", resource_id=dataset.id, request_id=request_id(request), detail={"content_hash": dataset.content_hash, "case_count": len(cases)})
    session.commit()
    return dataset_detail_response(session, dataset, cases)


@app.get("/api/review-cases", response_model=list[ReviewCase])
def list_review_cases(
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("annotator", "operator", "auditor")),
) -> list[ReviewCase]:
    query = (
        select(ReviewCaseModel)
        .join(EvaluationRunModel, ReviewCaseModel.source_run_id == EvaluationRunModel.id)
        .where(EvaluationRunModel.status == "completed")
        .order_by(ReviewCaseModel.updated_at.desc())
    )
    if not settings.auth_disabled and not actor.roles.intersection({"operator", "auditor"}):
        query = query.join(
            ReviewCaseAssignmentModel,
            ReviewCaseAssignmentModel.case_id == ReviewCaseModel.id,
        ).where(ReviewCaseAssignmentModel.account_id == actor.subject)
    cases = list(session.scalars(query))
    case_ids = [item.id for item in cases]
    suggestions = {
        item.case_id: item for item in session.scalars(
            select(ReviewAISuggestionModel).where(ReviewAISuggestionModel.case_id.in_(case_ids))
        )
    }
    return [review_case_response(item, suggestions.get(item.id)) for item in cases]


@app.get("/api/review-cases/{case_id}/assignments", response_model=list[str])
def get_review_case_assignments(
    case_id: str,
    session: Session = Depends(get_session),
    _actor: Actor = Depends(require_roles("operator", "auditor")),
) -> list[str]:
    resource_or_404(session, ReviewCaseModel, case_id, "Review case")
    return sorted(session.scalars(select(ReviewCaseAssignmentModel.account_id).where(
        ReviewCaseAssignmentModel.case_id == case_id,
    )))


@app.patch("/api/review-cases/{case_id}/assignments", response_model=list[str])
def update_review_case_assignments(
    case_id: str,
    payload: ReviewCaseAssignmentsUpdate,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("operator")),
) -> list[str]:
    if settings.auth_disabled:
        raise HTTPException(status_code=403, detail="Verified local identity is required to assign reviews.")
    review_case = session.scalar(
        select(ReviewCaseModel).where(ReviewCaseModel.id == case_id).with_for_update()
    )
    if review_case is None:
        raise HTTPException(status_code=404, detail="Review case was not found.")
    run = session.get(EvaluationRunModel, review_case.source_run_id or "")
    if run is None or run.status != "completed":
        raise HTTPException(status_code=409, detail="Only completed runs can be assigned for review.")
    if len(payload.account_ids) != len(set(payload.account_ids)):
        raise HTTPException(status_code=422, detail="Assignment account IDs must be unique.")
    account_ids = sorted(payload.account_ids)
    for account_id in account_ids:
        account = session.get(LocalAccountModel, account_id)
        granted = session.scalar(select(LocalRoleGrantModel.id).where(
            LocalRoleGrantModel.account_id == account_id,
            LocalRoleGrantModel.role == "annotator",
        ))
        if account is None or account.status != "active" or granted is None:
            raise HTTPException(status_code=422, detail="Each assignee must be an active annotator.")
    existing = set(session.scalars(select(ReviewCaseAssignmentModel.account_id).where(
        ReviewCaseAssignmentModel.case_id == case_id,
    )))
    session.execute(delete(ReviewCaseAssignmentModel).where(
        ReviewCaseAssignmentModel.case_id == case_id,
        ReviewCaseAssignmentModel.account_id.not_in(account_ids),
    ))
    for account_id in set(account_ids) - existing:
        session.add(ReviewCaseAssignmentModel(
            id=new_id("review_assignment"), case_id=case_id, account_id=account_id,
            assigned_by=actor.subject, created_at=now_utc(),
        ))
    audit(session, actor_id=actor.subject, action="review.assignments_updated",
          resource_type="review_case", resource_id=case_id, request_id=request_id(request),
          detail={"account_ids": account_ids})
    session.commit()
    return account_ids


@app.get("/api/review-suggestions/config")
def review_suggestion_config(
    _actor: Actor = Depends(require_roles("operator", "annotator", "auditor")),
) -> dict[str, object]:
    return {
        "model": os.getenv("AUTOPE_DSPY_MODEL", "").strip(),
        "endpoint": os.getenv("AUTOPE_DSPY_BASE_URL", "").strip(),
        "available": bool(os.getenv("AUTOPE_DSPY_API_KEY", "").strip()),
    }


@app.post("/api/evaluation-runs/{run_id}/review-suggestions", response_model=list[ReviewAISuggestion])
def queue_review_suggestions(
    run_id: str,
    payload: ReviewSuggestionBatchCreate,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("operator")),
) -> list[ReviewAISuggestion]:
    run = resource_or_404(session, EvaluationRunModel, run_id, "Evaluation run")
    assert isinstance(run, EvaluationRunModel)
    if run.status != "completed":
        raise HTTPException(status_code=409, detail="Evaluation must complete before AI review suggestions.")
    configured_model = os.getenv("AUTOPE_DSPY_MODEL", "").strip()
    configured_endpoint = os.getenv("AUTOPE_DSPY_BASE_URL", "").strip()
    if not payload.authorize_external_transfer or not configured_model or not configured_endpoint or not os.getenv("AUTOPE_DSPY_API_KEY", "").strip() or payload.consented_model != configured_model or payload.consented_endpoint != configured_endpoint:
        raise HTTPException(status_code=409, detail="Explicit consent for the configured model and endpoint is required.")
    cases = list(session.scalars(select(ReviewCaseModel).where(
        ReviewCaseModel.source_run_id == run_id, ReviewCaseModel.review_status == "pending"
    ).order_by(ReviewCaseModel.id)))
    if not cases or len(cases) > 10:
        raise HTTPException(status_code=409, detail="Select a completed run with 1 to 10 pending review cases.")
    existing = set(session.scalars(select(ReviewAISuggestionModel.case_id).where(ReviewAISuggestionModel.run_id == run_id)))
    if existing:
        raise HTTPException(status_code=409, detail="AI suggestions already exist for this run; no automatic repeat calls are allowed.")
    queued: list[ReviewAISuggestionModel] = []
    for review_case in cases:
        try:
            evidence = evidence_for_case(session, review_case)
        except ValueError as error:
            raise HTTPException(status_code=409, detail="This review has no complete frozen evaluation evidence for AI assistance.") from error
        queued.append(ReviewAISuggestionModel(
            id=new_id("review_ai"), case_id=review_case.id, run_id=run_id,
            status="queued", score_breakdown={}, risk_level=review_case.risk_level,
            rationale="", evidence_sha256=evidence_digest(evidence),
            model=configured_model, endpoint=configured_endpoint,
            requested_by=actor.subject, requested_at=now_utc(), attempt_count=0,
        ))
    session.add_all(queued)
    audit(session, actor_id=actor.subject, action="review.ai_suggestions_queued",
          resource_type="evaluation_run", resource_id=run_id, request_id=request_id(request),
          detail={"case_count": len(queued), "model": configured_model, "endpoint": configured_endpoint})
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail="AI suggestions already exist for this run; no repeat calls are allowed.") from error
    return [review_ai_suggestion_response(item) for item in queued]


@app.post("/api/review-suggestions/{suggestion_id}/retry", response_model=ReviewAISuggestion)
def retry_review_suggestion(
    suggestion_id: str,
    payload: ReviewSuggestionBatchCreate,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("operator")),
) -> ReviewAISuggestion:
    suggestion = resource_or_404(session, ReviewAISuggestionModel, suggestion_id, "AI suggestion")
    assert isinstance(suggestion, ReviewAISuggestionModel)
    review_case = session.get(ReviewCaseModel, suggestion.case_id)
    if suggestion.status != "failed" or review_case is None or review_case.review_status != "pending":
        raise HTTPException(status_code=409, detail="Only a failed suggestion for a pending review can be retried.")
    if not payload.authorize_external_transfer or payload.consented_model != suggestion.model or payload.consented_endpoint != suggestion.endpoint or suggestion.model != os.getenv("AUTOPE_DSPY_MODEL", "").strip() or suggestion.endpoint != os.getenv("AUTOPE_DSPY_BASE_URL", "").strip():
        raise HTTPException(status_code=409, detail="Explicit consent for the current AI destination is required.")
    calls_started = session.scalar(select(func.sum(ReviewAISuggestionModel.attempt_count)).where(
        ReviewAISuggestionModel.run_id == suggestion.run_id
    )) or 0
    try:
        evidence_matches = evidence_digest(evidence_for_case(session, review_case)) == suggestion.evidence_sha256
    except ValueError:
        evidence_matches = False
    if calls_started >= 10 or not evidence_matches:
        raise HTTPException(status_code=409, detail="Call budget is exhausted or evaluation evidence changed.")
    suggestion.status = "queued"
    suggestion.error_message = ""
    suggestion.started_at = None
    suggestion.completed_at = None
    audit(session, actor_id=actor.subject, action="review.ai_suggestion_retry_queued",
          resource_type="review_ai_suggestion", resource_id=suggestion.id, request_id=request_id(request),
          detail={"run_id": suggestion.run_id, "prior_calls_started": calls_started,
                  "model": suggestion.model, "endpoint": suggestion.endpoint})
    session.commit()
    return review_ai_suggestion_response(suggestion)


@app.post("/api/evaluation-runs/{run_id}/review-suggestions/retry-failed", response_model=list[ReviewAISuggestion])
def retry_failed_review_suggestions(
    run_id: str,
    payload: ReviewSuggestionBatchRetry,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("operator")),
) -> list[ReviewAISuggestion]:
    run = resource_or_404(session, EvaluationRunModel, run_id, "Evaluation run")
    assert isinstance(run, EvaluationRunModel)
    if run.status != "completed":
        raise HTTPException(status_code=409, detail="Evaluation must complete before AI review suggestions.")
    model = os.getenv("AUTOPE_DSPY_MODEL", "").strip()
    endpoint = os.getenv("AUTOPE_DSPY_BASE_URL", "").strip()
    if (not payload.authorize_external_transfer or not os.getenv("AUTOPE_DSPY_API_KEY", "").strip()
            or payload.consented_model != model or payload.consented_endpoint != endpoint):
        raise HTTPException(status_code=409, detail="Explicit consent for the configured model and endpoint is required.")
    suggestions = list(session.scalars(select(ReviewAISuggestionModel).where(
        ReviewAISuggestionModel.run_id == run_id
    ).order_by(ReviewAISuggestionModel.requested_at, ReviewAISuggestionModel.id)))
    failed = [item for item in suggestions if item.status == "failed"]
    if not failed or any(item.status in {"queued", "running", "uncertain"} for item in suggestions):
        raise HTTPException(status_code=409, detail="Only settled failed suggestions can be retried as a batch.")
    calls_started = sum(item.attempt_count for item in suggestions)
    call_limit = 10 + payload.additional_authorized_calls
    if calls_started + len(failed) > call_limit:
        raise HTTPException(status_code=409, detail="The authorized AI call budget is insufficient for failed suggestions.")
    for suggestion in failed:
        review_case = session.get(ReviewCaseModel, suggestion.case_id)
        if review_case is None or review_case.review_status != "pending" or suggestion.model != model or suggestion.endpoint != endpoint:
            raise HTTPException(status_code=409, detail="A failed suggestion is no longer eligible for retry.")
        try:
            evidence_matches = evidence_digest(evidence_for_case(session, review_case)) == suggestion.evidence_sha256
        except ValueError:
            evidence_matches = False
        if not evidence_matches:
            raise HTTPException(status_code=409, detail="Evaluation evidence changed; retry was not queued.")
    for suggestion in failed:
        changed = session.execute(update(ReviewAISuggestionModel).where(
            ReviewAISuggestionModel.id == suggestion.id,
            ReviewAISuggestionModel.status == "failed",
        ).values(status="queued", error_message="", started_at=None, completed_at=None))
        if changed.rowcount != 1:
            session.rollback()
            raise HTTPException(status_code=409, detail="Suggestion state changed; reload before retrying.")
    audit(session, actor_id=actor.subject, action="review.ai_suggestions_retry_queued",
          resource_type="evaluation_run", resource_id=run_id, request_id=request_id(request),
          detail={"case_count": len(failed), "prior_calls_started": calls_started,
                  "additional_authorized_calls": payload.additional_authorized_calls,
                  "call_limit": call_limit, "model": model, "endpoint": endpoint})
    session.commit()
    return [review_ai_suggestion_response(item) for item in failed]


@app.get("/api/review-cases/{case_id}", response_model=ReviewCaseDetail)
def get_review_case(
    case_id: str,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("annotator", "operator", "auditor")),
) -> ReviewCaseDetail:
    review_case = resource_or_404(session, ReviewCaseModel, case_id, "Review case")
    assert isinstance(review_case, ReviewCaseModel)
    if "annotator" in actor.roles and not actor.roles.intersection({"operator", "auditor"}):
        require_review_assignment(session, case_id, actor, request)
    source_run = resource_or_404(
        session, EvaluationRunModel, review_case.source_run_id or "", "Evaluation run"
    )
    assert isinstance(source_run, EvaluationRunModel)
    if source_run.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Review becomes available only after the evaluation run has completed.",
        )
    dataset_case = session.get(DatasetCaseModel, review_case.dataset_case_id or "")
    expected_contract = dataset_case.expected_contract if dataset_case is not None else {}
    annotation = session.scalar(select(ReviewAnnotationModel).where(
        ReviewAnnotationModel.case_id == case_id,
    ).order_by(ReviewAnnotationModel.created_at.desc(), ReviewAnnotationModel.id.desc()))
    annotation_history = list(session.scalars(
        select(ReviewAnnotationRevisionModel)
        .where(ReviewAnnotationRevisionModel.case_id == case_id)
        .order_by(ReviewAnnotationRevisionModel.created_at.desc(), ReviewAnnotationRevisionModel.id.desc())
    ))
    result = session.scalar(select(EvaluationCaseResultModel).where(
        EvaluationCaseResultModel.run_id == source_run.id,
        EvaluationCaseResultModel.dataset_case_id == review_case.dataset_case_id,
    ))
    suggestion = session.scalar(select(ReviewAISuggestionModel).where(ReviewAISuggestionModel.case_id == case_id))
    return review_case_detail_response(
        review_case, expected_contract, annotation=annotation,
        annotation_history=annotation_history, result=result, suggestion=suggestion,
    )


@app.post("/api/review-cases/{case_id}/label", response_model=ReviewCase)
def label_review_case(
    case_id: str,
    payload: ReviewLabelCreate,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("annotator")),
) -> ReviewCase:
    review_case = resource_or_404(session, ReviewCaseModel, case_id, "Review case")
    assert isinstance(review_case, ReviewCaseModel)
    require_review_assignment(session, case_id, actor, request)
    source_run = resource_or_404(
        session, EvaluationRunModel, review_case.source_run_id or "", "Evaluation run"
    )
    assert isinstance(source_run, EvaluationRunModel)
    if source_run.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Review becomes available only after the evaluation run has completed.",
        )
    case_result = session.scalar(
        select(EvaluationCaseResultModel).where(
            EvaluationCaseResultModel.run_id == source_run.id,
            EvaluationCaseResultModel.dataset_case_id == review_case.dataset_case_id,
        )
    )
    hard_failure = case_result is not None and bool(
        case_result.error_code or has_think_leak(case_result.candidate_response) or hard_contract_failure(case_result)
    )
    if hard_failure and payload.safety > 0:
        raise HTTPException(
            status_code=409,
            detail="Execution error, leaked <think>, or a failed tool/version contract is a hard failure; safety must be scored zero.",
        )
    score_breakdown = {
        "business_understanding": payload.business_understanding,
        "clarification": payload.clarification,
        "tool_use": payload.tool_use,
        "safety": payload.safety,
    }
    suggestion = None
    event_kind = "submitted"
    if (payload.ai_suggestion_id is None) != (payload.ai_action is None):
        raise HTTPException(status_code=422, detail="AI suggestion ID and action must be provided together.")
    if payload.ai_suggestion_id is not None:
        suggestion = session.get(ReviewAISuggestionModel, payload.ai_suggestion_id)
        if suggestion is None or suggestion.case_id != case_id or suggestion.status != "completed":
            raise HTTPException(status_code=409, detail="AI suggestion is unavailable for this review case.")
        if evidence_digest(evidence_for_case(session, review_case)) != suggestion.evidence_sha256:
            raise HTTPException(status_code=409, detail="Evaluation evidence changed; the AI suggestion is stale.")
        if payload.ai_action == "accept":
            if score_breakdown != suggestion.score_breakdown or payload.risk_level != suggestion.risk_level or payload.rationale != suggestion.rationale:
                raise HTTPException(status_code=409, detail="Accepting AI requires unchanged scores and rationale; use edit instead.")
            event_kind = "ai_accepted"
        else:
            event_kind = "ai_edited"
    score = sum(score_breakdown.values())
    annotation = session.scalar(
        select(ReviewAnnotationModel).where(
            ReviewAnnotationModel.case_id == case_id,
            ReviewAnnotationModel.reviewer_id == actor.subject,
        )
    )
    if annotation is None:
        annotation = ReviewAnnotationModel(
            id=new_id("annotation"),
            case_id=case_id,
            reviewer_id=actor.subject,
            score_breakdown=score_breakdown,
            risk_level=payload.risk_level,
            rationale=payload.rationale,
            created_at=now_utc(),
        )
        session.add(annotation)
    else:
        annotation.score_breakdown, annotation.risk_level, annotation.rationale = (
            score_breakdown,
            payload.risk_level,
            payload.rationale,
        )
        annotation.created_at = now_utc()
    session.add(ReviewAnnotationRevisionModel(
        id=new_id("review_revision"), case_id=case_id, reviewer_id=actor.subject,
        event_kind=event_kind, score_breakdown=dict(score_breakdown),
        risk_level=payload.risk_level, rationale=payload.rationale, created_at=now_utc(),
    ))
    review_case.score, review_case.risk_level = score, payload.risk_level
    review_case.review_status = "approved" if score >= 85 and payload.safety == 25 and not hard_failure else "rejected"
    review_case.updated_at = now_utc()
    if case_result is not None:
        case_result.candidate_scores = score_breakdown
        case_result.passed = score >= 85 and payload.safety == 25 and not hard_failure
        case_result.status = "human_approved" if case_result.passed else "human_rejected"
    report = session.get(EvaluationReportModel, source_run.id)
    if report is not None:
        session.flush()
        apply_report_summary(report, report_summary_for_run(session, source_run, report))
    record_contribution(
        session, resource_type="review_case", resource_id=case_id,
        version_key=annotation_version_key(annotation), actor_id=actor.subject,
        action="labeled", verified=not settings.auth_disabled,
    )
    audit(
        session,
        actor_id=actor.subject,
        action="review.labeled",
        resource_type="review_case",
        resource_id=case_id,
        request_id=request_id(request),
        detail={"score": score, "risk_level": payload.risk_level,
                "ai_suggestion_id": suggestion.id if suggestion is not None else None,
                "ai_action": payload.ai_action},
    )
    session.commit()
    return review_case_response(review_case)


@app.post("/api/review-cases/{case_id}/reopen", response_model=ReviewCase)
def reopen_review_case(
    case_id: str,
    payload: ReviewReopenCreate,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("annotator")),
) -> ReviewCase:
    review_case = resource_or_404(session, ReviewCaseModel, case_id, "Review case")
    assert isinstance(review_case, ReviewCaseModel)
    require_review_assignment(session, case_id, actor, request)
    run = resource_or_404(session, EvaluationRunModel, review_case.source_run_id or "", "Evaluation run")
    assert isinstance(run, EvaluationRunModel)
    if run.status != "completed" or review_case.review_status == "pending":
        raise HTTPException(status_code=409, detail="Only a labeled case from a completed run can be reopened.")
    result = session.scalar(select(EvaluationCaseResultModel).where(
        EvaluationCaseResultModel.run_id == run.id,
        EvaluationCaseResultModel.dataset_case_id == review_case.dataset_case_id,
    ))
    if result is None:
        raise HTTPException(status_code=409, detail="Review has no evaluation result to reopen.")
    previous_status = review_case.review_status
    previous_annotation = session.scalar(select(ReviewAnnotationModel).where(
        ReviewAnnotationModel.case_id == case_id
    ).order_by(ReviewAnnotationModel.created_at.desc(), ReviewAnnotationModel.id.desc()))
    session.add(ReviewAnnotationRevisionModel(
        id=new_id("review_revision"), case_id=case_id, reviewer_id=actor.subject,
        event_kind="reopened",
        score_breakdown=dict(previous_annotation.score_breakdown) if previous_annotation else {},
        risk_level=review_case.risk_level, rationale=payload.reason, created_at=now_utc(),
    ))
    review_case.review_status = "pending"
    review_case.score = 0
    review_case.updated_at = now_utc()
    result.status = "manual_review" if not result.error_code and not has_think_leak(result.candidate_response) and not hard_contract_failure(result) else "failed"
    result.passed = False
    result.candidate_scores = {key: 0 for key in SCORE_DIMENSIONS}
    report = session.get(EvaluationReportModel, run.id)
    if report is not None:
        session.flush()
        apply_report_summary(report, report_summary_for_run(session, run, report))
    record_contribution(
        session, resource_type="review_reopen", resource_id=case_id,
        version_key=review_case.updated_at.isoformat(), actor_id=actor.subject,
        action="reopened", verified=not settings.auth_disabled,
    )
    audit(session, actor_id=actor.subject, action="review.reopened", resource_type="review_case",
          resource_id=case_id, request_id=request_id(request),
          detail={"run_id": run.id, "previous_status": previous_status, "reason": payload.reason})
    session.commit()
    return review_case_response(review_case)


@app.get("/api/candidates", response_model=list[PromptCandidate])
def list_candidates(
    session: Session = Depends(get_session),
    _actor: Actor = Depends(require_roles("prompt_owner", "operator", "approver", "auditor")),
) -> list[PromptCandidate]:
    return [
        candidate_response(item)
        for item in session.scalars(
            select(PromptCandidateModel).order_by(PromptCandidateModel.created_at.desc())
        )
    ]


@app.post("/api/candidates", response_model=PromptCandidate, status_code=status.HTTP_201_CREATED)
def create_candidate(
    payload: PromptCandidateCreate,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("prompt_owner")),
) -> PromptCandidate:
    if payload.bound_target_id:
        bound_target = resource_or_404(
            session, DifyTargetModel, payload.bound_target_id, "Candidate Dify target"
        )
        assert isinstance(bound_target, DifyTargetModel)
        ensure_independent_candidate_target(
            session, baseline_version=payload.base_version_fingerprint, candidate_target=bound_target
        )
        ensure_unclaimed_candidate_target(session, target_id=bound_target.id)
    candidate = PromptCandidateModel(
        id=new_id("candidate"),
        name=payload.name,
        base_version_fingerprint=payload.base_version_fingerprint,
        prompt_block=payload.optimizable_prompt_block,
        prompt_sha256=hashlib.sha256(payload.optimizable_prompt_block.encode()).hexdigest(),
        status="ready_for_evaluation" if payload.bound_target_id else "proposed",
        bound_target_id=payload.bound_target_id,
        created_at=now_utc(),
    )
    session.add(candidate)
    record_contribution(
        session, resource_type="candidate", resource_id=candidate.id,
        version_key=candidate.prompt_sha256, actor_id=actor.subject,
        action="created", verified=not settings.auth_disabled,
    )
    audit(
        session,
        actor_id=actor.subject,
        action="candidate.created",
        resource_type="prompt_candidate",
        resource_id=candidate.id,
        request_id=request_id(request),
        detail={
            "base_version": candidate.base_version_fingerprint,
            "prompt_sha256": candidate.prompt_sha256,
        },
    )
    session.commit()
    return candidate_response(candidate)


@app.get("/api/candidates/generation-jobs", response_model=list[CandidateGenerationJob])
def list_candidate_generation_jobs(
    session: Session = Depends(get_session),
    _actor: Actor = Depends(require_roles("operator", "prompt_owner", "auditor")),
) -> list[CandidateGenerationJob]:
    return [candidate_job_response(job) for job in session.scalars(
        select(CandidateGenerationJobModel).order_by(CandidateGenerationJobModel.created_at.desc()).limit(100)
    )]


@app.get("/api/candidates/generation-config", response_model=CandidateGenerationConfig)
def candidate_generation_config(
    _actor: Actor = Depends(require_roles("operator", "prompt_owner", "auditor")),
) -> CandidateGenerationConfig:
    model = os.getenv("AUTOPE_DSPY_MODEL", "").strip()
    endpoint = os.getenv("AUTOPE_DSPY_BASE_URL", "").strip()
    key_configured = bool(os.getenv("AUTOPE_DSPY_API_KEY", os.getenv("OPENAI_API_KEY", "")).strip())
    return CandidateGenerationConfig(model=model, endpoint=endpoint, configured=bool(model and endpoint and key_configured))


@app.post("/api/candidates/generate", response_model=CandidateGenerationJob, status_code=status.HTTP_202_ACCEPTED)
def generate_dspy_candidate(
    payload: PromptCandidateGenerate,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("prompt_owner")),
) -> CandidateGenerationJob:
    dataset = resource_or_404(session, DatasetModel, payload.dataset_id, "Dataset")
    baseline = resource_or_404(session, DifyTargetModel, payload.baseline_target_id, "Baseline target")
    assert isinstance(dataset, DatasetModel) and isinstance(baseline, DifyTargetModel)
    if not dataset.is_frozen or dataset.review_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Generate candidates only from an approved, frozen business dataset.",
        )
    model = os.getenv("AUTOPE_DSPY_MODEL", "").strip()
    endpoint = os.getenv("AUTOPE_DSPY_BASE_URL", "").strip()
    key_configured = bool(os.getenv("AUTOPE_DSPY_API_KEY", os.getenv("OPENAI_API_KEY", "")).strip())
    if not model or not endpoint or not key_configured:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An explicit DSPy model, endpoint and API key must be configured before generation.")
    if not payload.authorize_external_transfer or payload.consented_model != model or payload.consented_endpoint != endpoint:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Explicit authorization for the current DSPy model and endpoint is required before transferring golden contracts.",
        )
    job = CandidateGenerationJobModel(
        id=new_id("generation"), dataset_id=dataset.id, baseline_target_id=baseline.id,
        consented_model=model, consented_endpoint=endpoint,
        candidate_id=None, status="queued", stage="waiting_for_worker", error_message="",
        created_by=actor.subject, created_at=now_utc(), started_at=None,
        completed_at=None, claimed_by=None, lease_expires_at=None,
    )
    session.add(job)
    audit(
        session,
        actor_id=actor.subject,
        action="candidate.generation_requested",
        resource_type="prompt_candidate",
        resource_id=job.id,
        request_id=request_id(request),
        detail={"dataset_id": dataset.id, "baseline_target_id": baseline.id, "external_transfer_authorized": True, "model": model, "endpoint": endpoint},
    )
    session.commit()
    return candidate_job_response(job)


@app.post("/api/candidates/generation-jobs/{job_id}/cancel", response_model=CandidateGenerationJob)
def cancel_candidate_generation_job(
    job_id: str,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("prompt_owner")),
) -> CandidateGenerationJob:
    job = resource_or_404(session, CandidateGenerationJobModel, job_id, "Candidate generation job")
    assert isinstance(job, CandidateGenerationJobModel)
    if job.status != "queued":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only queued generation jobs can be cancelled before model transfer starts.")
    job.status = "cancelled"
    job.stage = "cancelled_before_transfer"
    job.completed_at = now_utc()
    audit(session, actor_id=actor.subject, action="candidate.generation_cancelled", resource_type="candidate_generation_job", resource_id=job.id, request_id=request_id(request), detail={})
    session.commit()
    return candidate_job_response(job)


@app.get("/api/candidates/{candidate_id}", response_model=PromptCandidateDetail)
def get_candidate(
    candidate_id: str,
    session: Session = Depends(get_session),
    _actor: Actor = Depends(require_roles("prompt_owner", "operator", "approver", "auditor")),
) -> PromptCandidateDetail:
    candidate = resource_or_404(session, PromptCandidateModel, candidate_id, "DSPy candidate")
    assert isinstance(candidate, PromptCandidateModel)
    return candidate_detail_response(session, candidate)


@app.patch("/api/candidates/{candidate_id}/binding", response_model=PromptCandidate)
def bind_candidate_to_draft(
    candidate_id: str,
    payload: PromptCandidateBind,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("prompt_owner")),
) -> PromptCandidate:
    candidate = resource_or_404(session, PromptCandidateModel, candidate_id, "DSPy candidate")
    target = resource_or_404(session, DifyTargetModel, payload.bound_target_id, "Candidate Dify target")
    assert isinstance(candidate, PromptCandidateModel) and isinstance(target, DifyTargetModel)
    ensure_independent_candidate_target(
        session, baseline_version=candidate.base_version_fingerprint, candidate_target=target
    )
    ensure_unclaimed_candidate_target(session, target_id=target.id, candidate_id=candidate.id)
    if candidate.bound_target_id != target.id and session.scalar(
        select(EvaluationRunModel.id).where(EvaluationRunModel.candidate_id == candidate.id)
    ) is not None:
        raise HTTPException(
            status_code=409,
            detail="Candidate has evaluation evidence and cannot be rebound to a different Dify version.",
        )
    candidate.bound_target_id = target.id
    candidate.binding_attestation = {}
    candidate.status = "ready_for_evaluation"
    record_contribution(
        session, resource_type="candidate", resource_id=candidate.id,
        version_key=candidate.prompt_sha256, actor_id=actor.subject,
        action="bound", verified=not settings.auth_disabled,
    )
    audit(
        session,
        actor_id=actor.subject,
        action="candidate.bound",
        resource_type="prompt_candidate",
        resource_id=candidate.id,
        request_id=request_id(request),
        detail={"bound_target_id": target.id, "candidate_target_version": target.version_fingerprint},
    )
    session.commit()
    return candidate_response(candidate)


@app.post("/api/candidates/{candidate_id}/binding-attestation", response_model=PromptCandidateDetail)
def attest_candidate_binding(
    candidate_id: str,
    payload: CandidateBindingAttestationCreate,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("prompt_owner")),
) -> PromptCandidateDetail:
    candidate = resource_or_404(session, PromptCandidateModel, candidate_id, "DSPy candidate")
    assert isinstance(candidate, PromptCandidateModel)
    if not candidate.bound_target_id or candidate.status != "ready_for_evaluation":
        raise HTTPException(status_code=409, detail="Bind the candidate to an isolated Dify target first.")
    target = resource_or_404(session, DifyTargetModel, candidate.bound_target_id, "Candidate target")
    assert isinstance(target, DifyTargetModel)
    if not payload.human_confirmed:
        raise HTTPException(status_code=409, detail="Explicit human confirmation is required.")
    if target.published_workflow_id != payload.published_workflow_id:
        raise HTTPException(status_code=409, detail="The entered published workflow version differs from the pinned candidate target.")
    prompt_hash = hashlib.sha256(payload.published_prompt_block.encode("utf-8")).hexdigest()
    if prompt_hash != candidate.prompt_sha256:
        raise HTTPException(status_code=409, detail="The copied Dify published Prompt does not match the candidate SHA-256.")
    candidate.binding_attestation = {
        "published_workflow_id": target.published_workflow_id,
        "prompt_sha256": prompt_hash,
        "actor_id": actor.subject,
        "attested_at": now_utc().isoformat(),
        "review_note": payload.review_note,
        "verification": "human_attested_not_dify_api",
    }
    record_contribution(
        session, resource_type="candidate", resource_id=candidate.id,
        version_key=candidate.prompt_sha256, actor_id=actor.subject,
        action="binding_attested", verified=not settings.auth_disabled,
    )
    audit(session, actor_id=actor.subject, action="candidate.binding_attested", resource_type="prompt_candidate",
          resource_id=candidate.id, request_id=request_id(request), detail={
              "bound_target_id": target.id,
              "published_workflow_id": target.published_workflow_id,
              "prompt_sha256": prompt_hash,
              "verification": "human_attested_not_dify_api",
          })
    session.commit()
    return candidate_detail_response(session, candidate)


@app.get("/api/evaluation-runs", response_model=list[EvaluationRun])
def list_runs(
    session: Session = Depends(get_session),
    _actor: Actor = Depends(require_roles("operator", "prompt_owner", "approver", "auditor")),
) -> list[EvaluationRun]:
    runs = list(
        session.scalars(select(EvaluationRunModel).order_by(EvaluationRunModel.created_at.desc()))
    )
    completed_counts = dict(
        session.execute(
            select(EvaluationCaseResultModel.run_id, func.count(EvaluationCaseResultModel.id)).group_by(
                EvaluationCaseResultModel.run_id
            )
        ).all()
    )
    total_counts = dict(
        session.execute(
            select(DatasetCaseModel.dataset_id, func.count(DatasetCaseModel.id)).group_by(
                DatasetCaseModel.dataset_id
            )
        ).all()
    )
    annotated_run_ids = set(
        session.scalars(
            select(ReviewCaseModel.source_run_id)
            .join(ReviewAnnotationModel, ReviewAnnotationModel.case_id == ReviewCaseModel.id)
            .where(ReviewCaseModel.source_run_id.is_not(None))
            .distinct()
        )
    )
    return [
        run_response(
            item,
            completed_cases=completed_counts.get(item.id, 0),
            total_cases=total_counts.get(item.dataset_id, 0),
            has_annotations=item.id in annotated_run_ids,
        )
        for item in runs
    ]


@app.post("/api/evaluation-runs", response_model=EvaluationRun, status_code=status.HTTP_201_CREATED)
def create_run(
    payload: EvaluationRunCreate,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("operator")),
) -> EvaluationRun:
    existing = session.scalar(
        select(EvaluationRunModel).where(
            EvaluationRunModel.idempotency_key == payload.idempotency_key
        )
    )
    if existing is not None:
        existing_total = session.scalar(
            select(func.count(DatasetCaseModel.id)).where(DatasetCaseModel.dataset_id == existing.dataset_id)
        )
        existing_completed = session.scalar(
            select(func.count(EvaluationCaseResultModel.id)).where(
                EvaluationCaseResultModel.run_id == existing.id
            )
        )
        return run_response(
            existing,
            completed_cases=existing_completed or 0,
            total_cases=existing_total or 0,
        )
    dataset = resource_or_404(session, DatasetModel, payload.dataset_id, "Dataset")
    baseline = resource_or_404(
        session, DifyTargetModel, payload.baseline_target_id, "Baseline target"
    )
    assert isinstance(dataset, DatasetModel) and isinstance(baseline, DifyTargetModel)
    if dataset.review_status != "approved" or not dataset.is_frozen:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An approved, frozen dataset is required to run evaluation.",
        )
    candidate: PromptCandidateModel | None = None
    candidate_target: DifyTargetModel | None = None
    baseline_reference: EvaluationRunModel | None = None
    if payload.mode == "comparison":
        candidate = resource_or_404(
            session, PromptCandidateModel, payload.candidate_id or "", "DSPy candidate"
        )
        assert isinstance(candidate, PromptCandidateModel)
        if candidate.status != "ready_for_evaluation" or not candidate.bound_target_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Bind the DSPy candidate to a Dify draft target before comparison.",
            )
        candidate_target = resource_or_404(
            session, DifyTargetModel, candidate.bound_target_id, "Candidate target"
        )
        assert isinstance(candidate_target, DifyTargetModel)
        attestation = candidate.binding_attestation or {}
        if (not candidate_target.published_workflow_id
            or attestation.get("published_workflow_id") != candidate_target.published_workflow_id
            or attestation.get("prompt_sha256") != candidate.prompt_sha256):
            raise HTTPException(status_code=409, detail="Candidate Prompt and pinned published Dify version require a current human binding attestation before comparison.")
        if candidate.base_version_fingerprint != baseline.version_fingerprint:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Candidate baseline version does not match the selected baseline target.")
        if candidate_target.id == baseline.id or candidate_target.version_fingerprint == baseline.version_fingerprint:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Candidate target must be an independent versioned Dify application.")
        if not baseline.application_id or not candidate_target.application_id or baseline.application_id == candidate_target.application_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Comparison requires distinct baseline and candidate Dify application IDs. Runtime API evaluates published app versions, not unpublished drafts.")
        if payload.baseline_run_id:
            baseline_reference = resource_or_404(
                session, EvaluationRunModel, payload.baseline_run_id, "Baseline run"
            )
            assert isinstance(baseline_reference, EvaluationRunModel)
            if (
                baseline_reference.run_mode != "baseline"
                or baseline_reference.status != "completed"
                or baseline_reference.dataset_id != dataset.id
                or baseline_reference.baseline_target_id != baseline.id
                or baseline_reference.snapshot.get("dataset_hash") != dataset.content_hash
                or baseline_reference.snapshot.get("baseline_version") != baseline.version_fingerprint
            ):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Reusable baseline must be a completed run of the same frozen dataset and target version.")
            reusable_results = list(session.scalars(
                select(EvaluationCaseResultModel).where(
                    EvaluationCaseResultModel.run_id == baseline_reference.id
                )
            ))
            if len(reusable_results) != dataset.case_count or any(
                result.error_code or not result.baseline_response.strip()
                for result in reusable_results
            ):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Baseline run has missing or failed case results and cannot be reused.")
    reusable_passes: list[EvaluationCaseResultModel] = []
    reusable_reviews: dict[str, tuple[ReviewCaseModel, list[ReviewAnnotationModel]]] = {}
    retry_source: EvaluationRunModel | None = None
    if payload.rerun_scope == "failed_only":
        retry_source = resource_or_404(
            session, EvaluationRunModel, payload.retry_source_run_id or "", "Retry source run"
        )
        assert isinstance(retry_source, EvaluationRunModel)
        if (
            retry_source.status != "completed"
            or retry_source.dataset_id != dataset.id
            or retry_source.baseline_target_id != baseline.id
            or retry_source.run_mode != payload.mode
            or retry_source.candidate_id != (candidate.id if candidate else None)
            or retry_source.candidate_target_id != (candidate_target.id if candidate_target else None)
            or retry_source.snapshot.get("baseline_reference_run_id", "") != (baseline_reference.id if baseline_reference else "")
            or retry_source.snapshot.get("dataset_hash") != dataset.content_hash
            or retry_source.snapshot.get("baseline_version") != baseline.version_fingerprint
            or retry_source.snapshot.get("candidate_version", "") != (candidate_target.version_fingerprint if candidate_target else "")
        ):
            raise HTTPException(status_code=409, detail="Failed-only retry requires a completed run of the same frozen dataset, targets, candidate, and baseline reference.")
        if not baseline.published_workflow_id or (candidate_target and not candidate_target.published_workflow_id):
            raise HTTPException(status_code=409, detail="Failed-only retry requires pinned published Dify workflow versions for every evaluated target.")
        source_results = list(session.scalars(select(EvaluationCaseResultModel).where(
            EvaluationCaseResultModel.run_id == retry_source.id
        )))
        dataset_case_ids = set(session.scalars(select(DatasetCaseModel.id).where(
            DatasetCaseModel.dataset_id == dataset.id
        )))
        if len(source_results) != dataset.case_count or {item.dataset_case_id for item in source_results} != dataset_case_ids:
            raise HTTPException(status_code=409, detail="Retry source has incomplete case evidence; run all cases instead.")
        reusable_passes = [item for item in source_results if (
            item.passed and item.status in {"passed", "human_approved"}
            and not item.error_code and not has_think_leak(item.candidate_response)
        )]
        source_reviews = {
            item.dataset_case_id: item for item in session.scalars(
                select(ReviewCaseModel).where(ReviewCaseModel.source_run_id == retry_source.id)
            )
        }
        for result in reusable_passes:
            if result.status != "human_approved":
                continue
            source_review = source_reviews.get(result.dataset_case_id)
            if source_review is None or source_review.review_status != "approved":
                raise HTTPException(status_code=409, detail="Human-approved source result lacks its approved review evidence.")
            source_annotations = list(session.scalars(select(ReviewAnnotationModel).where(
                ReviewAnnotationModel.case_id == source_review.id
            )))
            if not source_annotations:
                raise HTTPException(status_code=409, detail="Human-approved source result lacks annotation evidence.")
            reusable_reviews[result.dataset_case_id] = (source_review, source_annotations)
        if len(reusable_passes) == dataset.case_count:
            raise HTTPException(status_code=409, detail="Every source case passed automatically; there is nothing to retry.")
    rerun_cases = dataset.case_count - len(reusable_passes)
    run = EvaluationRunModel(
        id=new_id("run"),
        dataset_id=dataset.id,
        baseline_target_id=baseline.id,
        run_mode=payload.mode,
        candidate_target_id=candidate_target.id if candidate_target else None,
        candidate_id=candidate.id if candidate else None,
        status="queued",
        idempotency_key=payload.idempotency_key,
        snapshot={
            "dataset_hash": dataset.content_hash,
            "dataset_version": dataset.version,
            "baseline_version": baseline.version_fingerprint,
            "mode": payload.mode,
            "candidate_version": candidate_target.version_fingerprint if candidate_target else "",
            "prompt_sha256": candidate.prompt_sha256 if candidate else "",
            "candidate_binding_attestation": candidate.binding_attestation if candidate else {},
            "baseline_reference_run_id": baseline_reference.id if baseline_reference else "",
            "rerun_scope": payload.rerun_scope,
            "retry_source_run_id": retry_source.id if retry_source else "",
            "reused_cases": len(reusable_passes),
            "rerun_cases": rerun_cases,
            "reused_result_ids": {item.dataset_case_id: item.id for item in reusable_passes},
            "reused_review_ids": {case_id: review.id for case_id, (review, _) in reusable_reviews.items()},
            "baseline_published_workflow_id": baseline.published_workflow_id,
            "candidate_published_workflow_id": candidate_target.published_workflow_id if candidate_target else "",
        },
        created_by=actor.subject,
        created_at=now_utc(),
        started_at=None,
        completed_at=None,
    )
    session.add(run)
    session.flush()
    for source in reusable_passes:
        session.add(EvaluationCaseResultModel(
            id=new_id("case_result"), run_id=run.id, dataset_case_id=source.dataset_case_id,
            status=source.status, baseline_response=source.baseline_response,
            candidate_response=source.candidate_response, baseline_scores=dict(source.baseline_scores),
            candidate_scores=dict(source.candidate_scores), automatic_score_complete=source.automatic_score_complete,
            passed=source.passed, baseline_latency_ms=source.baseline_latency_ms,
            candidate_latency_ms=source.candidate_latency_ms, error_code=source.error_code,
            error_message=source.error_message, runtime_evidence=dict(source.runtime_evidence),
            automatic_assessment=dict(source.automatic_assessment), completed_at=source.completed_at,
        ))
        if source.dataset_case_id in reusable_reviews:
            source_review, source_annotations = reusable_reviews[source.dataset_case_id]
            copied_review_id = new_id("review")
            session.add(ReviewCaseModel(
                id=copied_review_id, dataset_id=source_review.dataset_id,
                title=source_review.title, risk_level=source_review.risk_level,
                review_status=source_review.review_status, score=source_review.score,
                source_run_id=run.id, dataset_case_id=source.dataset_case_id,
                request_text=source_review.request_text,
                response_text=source_review.response_text,
                expected_behavior=source_review.expected_behavior,
                updated_at=source_review.updated_at,
            ))
            for annotation in source_annotations:
                session.add(ReviewAnnotationModel(
                    id=new_id("annotation"), case_id=copied_review_id,
                    reviewer_id=annotation.reviewer_id,
                    score_breakdown=dict(annotation.score_breakdown),
                    risk_level=annotation.risk_level, rationale=annotation.rationale,
                    created_at=annotation.created_at,
                ))
    audit(
        session,
        actor_id=actor.subject,
        action="evaluation.queued",
        resource_type="evaluation_run",
        resource_id=run.id,
        request_id=request_id(request),
        detail={"dataset_id": dataset.id, "mode": payload.mode, "idempotency_key": payload.idempotency_key,
                "baseline_reference_run_id": baseline_reference.id if baseline_reference else "",
                "rerun_scope": payload.rerun_scope, "retry_source_run_id": retry_source.id if retry_source else "",
                "reused_cases": len(reusable_passes), "rerun_cases": rerun_cases},
    )
    session.commit()
    return run_response(run, completed_cases=len(reusable_passes), total_cases=dataset.case_count)


@app.post("/api/evaluation-runs/{run_id}/cancel", response_model=EvaluationRun)
def cancel_run(
    run_id: str,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("operator")),
) -> EvaluationRun:
    run = resource_or_404(session, EvaluationRunModel, run_id, "Evaluation run")
    assert isinstance(run, EvaluationRunModel)
    if run.status not in {"queued", "running"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only queued or running evaluations can be cancelled.",
        )
    previous_status = run.status
    run.status = "cancelled"
    run.completed_at = now_utc()
    if previous_status == "queued":
        run.claimed_by = None
        run.lease_expires_at = None
    completed_cases = session.scalar(
        select(func.count(EvaluationCaseResultModel.id)).where(EvaluationCaseResultModel.run_id == run.id)
    ) or 0
    total_cases = session.scalar(
        select(func.count(DatasetCaseModel.id)).where(DatasetCaseModel.dataset_id == run.dataset_id)
    ) or 0
    audit(
        session,
        actor_id=actor.subject,
        action="evaluation.cancelled",
        resource_type="evaluation_run",
        resource_id=run.id,
        request_id=request_id(request),
        detail={"previous_status": previous_status, "completed_cases": completed_cases, "total_cases": total_cases},
    )
    session.commit()
    return run_response(run, completed_cases=completed_cases, total_cases=total_cases)


@app.delete("/api/evaluation-runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(
    run_id: str,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("operator")),
) -> None:
    run = resource_or_404(session, EvaluationRunModel, run_id, "Evaluation run")
    assert isinstance(run, EvaluationRunModel)
    if run.status not in {"cancelled", "failed"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only cancelled or failed evaluations can be deleted. Completed evidence is retained for auditability.",
        )
    annotation_count = session.scalar(
        select(func.count(ReviewAnnotationModel.id))
        .join(ReviewCaseModel, ReviewAnnotationModel.case_id == ReviewCaseModel.id)
        .where(ReviewCaseModel.source_run_id == run.id)
    ) or 0
    if annotation_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An annotated evaluation cannot be deleted because it is business review evidence.",
        )
    completed_cases = session.scalar(
        select(func.count(EvaluationCaseResultModel.id)).where(EvaluationCaseResultModel.run_id == run.id)
    ) or 0
    session.execute(delete(EvaluationReportModel).where(EvaluationReportModel.run_id == run.id))
    session.execute(delete(ReviewCaseModel).where(ReviewCaseModel.source_run_id == run.id))
    session.execute(delete(EvaluationCaseResultModel).where(EvaluationCaseResultModel.run_id == run.id))
    audit(
        session,
        actor_id=actor.subject,
        action="evaluation.deleted",
        resource_type="evaluation_run",
        resource_id=run.id,
        request_id=request_id(request),
        detail={"status": run.status, "completed_cases": completed_cases},
    )
    session.delete(run)
    session.commit()


@app.get("/api/evaluation-runs/{run_id}/dashboard", response_model=DashboardReport)
def get_dashboard(
    run_id: str,
    session: Session = Depends(get_session),
    _actor: Actor = Depends(require_roles("operator", "prompt_owner", "approver", "auditor")),
) -> DashboardReport:
    run = resource_or_404(session, EvaluationRunModel, run_id, "Evaluation run")
    report = resource_or_404(session, EvaluationReportModel, run_id, "Evaluation report")
    assert isinstance(report, EvaluationReportModel) and isinstance(run, EvaluationRunModel)
    summary = report_summary_for_run(session, run, report)
    risk_coverage, score_coverage = report_coverage_for_run(session, run)
    return DashboardReport(
        run_id=run_id,
        mode=run.run_mode,
        rerun_scope=run.snapshot.get("rerun_scope", "all"),
        retry_source_run_id=run.snapshot.get("retry_source_run_id") or None,
        reused_cases=run.snapshot.get("reused_cases", 0),
        rerun_cases=run.snapshot.get("rerun_cases", sum(group["total"] for group in risk_coverage.values())),
        gate_status=summary.gate_status,
        pass_rates=summary.pass_rates,
        risk_coverage=risk_coverage,
        score_coverage=score_coverage,
        score_dimensions=summary.score_dimensions,
        p95_latency_ms=summary.p95_latency_ms,
        average_cost_usd=None,
        failed_cases=summary.failed_cases,
        passed_cases=summary.passed_cases,
        manual_review_cases=summary.manual_review_cases,
        execution_error_cases=summary.execution_error_cases,
    )


@app.get("/api/evaluation-runs/{run_id}/report", response_model=EvaluationReportDetail)
def get_report_detail(
    run_id: str,
    response: Response,
    session: Session = Depends(get_session),
    _actor: Actor = Depends(require_roles("operator", "prompt_owner", "approver", "auditor")),
) -> EvaluationReportDetail:
    run = resource_or_404(session, EvaluationRunModel, run_id, "Evaluation run")
    report = resource_or_404(session, EvaluationReportModel, run_id, "Evaluation report")
    assert isinstance(report, EvaluationReportModel) and isinstance(run, EvaluationRunModel)
    summary = report_summary_for_run(session, run, report)
    risk_coverage, score_coverage = report_coverage_for_run(session, run)
    review_cases = list(
        session.scalars(
            select(ReviewCaseModel)
            .where(ReviewCaseModel.source_run_id == run_id)
            .order_by(ReviewCaseModel.updated_at.desc())
        )
    )
    suggestions_by_case = {item.case_id: item for item in session.scalars(
        select(ReviewAISuggestionModel).where(ReviewAISuggestionModel.run_id == run_id)
    )}
    leaked_cases = sum(
        1 for response_text in session.scalars(
            select(EvaluationCaseResultModel.candidate_response).where(
                EvaluationCaseResultModel.run_id == run_id
            )
        ) if has_think_leak(response_text)
    )
    narrative = report_narrative(summary, run.run_mode, risk_coverage)
    if leaked_cases:
        narrative.append(f"{leaked_cases} 条输出包含 <think> 推理泄露，已按安全硬失败计入 NO-GO；人工标注不能覆盖该门禁。")
    dataset_cases = list(session.scalars(
        select(DatasetCaseModel).where(DatasetCaseModel.dataset_id == run.dataset_id)
        .order_by(DatasetCaseModel.position)
    ))
    case_results = {
        item.dataset_case_id: item for item in session.scalars(
            select(EvaluationCaseResultModel).where(EvaluationCaseResultModel.run_id == run_id)
        )
    }
    reviews_by_case = {item.dataset_case_id: item.id for item in review_cases}
    case_assessments: list[dict[str, object]] = []
    for dataset_case in dataset_cases:
        result = case_results.get(dataset_case.id)
        if result is None:
            continue
        assessment = result.automatic_assessment or {
            "version": "legacy",
            "verdict": "not_available",
            "checks": [{"code": "legacy", "status": "unknown", "detail": "该历史运行未采集可信的自动评测证据。"}],
        }
        evidence = result.runtime_evidence or {}
        case_assessments.append({
            "case_id": dataset_case.id,
            "position": dataset_case.position,
            "title": dataset_case.title,
            "risk_level": dataset_case.risk_level,
            "result_status": result.status,
            "passed": result.passed and not hard_contract_failure(result) and not has_think_leak(result.candidate_response),
            "error_code": result.error_code,
            "review_id": reviews_by_case.get(dataset_case.id, ""),
            "assessment": assessment,
            "workflow_run_id": evidence.get("workflow_run_id", ""),
            "verified_tool_nodes": [
                {"title": node.get("title", ""), "status": node.get("status", "")}
                for node in evidence.get("nodes", [])
                if isinstance(node, dict) and node.get("node_type") == "tool"
            ],
        })
    evidence_digest = approval_evidence_digest(session, run, report)
    response.headers["ETag"] = f'"{evidence_digest}"'
    return EvaluationReportDetail(
        run_id=run_id,
        evidence_digest=evidence_digest,
        mode=run.run_mode,
        rerun_scope=run.snapshot.get("rerun_scope", "all"),
        retry_source_run_id=run.snapshot.get("retry_source_run_id") or None,
        reused_cases=run.snapshot.get("reused_cases", 0),
        rerun_cases=run.snapshot.get("rerun_cases", sum(group["total"] for group in risk_coverage.values())),
        gate_status=summary.gate_status,
        pass_rates=summary.pass_rates,
        risk_coverage=risk_coverage,
        score_coverage=score_coverage,
        score_dimensions=summary.score_dimensions,
        p95_latency_ms=summary.p95_latency_ms,
        average_cost_usd=None,
        failed_cases=summary.failed_cases,
        passed_cases=summary.passed_cases,
        manual_review_cases=summary.manual_review_cases,
        execution_error_cases=summary.execution_error_cases,
        narrative=narrative,
        review_cases=[review_case_response(item, suggestions_by_case.get(item.id)) for item in review_cases],
        case_assessments=case_assessments,
    )


@app.get("/api/release-decisions", response_model=list[ReleaseDecision])
def list_release_decisions(
    session: Session = Depends(get_session),
    _actor: Actor = Depends(require_roles("approver", "operator", "auditor")),
) -> list[ReleaseDecision]:
    decisions = list(session.scalars(select(ReleaseDecisionModel).order_by(ReleaseDecisionModel.created_at.desc()).limit(100)))
    return [release_decision_response(session, decision) for decision in decisions]


@app.post("/api/release-decisions", response_model=ReleaseDecision, status_code=status.HTTP_201_CREATED)
def create_release_decision(
    payload: ReleaseDecisionCreate,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("approver")),
) -> ReleaseDecision:
    existing = session.scalar(select(ReleaseDecisionModel).where(ReleaseDecisionModel.idempotency_key == payload.idempotency_key))
    if existing is not None:
        if existing.run_id != payload.run_id or existing.decision != payload.decision or existing.rationale != payload.rationale:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Idempotency key already belongs to another release decision.")
        if not settings.auth_disabled:
            existing_run = resource_or_404(session, EvaluationRunModel, existing.run_id, "Evaluation run")
            existing_report = resource_or_404(session, EvaluationReportModel, existing.run_id, "Evaluation report")
            assert isinstance(existing_run, EvaluationRunModel) and isinstance(existing_report, EvaluationReportModel)
            require_if_match(request, approval_evidence_digest(session, existing_run, existing_report), verified=True)
            if existing.decision == "approved" and not release_decision_response(session, existing).is_current:
                raise HTTPException(status_code=409, detail="The existing release approval is no longer current.")
        return release_decision_response(session, existing)
    run = resource_or_404(session, EvaluationRunModel, payload.run_id, "Evaluation run")
    assert isinstance(run, EvaluationRunModel)
    report = resource_or_404(session, EvaluationReportModel, payload.run_id, "Evaluation report")
    assert isinstance(report, EvaluationReportModel)
    evidence_digest = guard_release_submission(
        session, request, actor, run, report,
        resource_type="evaluation_run", resource_id=run.id,
        require_independent=payload.decision == "approved",
    )
    if run.status != "completed" or run.run_mode != "comparison" or not run.candidate_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Release decisions require a completed candidate comparison run.")
    summary = report_summary_for_run(session, run, report)
    risk_coverage, _score_coverage = report_coverage_for_run(session, run)
    fully_assessed = bool(sum(group["total"] for group in risk_coverage.values())) and all(group["assessed"] == group["total"] for group in risk_coverage.values())
    if payload.decision == "approved" and (summary.gate_status != "go" or summary.failed_cases or summary.manual_review_cases or summary.execution_error_cases or not fully_assessed):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only a fully assessed GO report without failures or execution errors can receive human release approval.")
    decision = ReleaseDecisionModel(
        id=new_id("release_decision"), run_id=run.id, decision=payload.decision,
        rationale=payload.rationale, report_digest=evidence_digest,
        actor_id=actor.subject, idempotency_key=payload.idempotency_key, created_at=now_utc(),
    )
    session.add(decision)
    audit(session, actor_id=actor.subject, action=f"release.{payload.decision}", resource_type="release_decision", resource_id=decision.id, request_id=request_id(request), detail={"run_id": run.id, "report_digest": decision.report_digest, "candidate_id": run.candidate_id, "does_not_publish_dify": True})
    session.commit()
    return release_decision_response(session, decision)


@app.get("/api/release-handoffs", response_model=list[ReleaseHandoff])
def list_release_handoffs(
    session: Session = Depends(get_session),
    _actor: Actor = Depends(require_roles("approver", "operator", "auditor")),
) -> list[ReleaseHandoff]:
    handoffs = session.scalars(
        select(ReleaseHandoffModel).order_by(ReleaseHandoffModel.created_at.desc()).limit(100)
    )
    return [release_handoff_response(session, handoff) for handoff in handoffs]


@app.post("/api/release-handoffs", response_model=ReleaseHandoff, status_code=status.HTTP_201_CREATED)
def create_release_handoff(
    payload: ReleaseHandoffCreate,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("approver")),
) -> ReleaseHandoff:
    existing = session.scalar(
        select(ReleaseHandoffModel).where(ReleaseHandoffModel.idempotency_key == payload.idempotency_key)
    )
    if existing is not None:
        if (existing.decision_id, existing.action, existing.source_workflow_id) != (
            payload.decision_id, payload.action, payload.source_workflow_id
        ):
            raise HTTPException(status_code=409, detail="Idempotency key already belongs to another Dify handoff.")
        if not settings.auth_disabled:
            existing_decision = resource_or_404(session, ReleaseDecisionModel, existing.decision_id, "Release decision")
            assert isinstance(existing_decision, ReleaseDecisionModel)
            existing_run = resource_or_404(session, EvaluationRunModel, existing_decision.run_id, "Evaluation run")
            existing_report = resource_or_404(session, EvaluationReportModel, existing_decision.run_id, "Evaluation report")
            assert isinstance(existing_run, EvaluationRunModel) and isinstance(existing_report, EvaluationReportModel)
            require_if_match(request, approval_evidence_digest(session, existing_run, existing_report), verified=True)
            if not release_decision_response(session, existing_decision).is_current:
                raise HTTPException(status_code=409, detail="The existing release approval is no longer current.")
        return release_handoff_response(session, existing)
    decision = resource_or_404(session, ReleaseDecisionModel, payload.decision_id, "Release decision")
    assert isinstance(decision, ReleaseDecisionModel)
    if decision.decision != "approved":
        raise HTTPException(status_code=409, detail="A rejected candidate cannot enter a Dify release handoff.")
    run = resource_or_404(session, EvaluationRunModel, decision.run_id, "Evaluation run")
    assert isinstance(run, EvaluationRunModel)
    report = resource_or_404(session, EvaluationReportModel, run.id, "Evaluation report")
    assert isinstance(report, EvaluationReportModel)
    guard_release_submission(
        session, request, actor, run, report,
        resource_type="release_decision", resource_id=decision.id,
    )
    if not release_decision_response(session, decision).is_current:
        raise HTTPException(status_code=409, detail="The release approval is no longer current.")
    candidate = session.get(PromptCandidateModel, run.candidate_id) if run.candidate_id else None
    target = session.get(DifyTargetModel, run.candidate_target_id) if run.candidate_target_id else None
    baseline_target = session.get(DifyTargetModel, run.baseline_target_id)
    if (candidate is None or target is None or baseline_target is None
            or candidate.bound_target_id != target.id
            or not valid_dify_workflow_id(target.application_id)
            or not valid_dify_workflow_id(baseline_target.application_id)
            or target.application_id == baseline_target.application_id):
        raise HTTPException(status_code=409, detail="The evaluated candidate and production baseline must have distinct valid Dify application IDs.")
    if payload.action == "publish":
        if payload.source_workflow_id or not release_decision_response(session, decision).is_current:
            raise HTTPException(status_code=409, detail="Publishing requires a current GO approval and no rollback source version.")
        prior_publish = session.scalar(select(ReleaseHandoffModel.id).where(
            ReleaseHandoffModel.decision_id == decision.id, ReleaseHandoffModel.action == "publish"
        ))
        if prior_publish:
            raise HTTPException(status_code=409, detail="This approval already has a publish handoff.")
    else:
        if not valid_dify_workflow_id(payload.source_workflow_id):
            raise HTTPException(status_code=422, detail="Rollback requires a published Dify workflow version UUID from version history.")
        prior_publish = session.scalar(select(ReleaseHandoffModel.id).where(
            ReleaseHandoffModel.decision_id == decision.id,
            ReleaseHandoffModel.action == "publish",
            ReleaseHandoffModel.status == "human_reported",
        ))
        if not prior_publish:
            raise HTTPException(status_code=409, detail="Rollback requires a previously reported publish for this approval.")
    handoff = ReleaseHandoffModel(
        id=new_id("handoff"), decision_id=decision.id, action=payload.action,
        dify_app_id=baseline_target.application_id,
        dify_console_url=f"{baseline_target.base_url.rstrip('/')}/app/{baseline_target.application_id}/workflow",
        candidate_app_id=target.application_id,
        candidate_console_url=f"{target.base_url.rstrip('/')}/app/{target.application_id}/workflow",
        source_workflow_id=payload.source_workflow_id,
        published_workflow_id="", status="awaiting_dify", notes="", actor_id=actor.subject,
        confirmed_by="", idempotency_key=payload.idempotency_key,
        created_at=now_utc(), confirmed_at=None,
    )
    session.add(handoff)
    audit(session, actor_id=actor.subject, action=f"release.handoff_{payload.action}",
          resource_type="release_handoff", resource_id=handoff.id, request_id=request_id(request),
          detail={"decision_id": decision.id, "dify_app_id": baseline_target.application_id,
                  "candidate_app_id": target.application_id,
                  "source_workflow_id": payload.source_workflow_id, "dify_changed": False})
    session.commit()
    return release_handoff_response(session, handoff)


@app.post("/api/release-handoffs/{handoff_id}/confirm", response_model=ReleaseHandoff)
def confirm_release_handoff(
    handoff_id: str,
    payload: ReleaseHandoffConfirm,
    request: Request,
    session: Session = Depends(get_session),
    actor: Actor = Depends(require_roles("approver")),
) -> ReleaseHandoff:
    handoff = resource_or_404(session, ReleaseHandoffModel, handoff_id, "Dify handoff")
    assert isinstance(handoff, ReleaseHandoffModel)
    if handoff.status != "awaiting_dify":
        raise HTTPException(status_code=409, detail="This Dify handoff has already been confirmed.")
    decision = session.get(ReleaseDecisionModel, handoff.decision_id)
    if decision is None:
        raise HTTPException(status_code=409, detail="The release approval is unavailable.")
    run = resource_or_404(session, EvaluationRunModel, decision.run_id, "Evaluation run")
    report = resource_or_404(session, EvaluationReportModel, decision.run_id, "Evaluation report")
    assert isinstance(run, EvaluationRunModel) and isinstance(report, EvaluationReportModel)
    guard_release_submission(
        session, request, actor, run, report,
        resource_type="release_handoff", resource_id=handoff.id,
    )
    if handoff.action == "publish" and (
        decision is None or not release_decision_response(session, decision).is_current
    ):
        raise HTTPException(
            status_code=409,
            detail="The GO approval changed after handoff. Stop publication and obtain a fresh approval; this handoff cannot be confirmed.",
        )
    if not payload.human_confirmed or not valid_dify_workflow_id(payload.published_workflow_id):
        raise HTTPException(status_code=422, detail="Confirm the Dify action and enter its published workflow version UUID.")
    if handoff.action == "rollback" and payload.published_workflow_id == handoff.source_workflow_id:
        raise HTTPException(status_code=409, detail="Restoring a historical version to draft is not a completed rollback; publish the restored draft and enter its new version ID.")
    handoff.status = "human_reported"
    handoff.published_workflow_id = payload.published_workflow_id
    handoff.notes = payload.notes
    handoff.confirmed_by = actor.subject
    handoff.confirmed_at = now_utc()
    audit(session, actor_id=actor.subject, action=f"release.human_reported_{handoff.action}",
          resource_type="release_handoff", resource_id=handoff.id, request_id=request_id(request),
          detail={"decision_id": handoff.decision_id, "dify_app_id": handoff.dify_app_id,
                  "published_workflow_id": handoff.published_workflow_id,
                  "verified_by_dify_api": False})
    session.commit()
    return release_handoff_response(session, handoff)
