from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.schemas import (
    DashboardReport,
    Dataset,
    DatasetCreate,
    DifyTarget,
    DifyTargetCreate,
    EvaluationRun,
    EvaluationRunCreate,
    PromptCandidate,
    PromptCandidateCreate,
    ReviewCase,
    ReviewLabelCreate,
    ScoreDimension,
)


def now_utc() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class EvaluationStore:
    """Development-only in-memory store; production must replace it with audited persistence."""

    def __init__(self) -> None:
        created_at = now_utc()
        self.targets: dict[str, DifyTarget] = {}
        self.datasets: dict[str, Dataset] = {}
        self.review_cases: dict[str, ReviewCase] = {}
        self.runs: dict[str, EvaluationRun] = {}
        self.candidates: dict[str, PromptCandidate] = {}
        self._seed(created_at)

    def _seed(self, created_at: datetime) -> None:
        baseline = DifyTarget(
            id="target_baseline",
            name="澄肌护肤助手 · Baseline",
            base_url="https://api.dify.example",
            runtime_path="/v1/workflows/run",
            target_kind="workflow",
            secret_ref="dify/skin/baseline",
            version_fingerprint="skin-flow@2026.10.12-baseline",
            created_at=created_at,
        )
        candidate = baseline.model_copy(
            update={
                "id": "target_candidate",
                "name": "澄肌护肤助手 · Candidate",
                "secret_ref": "dify/skin/candidate",
                "version_fingerprint": "skin-flow@candidate-9d2a",
            }
        )
        dataset = Dataset(
            id="dataset_golden_v1",
            name="护肤咨询黄金集 v1",
            description="已脱敏的咨询、澄清、工具与安全场景。",
            case_count=51,
            review_status="approved",
            created_at=created_at,
        )
        review_case = ReviewCase(
            id="case_dry_skin_01",
            dataset_id=dataset.id,
            title="干敏肌预算有限的产品推荐",
            risk_level="normal",
            review_status="pending",
            score=0,
            updated_at=created_at,
        )
        prompt_candidate = PromptCandidate(
            id="candidate_clarify_v1",
            name="澄清顺序优化 v1",
            base_version_fingerprint=baseline.version_fingerprint,
            status="ready_for_evaluation",
            created_at=created_at,
        )
        evaluation_run = EvaluationRun(
            id="run_seed_20261012",
            dataset_id=dataset.id,
            baseline_target_id=baseline.id,
            candidate_target_id=candidate.id,
            candidate_id=prompt_candidate.id,
            status="completed",
            created_at=created_at,
            completed_at=created_at,
        )
        self.targets = {baseline.id: baseline, candidate.id: candidate}
        self.datasets = {dataset.id: dataset}
        self.review_cases = {review_case.id: review_case}
        self.candidates = {prompt_candidate.id: prompt_candidate}
        self.runs = {evaluation_run.id: evaluation_run}

    def create_target(self, payload: DifyTargetCreate) -> DifyTarget:
        target = DifyTarget(
            id=new_id("target"),
            name=payload.name,
            base_url=str(payload.base_url).rstrip("/"),
            runtime_path=payload.runtime_path,
            target_kind=payload.target_kind,
            secret_ref=payload.secret_ref,
            version_fingerprint=payload.version_fingerprint,
            created_at=now_utc(),
        )
        self.targets[target.id] = target
        return target

    def create_dataset(self, payload: DatasetCreate) -> Dataset:
        dataset = Dataset(
            id=new_id("dataset"),
            name=payload.name,
            description=payload.description,
            case_count=payload.case_count,
            review_status="draft",
            created_at=now_utc(),
        )
        self.datasets[dataset.id] = dataset
        return dataset

    def label_case(self, case_id: str, payload: ReviewLabelCreate) -> ReviewCase:
        existing_case = self.review_cases[case_id]
        score = sum(
            [
                payload.business_understanding,
                payload.clarification,
                payload.tool_use,
                payload.safety,
            ]
        )
        updated_case = existing_case.model_copy(
            update={
                "risk_level": payload.risk_level,
                "review_status": "approved" if score >= 85 else "rejected",
                "score": score,
                "updated_at": now_utc(),
            }
        )
        self.review_cases[case_id] = updated_case
        return updated_case

    def create_candidate(self, payload: PromptCandidateCreate) -> PromptCandidate:
        candidate = PromptCandidate(
            id=new_id("candidate"),
            name=payload.name,
            base_version_fingerprint=payload.base_version_fingerprint,
            status="ready_for_evaluation",
            created_at=now_utc(),
        )
        self.candidates[candidate.id] = candidate
        return candidate

    def create_run(self, payload: EvaluationRunCreate) -> EvaluationRun:
        run = EvaluationRun(
            id=new_id("run"),
            dataset_id=payload.dataset_id,
            baseline_target_id=payload.baseline_target_id,
            candidate_target_id=payload.candidate_target_id,
            candidate_id=payload.candidate_id,
            status="completed",
            created_at=now_utc(),
            completed_at=now_utc(),
        )
        self.runs[run.id] = run
        return run

    def dashboard(self, run_id: str) -> DashboardReport:
        return DashboardReport(
            run_id=run_id,
            gate_status="go",
            pass_rates={"normal": 0.91, "high_risk": 1.0, "red_line": 1.0},
            score_dimensions={
                "业务理解": ScoreDimension(baseline=20.0, candidate=23.0),
                "信息澄清": ScoreDimension(baseline=19.0, candidate=22.0),
                "工具使用": ScoreDimension(baseline=23.0, candidate=24.0),
                "安全合规": ScoreDimension(baseline=25.0, candidate=25.0),
            },
            p95_latency_ms=1680,
            average_cost_usd=0.0018,
            failed_cases=2,
        )


store = EvaluationStore()

