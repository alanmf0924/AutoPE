from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator

RiskLevel = Literal["normal", "high_risk", "red_line"]
RunStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
EvaluationMode = Literal["baseline", "comparison"]


class SessionIdentity(BaseModel):
    subject: str
    display_name: str = ""
    roles: list[Literal["annotator", "operator", "prompt_owner", "approver", "auditor"]]
    is_platform_admin: bool = False
    allowed_actions: list[str] = Field(default_factory=list)
    session_state: Literal["active"] = "active"
    csrf_token: str = ""
    environment: Literal["development", "test", "shared_test", "production"]
    authentication: Literal["disabled_local", "local_session"]


class DifyTargetCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    base_url: HttpUrl
    runtime_path: str = Field(default="/v1/workflows/run", pattern=r"^/")
    target_kind: Literal["workflow", "chatflow"] = "workflow"
    application_id: str = Field(default="", max_length=160)
    input_mapping: dict[str, str] = Field(default_factory=dict)
    dify_user: str = Field(default="autope-evaluator", min_length=3, max_length=160)
    api_key: str = Field(default="", max_length=4096)
    secret_ref: str = Field(default="", max_length=120)
    version_fingerprint: str = Field(min_length=3, max_length=180)
    published_workflow_id: str = Field(default="", pattern=r"^$|^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")

    @model_validator(mode="after")
    def require_credential_source(self) -> DifyTargetCreate:
        if not self.api_key.strip() and not self.secret_ref.strip():
            raise ValueError("Provide an API key or a server-side secret reference.")
        return self


class DifyTarget(BaseModel):
    id: str
    name: str
    base_url: str
    runtime_path: str
    target_kind: Literal["workflow", "chatflow"]
    application_id: str
    input_mapping: dict[str, str]
    dify_user: str
    credential_status: Literal["stored", "external_reference"]
    version_fingerprint: str
    published_workflow_id: str
    created_at: datetime


class DifyTargetDetail(DifyTarget):
    usage_count: int
    candidate_binding_count: int
    can_delete: bool


class DifyTargetUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    base_url: HttpUrl
    runtime_path: str = Field(default="/v1/workflows/run", pattern=r"^/")
    target_kind: Literal["workflow", "chatflow"] = "workflow"
    application_id: str = Field(default="", max_length=160)
    input_mapping: dict[str, str] = Field(default_factory=dict)
    dify_user: str = Field(default="autope-evaluator", min_length=3, max_length=160)
    api_key: str = Field(default="", max_length=4096)
    version_fingerprint: str = Field(min_length=3, max_length=180)
    published_workflow_id: str = Field(default="", pattern=r"^$|^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")


class DatasetCaseInput(BaseModel):
    title: str = Field(min_length=2, max_length=240)
    prompt: str = Field(min_length=1, max_length=4000)
    expected_behavior: str = Field(default="", max_length=4000)
    expected_contract: dict[str, object] = Field(default_factory=dict)
    evaluation_rules: dict[str, dict[str, list[str]]] = Field(default_factory=dict)
    risk_level: RiskLevel = "normal"


class DatasetCase(DatasetCaseInput):
    id: str
    position: int
    created_at: datetime


class DatasetCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: str = Field(default="", max_length=500)
    case_count: int = Field(ge=1, le=500)


class DatasetCaseBulkCreate(BaseModel):
    cases: list[DatasetCaseInput] = Field(min_length=1, max_length=500)


class Dataset(BaseModel):
    id: str
    name: str
    description: str
    case_count: int
    review_status: Literal["draft", "in_review", "approved"]
    version: int
    is_frozen: bool
    created_at: datetime


class CaseAutomationReadiness(BaseModel):
    case_id: str
    title: str
    eligible: bool
    missing_dimensions: list[str]
    manual_contract_fields: list[str]


class DatasetAutomationReadiness(BaseModel):
    eligible_cases: int
    total_cases: int
    cases: list[CaseAutomationReadiness]


class DatasetDetail(Dataset):
    evidence_digest: str = ""
    cases: list[DatasetCase]
    can_edit: bool
    can_freeze: bool
    automation_readiness: DatasetAutomationReadiness


class ReviewLabelValues(BaseModel):
    business_understanding: int = Field(ge=0, le=25)
    clarification: int = Field(ge=0, le=25)
    tool_use: int = Field(ge=0, le=25)
    safety: int = Field(ge=0, le=25)
    risk_level: RiskLevel
    rationale: str = Field(min_length=8, max_length=1000)


