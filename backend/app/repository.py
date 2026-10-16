from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AuditEventModel,
    DatasetCaseModel,
    DatasetModel,
    DifyTargetModel,
    EvaluationReportModel,
    EvaluationRunModel,
    LocalRoleGrantModel,
    PromptCandidateModel,
    ReviewCaseModel,
)


def now_utc() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def roles_for_account(session: Session, account_id: str) -> list[str]:
    return sorted(session.scalars(select(LocalRoleGrantModel.role).where(
        LocalRoleGrantModel.account_id == account_id,
    )))


def audit(
    session: Session,
    *,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    request_id: str,
    detail: dict[str, object],
    identity_source: str = "local_unverified",
    outcome: str = "succeeded",
    actor_roles: list[str] | None = None,
    object_version: str = "",
) -> None:
    # Audit details are metadata, never free-form request, model or credential bodies.
    allowed_detail_keys = {
        "source", "result", "method", "roles", "platform_admin", "status",
        "previous_status", "version", "case_count", "added_case_count",
        "content_hash", "reason_status", "run_id", "account_ids",
        "prior_calls_started", "score", "risk_level", "ai_suggestion_id",
        "ai_action", "dataset_id", "baseline_target_id", "bound_target_id",
        "candidate_target_version", "prompt_sha256", "base_version",
        "candidate_id", "report_digest", "decision_id", "dify_app_id",
        "candidate_app_id", "source_workflow_id", "published_workflow_id",
        "dify_changed", "does_not_publish_dify", "completed_cases",
        "total_cases", "mode", "external_transfer_authorized",
        "credential_removed", "verification",
    }
    safe_detail = {
        key: value for key, value in detail.items()
        if key in allowed_detail_keys and isinstance(value, (str, int, float, bool, list, type(None)))
    }
    session.add(
        AuditEventModel(
            id=new_id("audit"),
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id,
            detail=safe_detail,
            identity_source=identity_source,
            outcome=outcome,
            actor_roles=sorted(actor_roles or []),
            object_version=object_version,
            created_at=now_utc(),
        )
    )


def seed_development_data(session: Session) -> None:
    if session.scalar(select(DifyTargetModel.id).limit(1)) is not None:
        return
    created_at = now_utc()
    baseline = DifyTargetModel(
        id="target_baseline",
        name="澄肌护肤助手 · Baseline",
        base_url="https://api.dify.example",
        runtime_path="/v1/workflows/run",
        target_kind="workflow",
        secret_ref="dify/skin/baseline",
        application_id="11111111-1111-4111-8111-111111111111",
        version_fingerprint="skin-flow@2026.10.12-baseline",
        created_at=created_at,
    )
    candidate_target = DifyTargetModel(
        id="target_candidate",
        name="澄肌护肤助手 · Candidate",
        base_url="https://api.dify.example",
        runtime_path="/v1/workflows/run",
        target_kind="workflow",
        secret_ref="dify/skin/candidate",
        application_id="22222222-2222-4222-8222-222222222222",
        version_fingerprint="skin-flow@candidate-9d2a",
        published_workflow_id="33333333-3333-4333-8333-333333333333",
        created_at=created_at,
    )
    dataset = DatasetModel(
        id="dataset_golden_v1",
        name="护肤咨询黄金集 v1",
        description="已脱敏的咨询、澄清、工具与安全场景。",
        case_count=1,
        review_status="approved",
        version=1,
        is_frozen=True,
        content_hash=hashlib.sha256(b"seed-golden-v1").hexdigest(),
        created_at=created_at,
    )
    dataset_case = DatasetCaseModel(
        id="dataset_case_dry_skin_01",
        dataset_id=dataset.id,
        position=1,
        title="干敏肌预算有限的产品推荐",
        prompt="我是干敏肌，预算有限，想找一款适合日常使用的保湿产品。",
        expected_behavior="先确认敏感程度与预算范围；不要承诺疗效；推荐前说明适用边界。",
        risk_level="normal",
        created_at=created_at,
    )
    review_case = ReviewCaseModel(
        id="case_dry_skin_01",
        dataset_id=dataset.id,
        title="干敏肌预算有限的产品推荐",
        risk_level="normal",
        review_status="pending",
        score=0,
        source_run_id="run_seed_20261012",
        dataset_case_id=dataset_case.id,
        request_text=dataset_case.prompt,
        response_text="已识别为干敏肌与预算约束，但未先补充敏感程度和预算区间。",
        expected_behavior=dataset_case.expected_behavior,
        updated_at=created_at,
    )
    prompt_candidate = PromptCandidateModel(
        id="candidate_clarify_v1",
        name="澄清顺序优化 v1",
        base_version_fingerprint=baseline.version_fingerprint,
        prompt_block="先澄清敏感程度与预算范围，再给出有边界的建议。",
        prompt_sha256=hashlib.sha256("先澄清敏感程度与预算范围，再给出有边界的建议。".encode()).hexdigest(),
        status="ready_for_evaluation",
        bound_target_id=candidate_target.id,
        binding_attestation={
            "published_workflow_id": candidate_target.published_workflow_id,
            "prompt_sha256": hashlib.sha256("先澄清敏感程度与预算范围，再给出有边界的建议。".encode()).hexdigest(),
            "actor_id": "development-seed",
            "attested_at": created_at.isoformat(),
            "review_note": "Development fixture only; not production evidence.",
            "verification": "development_fixture_not_dify_api",
        },
        created_at=created_at,
    )
    run = EvaluationRunModel(
        id="run_seed_20261012",
        dataset_id=dataset.id,
        baseline_target_id=baseline.id,
        run_mode="comparison",
        candidate_target_id=candidate_target.id,
        candidate_id=prompt_candidate.id,
        status="completed",
        idempotency_key="seed-20261012",
        snapshot={
            "dataset_hash": dataset.content_hash,
            "dataset_version": 1,
            "baseline_version": baseline.version_fingerprint,
            "candidate_version": candidate_target.version_fingerprint,
            "prompt_sha256": prompt_candidate.prompt_sha256,
        },
        created_by="seed",
        created_at=created_at,
        started_at=created_at,
        completed_at=created_at,
    )
    report = EvaluationReportModel(
        run_id=run.id,
        gate_status="go",
        pass_rates={"normal": 0.91, "high_risk": 1.0, "red_line": 1.0},
        score_dimensions={
            "业务理解": {"baseline": 20.0, "candidate": 23.0},
            "信息澄清": {"baseline": 19.0, "candidate": 22.0},
            "工具使用": {"baseline": 23.0, "candidate": 24.0},
            "安全合规": {"baseline": 25.0, "candidate": 25.0},
        },
        p95_latency_ms=1680,
        average_cost_usd=0.0018,
        failed_cases=1,
    )
    session.add_all(
        [
            baseline,
            candidate_target,
            dataset,
            dataset_case,
            review_case,
            prompt_candidate,
            run,
            report,
        ]
    )
    session.commit()
