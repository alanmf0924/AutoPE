from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class LocalAccountModel(Base):
    __tablename__ = "local_accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    login_name: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LocalRoleGrantModel(Base):
    __tablename__ = "local_role_grants"
    __table_args__ = (UniqueConstraint("account_id", "role", name="uq_local_role_grant"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("local_accounts.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LocalSessionModel(Base):
    __tablename__ = "local_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("local_accounts.id"), nullable=False, index=True
    )
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LocalOneTimeCredentialModel(Base):
    __tablename__ = "local_one_time_credentials"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(24), nullable=False)
    account_id: Mapped[str | None] = mapped_column(
        ForeignKey("local_accounts.id"), nullable=True, index=True
    )
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LocalLoginAttemptModel(Base):
    __tablename__ = "local_login_attempts"

    key_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DifyTargetModel(Base):
    __tablename__ = "dify_targets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    runtime_path: Mapped[str] = mapped_column(String(256), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    secret_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    application_id: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    input_mapping: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    dify_user: Mapped[str] = mapped_column(String(160), nullable=False, default="autope-evaluator")
    version_fingerprint: Mapped[str] = mapped_column(String(180), nullable=False)
    published_workflow_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DifyCredentialModel(Base):
    __tablename__ = "dify_credentials"

    secret_ref: Mapped[str] = mapped_column(String(120), primary_key=True)
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DatasetModel(Base):
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    case_count: Mapped[int] = mapped_column(Integer, nullable=False)
    review_status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DatasetCaseModel(Base):
    __tablename__ = "dataset_cases"
    __table_args__ = (UniqueConstraint("dataset_id", "position", name="uq_dataset_case_position"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    expected_behavior: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expected_contract: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    evaluation_rules: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewCaseModel(Base):
    __tablename__ = "review_cases"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    review_status: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("evaluation_runs.id"), nullable=True, index=True
    )
    dataset_case_id: Mapped[str | None] = mapped_column(
        ForeignKey("dataset_cases.id"), nullable=True, index=True
    )
    request_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    response_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expected_behavior: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewCaseAssignmentModel(Base):
    __tablename__ = "review_case_assignments"
    __table_args__ = (
        UniqueConstraint("case_id", "account_id", name="uq_review_case_assignment"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("review_cases.id"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("local_accounts.id"), nullable=False, index=True)
    assigned_by: Mapped[str] = mapped_column(ForeignKey("local_accounts.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EvidenceContributionModel(Base):
    __tablename__ = "evidence_contributions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version_key: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(ForeignKey("local_accounts.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewAnnotationModel(Base):
    __tablename__ = "review_annotations"
    __table_args__ = (
        UniqueConstraint("case_id", "reviewer_id", name="uq_review_annotation_reviewer"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("review_cases.id"), nullable=False, index=True)
    reviewer_id: Mapped[str] = mapped_column(String(160), nullable=False)
    score_breakdown: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewAnnotationRevisionModel(Base):
    __tablename__ = "review_annotation_revisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("review_cases.id"), nullable=False, index=True)
    reviewer_id: Mapped[str] = mapped_column(String(160), nullable=False)
    event_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    score_breakdown: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewAISuggestionModel(Base):
    __tablename__ = "review_ai_suggestions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("review_cases.id"), nullable=False, unique=True, index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    score_breakdown: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False, default=dict)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(2048), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(160), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")


class PromptCandidateModel(Base):
    __tablename__ = "prompt_candidates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_version_fingerprint: Mapped[str] = mapped_column(String(180), nullable=False)
    prompt_block: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    bound_target_id: Mapped[str | None] = mapped_column(
        ForeignKey("dify_targets.id"), nullable=True, index=True
    )
    binding_attestation: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CandidateGenerationJobModel(Base):
    __tablename__ = "candidate_generation_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False, index=True)
    baseline_target_id: Mapped[str] = mapped_column(ForeignKey("dify_targets.id"), nullable=False)
    consented_model: Mapped[str] = mapped_column(String(160), nullable=False)
    consented_endpoint: Mapped[str] = mapped_column(String(2048), nullable=False)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("prompt_candidates.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationRunModel(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False)
    baseline_target_id: Mapped[str] = mapped_column(ForeignKey("dify_targets.id"), nullable=False)
    run_mode: Mapped[str] = mapped_column(String(24), nullable=False, default="comparison")
    candidate_target_id: Mapped[str | None] = mapped_column(ForeignKey("dify_targets.id"), nullable=True)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("prompt_candidates.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")


class EvaluationCaseResultModel(Base):
    __tablename__ = "evaluation_case_results"
    __table_args__ = (
        UniqueConstraint("run_id", "dataset_case_id", name="uq_evaluation_case_result"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"), nullable=False, index=True)
    dataset_case_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_cases.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    baseline_response: Mapped[str] = mapped_column(Text, nullable=False, default="")
    candidate_response: Mapped[str] = mapped_column(Text, nullable=False, default="")
    baseline_scores: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False, default=dict)
    candidate_scores: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False, default=dict)
    automatic_score_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    baseline_latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    runtime_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    automatic_assessment: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkerHeartbeatModel(Base):
    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class EvaluationReportModel(Base):
    __tablename__ = "evaluation_reports"

    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"), primary_key=True)
    gate_status: Mapped[str] = mapped_column(String(16), nullable=False)
    pass_rates: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    score_dimensions: Mapped[dict[str, dict[str, float]]] = mapped_column(JSON, nullable=False)
    p95_latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    average_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    failed_cases: Mapped[int] = mapped_column(Integer, nullable=False)
    passed_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manual_review_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    execution_error_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AuditEventModel(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(160), nullable=False)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    identity_source: Mapped[str] = mapped_column(String(32), nullable=False, default="local_unverified")
    outcome: Mapped[str] = mapped_column(String(24), nullable=False, default="succeeded")
    actor_roles: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    object_version: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReleaseDecisionModel(Base):
    __tablename__ = "release_decisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    report_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(160), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReleaseHandoffModel(Base):
    __tablename__ = "release_handoffs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("release_decisions.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    dify_app_id: Mapped[str] = mapped_column(String(160), nullable=False)
    dify_console_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    candidate_app_id: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    candidate_console_url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    source_workflow_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    published_workflow_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    actor_id: Mapped[str] = mapped_column(String(160), nullable=False)
    confirmed_by: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