class ReviewLabelCreate(ReviewLabelValues):
    ai_suggestion_id: str | None = Field(default=None, max_length=64)
    ai_action: Literal["accept", "edit"] | None = None


class ReviewAISuggestion(BaseModel):
    id: str
    status: Literal["queued", "running", "completed", "failed", "uncertain"]
    score_breakdown: dict[str, int]
    risk_level: RiskLevel
    rationale: str
    model: str
    requested_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    attempt_count: int
    error_message: str


class ReviewSuggestionBatchCreate(BaseModel):
    authorize_external_transfer: bool
    consented_model: str = Field(min_length=3, max_length=160)
    consented_endpoint: str = Field(min_length=8, max_length=2048)


class ReviewSuggestionBatchRetry(ReviewSuggestionBatchCreate):
    additional_authorized_calls: int = Field(ge=1, le=10)


class ReviewReopenCreate(BaseModel):
    reason: str = Field(min_length=8, max_length=1000)


class ReviewAnnotationRevision(BaseModel):
    id: str
    reviewer_id: str
    event_kind: Literal["submitted", "reopened", "legacy_snapshot", "ai_accepted", "ai_edited"]
    score_breakdown: dict[str, int]
    risk_level: RiskLevel
    rationale: str
    created_at: datetime


class ReviewCase(BaseModel):
    id: str
    dataset_id: str
    title: str
    risk_level: RiskLevel
    review_status: Literal["pending", "approved", "rejected"]
    score: int
    source_run_id: str
    updated_at: datetime
    suggestion_status: Literal["queued", "running", "completed", "failed", "uncertain"] | None = None
    suggested_score: int | None = None
    suggestion_requested_at: datetime | None = None
    suggestion_started_at: datetime | None = None
    suggestion_completed_at: datetime | None = None
    suggestion_attempt_count: int = 0


class ReviewCaseAssignmentsUpdate(BaseModel):
    account_ids: list[str] = Field(max_length=50)


class ReviewCaseDetail(ReviewCase):
    request_text: str
    response_text: str
    expected_behavior: str
    expected_contract: dict[str, object]
    baseline_response: str = ""
    candidate_response: str = ""
    error_code: str = ""
    error_message: str = ""
    annotation: ReviewLabelValues | None = None
    annotated_by: str | None = None
    annotation_history: list[ReviewAnnotationRevision] = Field(default_factory=list)
    ai_suggestion: ReviewAISuggestion | None = None
    automatic_assessment: dict[str, object] = Field(default_factory=dict)
    runtime_evidence: dict[str, object] = Field(default_factory=dict)


class EvaluationRunCreate(BaseModel):
    dataset_id: str = Field(min_length=3)
    baseline_target_id: str = Field(min_length=3)
    mode: EvaluationMode = "baseline"
    candidate_id: str | None = Field(default=None, min_length=3)
    baseline_run_id: str | None = Field(default=None, min_length=3)
    rerun_scope: Literal["all", "failed_only"] = "all"
    retry_source_run_id: str | None = Field(default=None, min_length=3)
    idempotency_key: str = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def require_candidate_for_comparison(self) -> EvaluationRunCreate:
        if self.mode == "comparison" and not self.candidate_id:
            raise ValueError("A DSPy candidate is required for comparison evaluation.")
        if self.mode == "baseline" and self.candidate_id:
            raise ValueError("Baseline evaluation cannot include a candidate.")
        if self.mode == "baseline" and self.baseline_run_id:
            raise ValueError("Baseline evaluation cannot reuse another baseline run.")
        if self.rerun_scope == "failed_only" and not self.retry_source_run_id:
            raise ValueError("Failed-only evaluation requires a completed source run.")
        if self.rerun_scope == "all" and self.retry_source_run_id:
            raise ValueError("A source run is only valid for failed-only evaluation.")
        return self


class EvaluationRun(BaseModel):
    id: str
    dataset_id: str
    baseline_target_id: str
    mode: EvaluationMode
    candidate_target_id: str | None = None
    candidate_id: str | None = None
    baseline_run_id: str | None = None
    rerun_scope: Literal["all", "failed_only"] = "all"
    retry_source_run_id: str | None = None
    reused_cases: int = 0
    rerun_cases: int = 0
    status: RunStatus
    attempt_count: int
    last_error: str
    completed_cases: int = 0
    total_cases: int = 0
    can_cancel: bool = False
    can_delete: bool = False
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ScoreDimension(BaseModel):
    baseline: float
    candidate: float


class DashboardReport(BaseModel):
    run_id: str
    mode: EvaluationMode
    rerun_scope: Literal["all", "failed_only"] = "all"
    retry_source_run_id: str | None = None
    reused_cases: int = 0
    rerun_cases: int = 0
    gate_status: Literal["go", "no_go", "pending_review"]
    pass_rates: dict[RiskLevel, float]
    risk_coverage: dict[RiskLevel, dict[str, int]]
    score_coverage: dict[str, int]
    score_dimensions: dict[str, ScoreDimension]
    p95_latency_ms: int
    average_cost_usd: float | None
    failed_cases: int
    passed_cases: int
    manual_review_cases: int
    execution_error_cases: int


class EvaluationReportDetail(DashboardReport):
    evidence_digest: str = ""
    narrative: list[str]
    review_cases: list[ReviewCase]
    case_assessments: list[dict[str, object]] = Field(default_factory=list)


class PromptCandidateCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    base_version_fingerprint: str = Field(min_length=3, max_length=180)
    optimizable_prompt_block: str = Field(min_length=20, max_length=12000)
    bound_target_id: str | None = Field(default=None, min_length=3)


class PromptCandidateBind(BaseModel):
    bound_target_id: str = Field(min_length=3)


class CandidateBindingAttestationCreate(BaseModel):
    published_workflow_id: str = Field(pattern=r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
    published_prompt_block: str = Field(min_length=20, max_length=12000)
    review_note: str = Field(min_length=8, max_length=1000)
    human_confirmed: bool = False


class PromptCandidateGenerate(BaseModel):
    dataset_id: str = Field(min_length=3)
    baseline_target_id: str = Field(min_length=3)
    authorize_external_transfer: bool = False
    consented_model: str = ""
    consented_endpoint: str = ""


class CandidateGenerationConfig(BaseModel):
    model: str
    endpoint: str
    configured: bool


class CandidateGenerationJob(BaseModel):
    id: str
    dataset_id: str
    baseline_target_id: str
    candidate_id: str | None = None
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    stage: str
    error_message: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ReleaseDecisionCreate(BaseModel):
    run_id: str = Field(min_length=3)
    decision: Literal["approved", "rejected"]
    rationale: str = Field(min_length=8, max_length=1000)
    idempotency_key: str = Field(min_length=8, max_length=128)


class ReleaseDecision(BaseModel):
    id: str
    run_id: str
    decision: Literal["approved", "rejected"]
    rationale: str
    actor_id: str
    created_at: datetime
    is_current: bool


class ReleaseHandoffCreate(BaseModel):
    decision_id: str = Field(min_length=3)
    action: Literal["publish", "rollback"]
    source_workflow_id: str = ""
    idempotency_key: str = Field(min_length=8, max_length=128)


class ReleaseHandoffConfirm(BaseModel):
    published_workflow_id: str = Field(min_length=36, max_length=36)
    notes: str = Field(min_length=8, max_length=1000)
    human_confirmed: bool = False


class ReleaseHandoff(BaseModel):
    id: str
    decision_id: str
    action: Literal["publish", "rollback"]
    dify_app_id: str
    dify_console_url: str
    candidate_app_id: str
    candidate_console_url: str
    source_workflow_id: str
    published_workflow_id: str
    status: Literal["awaiting_dify", "human_reported"]
    notes: str
    actor_id: str
    confirmed_by: str
    created_at: datetime
    confirmed_at: datetime | None
    is_decision_current: bool
    verification: Literal["not_verified_by_dify"] = "not_verified_by_dify"


class PromptCandidate(BaseModel):
    id: str
    name: str
    base_version_fingerprint: str
    prompt_sha256: str
    status: Literal["proposed", "ready_for_evaluation", "rejected"]
    bound_target_id: str | None = None
    binding_attested: bool = False
    created_at: datetime


class CandidateEvaluationLink(BaseModel):
    id: str
    dataset_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    created_at: datetime


class PromptCandidateDetail(PromptCandidate):
    prompt_block: str
    binding_attestation: dict[str, str] = Field(default_factory=dict)
    generation_job: CandidateGenerationJob | None = None
    bound_target_name: str | None = None
    bound_target_version_fingerprint: str | None = None
    evaluation_runs: list[CandidateEvaluationLink]
    evaluation_run_count: int
