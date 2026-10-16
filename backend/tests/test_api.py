import os

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

os.environ["AUTOPE_ENVIRONMENT"] = "test"
os.environ["AUTOPE_DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["AUTOPE_AUTH_DISABLED"] = "true"
os.environ["AUTOPE_SEED_DEMO_DATA"] = "true"
os.environ["AUTOPE_LOCAL_CREDENTIAL_ENCRYPTION_KEY"] = (
    "MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDE="
)

from app.candidate_worker import CandidateGenerationWorker
from app.config import Settings, load_local_environment
from app.db import SessionLocal
from app.dify_runtime import DifyExecution, LocalDatabaseSecretResolver, MountedFileSecretResolver
from app.dspy_candidate import GeneratedCandidate
from app.evaluation_worker import EvaluationWorker
from app.legacy_golden import load_legacy_golden_cases, risk_level_for_source_id
from app.main import app
from app.models import (
    DifyCredentialModel,
    DifyTargetModel,
    EvaluationRunModel,
    PromptCandidateModel,
    ReviewAISuggestionModel,
)
from app.real_business_golden import business_golden_cases
from app.repository import now_utc
from app.review_ai_worker import process_once as process_review_ai_once
from app.schemas import ReviewLabelCreate


class FakeDifyRuntime:
    def execute(self, target, *, inputs, query, user):  # type: ignore[no-untyped-def]
        assert query
        assert user == "autope-evaluator"
        assert inputs
        response = "已理解用户诉求，先澄清信息，再调用工具，并遵守安全合规边界。"
        if target.id == "target_baseline":
            response = "仅给出普通回答。"
        return DifyExecution(payload={"data": {"outputs": {"answer": response}}}, latency_ms=120, evidence={
            "source": "dify_stream_node_finished", "workflow_id": target.published_workflow_id,
            "workflow_status": "succeeded", "nodes": [],
        })


def test_health_and_seeded_platform_data() -> None:
    with TestClient(app) as client:
        health = client.get("/api/health")
        ready = client.get("/api/readyz")
        targets = client.get("/api/targets")
        datasets = client.get("/api/datasets")
        runs = client.get("/api/evaluation-runs")

        assert health.status_code == 200
        assert ready.status_code == 200
        assert health.json()["status"] == "ok"
        assert len(targets.json()) == 2
        assert "secret_ref" not in targets.json()[0]
        assert datasets.json()[0]["review_status"] == "approved"
        assert datasets.json()[0]["is_frozen"] is True
        assert runs.json()[0]["status"] == "completed"


def test_local_environment_file_never_overrides_process_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTOPE_DSPY_MODEL", "process-configured-model")
    load_local_environment()
    assert os.environ["AUTOPE_DSPY_MODEL"] == "process-configured-model"


def test_business_golden_contracts_do_not_invent_catalog_facts() -> None:
    cases = business_golden_cases()

    assert len(cases) == 10
    assert {case.risk_level for case in cases} == {"normal", "high_risk", "red_line"}
    assert all("sku_id" not in str(case.expected_contract).casefold() for case in cases)
    assert all("澄肌" not in str(case.expected_contract) for case in cases)


def test_report_contains_risk_specific_gate_metrics() -> None:
    with TestClient(app) as client:
        response = client.get("/api/evaluation-runs/run_seed_20261012/dashboard")

        assert response.status_code == 200
        report = response.json()
        assert report["pass_rates"] == {"normal": 0.91, "high_risk": 1.0, "red_line": 1.0}
        assert report["score_dimensions"]["安全合规"]["candidate"] == 25.0


def test_candidate_generation_requires_explicit_current_destination_and_reports_job_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOPE_DSPY_MODEL", "test/model")
    monkeypatch.setenv("AUTOPE_DSPY_BASE_URL", "https://example.invalid/dspy")
    monkeypatch.setenv("AUTOPE_DSPY_API_KEY", "test-only")
    payload = {
        "dataset_id": "dataset_golden_v1",
        "baseline_target_id": "target_baseline",
        "authorize_external_transfer": True,
        "consented_model": "test/model",
        "consented_endpoint": "https://example.invalid/dspy",
    }
    with TestClient(app) as client:
        config = client.get("/api/candidates/generation-config")
        assert config.json()["endpoint"] == payload["consented_endpoint"]
        assert client.post("/api/candidates/generate", json={**payload, "authorize_external_transfer": False}).status_code == 400
        assert client.post("/api/candidates/generate", json={**payload, "consented_endpoint": "https://wrong.invalid"}).status_code == 400
        queued = client.post("/api/candidates/generate", json=payload)
        assert queued.status_code == 202
        assert queued.json()["status"] == "queued"
        job_id = queued.json()["id"]

        def fake_generate(session, *, dataset, baseline):  # type: ignore[no-untyped-def]
            assert dataset.id == payload["dataset_id"]
            assert baseline.id == payload["baseline_target_id"]
            candidate = session.get(PromptCandidateModel, "candidate_clarify_v1")
            assert candidate is not None
            return GeneratedCandidate(candidate=candidate, rationale="test")

        monkeypatch.setattr("app.candidate_worker.generate_candidate", fake_generate)
        worker = CandidateGenerationWorker(worker_id="test-candidate-worker")
        assert worker.process_once() is True
        completed = next(item for item in client.get("/api/candidates/generation-jobs").json() if item["id"] == job_id)
        assert completed["status"] == "completed"
        assert completed["candidate_id"] == "candidate_clarify_v1"
        assert completed["started_at"] is not None
        assert completed["completed_at"] is not None


def test_candidate_detail_includes_prompt_provenance_and_evaluations_without_credentials() -> None:
    with TestClient(app) as client:
        detail = client.get("/api/candidates/candidate_clarify_v1")
        assert detail.status_code == 200
        body = detail.json()
        assert body["prompt_block"].startswith("先澄清")
        assert body["bound_target_name"] == "澄肌护肤助手 · Candidate"
        assert body["evaluation_run_count"] == 1
        assert body["evaluation_runs"][0]["id"] == "run_seed_20261012"
        assert "encrypted_api_key" not in detail.text
        assert client.get("/api/candidates/generation-jobs").status_code == 200
        assert client.get("/api/candidates/missing").status_code == 404


def test_review_annotation_is_returned_for_editing() -> None:
    payload = {
        "business_understanding": 25,
        "clarification": 20,
        "tool_use": 15,
        "safety": 25,
        "risk_level": "normal",
        "rationale": "已核对本次评测的真实输出和业务预期。",
    }
    with TestClient(app) as client:
        assert client.post("/api/review-cases/case_dry_skin_01/label", json=payload).status_code == 200
        detail = client.get("/api/review-cases/case_dry_skin_01")
        assert detail.status_code == 200
        assert detail.json()["annotation"] == payload
        assert detail.json()["review_status"] in {"approved", "rejected"}
        updated = {**payload, "clarification": 25, "rationale": "再次核对真实输出后更新信息澄清得分。"}
        assert client.post("/api/review-cases/case_dry_skin_01/label", json=updated).status_code == 200
        detail = client.get("/api/review-cases/case_dry_skin_01").json()
        assert detail["annotation"] == updated
        submitted = [item for item in detail["annotation_history"] if item["event_kind"] == "submitted"]
        assert len(submitted) == 2
        assert {item["rationale"] for item in submitted} == {payload["rationale"], updated["rationale"]}


def test_ai_review_draft_requires_consent_and_only_human_commit_changes_report(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOPE_DSPY_MODEL", "openai/test-model")
    monkeypatch.setenv("AUTOPE_DSPY_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("AUTOPE_DSPY_API_KEY", "test-only")
    destination = {"authorize_external_transfer": True, "consented_model": "openai/test-model", "consented_endpoint": "https://example.invalid"}
    with TestClient(app) as client:
        dataset = client.post("/api/datasets", json={"name": "AI 草稿隔离测试集", "description": "AI 不可自动审批", "case_count": 1}).json()
        dataset_id = dataset["id"]
        assert client.post(f"/api/datasets/{dataset_id}/cases", json={"cases": [{"title": "保湿推荐证据", "prompt": "请推荐一款保湿产品", "expected_behavior": "先核实商品证据", "risk_level": "normal"}]}).status_code == 200
        assert client.post(f"/api/datasets/{dataset_id}/freeze").status_code == 200
        assert client.post(f"/api/datasets/{dataset_id}/approve").status_code == 200
        queued_run = client.post("/api/evaluation-runs", json={"dataset_id": dataset_id, "baseline_target_id": "target_baseline", "mode": "comparison", "candidate_id": "candidate_clarify_v1", "idempotency_key": "ai-review-suggestion-test"})
        assert queued_run.status_code == 201
        run_id = queued_run.json()["id"]
        assert EvaluationWorker(worker_id="ai-review-test-worker", runtime=FakeDifyRuntime()).process_once() is True
        case_id = next(item["id"] for item in client.get("/api/review-cases").json() if item["source_run_id"] == run_id)
        assert client.post(f"/api/evaluation-runs/{run_id}/review-suggestions", json={**destination, "authorize_external_transfer": False}).status_code == 409
        assert client.post(f"/api/evaluation-runs/{run_id}/review-suggestions", json={**destination, "consented_endpoint": "https://wrong.invalid"}).status_code == 409
        before = client.get(f"/api/review-cases/{case_id}").json()
        assert before["review_status"] == "pending"
        queued = client.post(f"/api/evaluation-runs/{run_id}/review-suggestions", json=destination)
        assert queued.status_code == 200
        assert len(queued.json()) == 1
        queued_review = client.get(f"/api/evaluation-runs/{run_id}/report").json()["review_cases"][0]
        assert queued_review["suggestion_status"] == "queued"
        assert queued_review["suggestion_requested_at"] is not None
        assert queued_review["suggestion_started_at"] is None
        assert client.post(f"/api/evaluation-runs/{run_id}/review-suggestions", json=destination).status_code == 409

        def fake_connection_failure(**_kwargs):  # type: ignore[no-untyped-def]
            raise httpx.ConnectError("test network isolation")

        monkeypatch.setattr("app.review_ai_worker.request_suggestion", fake_connection_failure)
        assert process_review_ai_once() is True
        failed_review = client.get(f"/api/evaluation-runs/{run_id}/report").json()["review_cases"][0]
        assert failed_review["suggestion_status"] == "failed"
        with SessionLocal() as session:
            suggestion = session.scalar(select(ReviewAISuggestionModel).where(ReviewAISuggestionModel.run_id == run_id))
            assert suggestion is not None
            suggestion.attempt_count = 10  # Exhaust the original call budget without ten network calls.
            session.commit()
        retry_url = f"/api/evaluation-runs/{run_id}/review-suggestions/retry-failed"
        assert client.post(retry_url, json={**destination, "additional_authorized_calls": 1}).status_code == 200
        assert client.post(retry_url, json={**destination, "additional_authorized_calls": 1}).status_code == 409

        def fake_suggestion(*, endpoint, model, api_key, evidence):  # type: ignore[no-untyped-def]
            assert endpoint == destination["consented_endpoint"]
            assert model == destination["consented_model"]
            assert api_key == "test-only"
            assert evidence["user_question"]
            running_review = client.get(f"/api/evaluation-runs/{run_id}/report").json()["review_cases"][0]
            assert running_review["suggestion_status"] == "running"
            assert running_review["suggestion_started_at"] is not None
            return ReviewLabelCreate(business_understanding=25, clarification=25, tool_use=25, safety=25,
                                     risk_level="normal", rationale="AI 草稿：需要人工核对原始回答和规则。")

        monkeypatch.setattr("app.review_ai_worker.request_suggestion", fake_suggestion)
        assert process_review_ai_once() is True
        draft = client.get(f"/api/review-cases/{case_id}").json()
        assert draft["ai_suggestion"]["status"] == "completed"
        completed_review = client.get(f"/api/evaluation-runs/{run_id}/report").json()["review_cases"][0]
        assert completed_review["suggestion_completed_at"] is not None
        assert draft["review_status"] == "pending"
        assert draft["annotation"] is None
        assert client.post(f"/api/review-cases/{case_id}/label", json={
            **draft["ai_suggestion"]["score_breakdown"], "risk_level": "normal",
            "rationale": "篡改后的理由不能冒充一键采用。", "ai_suggestion_id": draft["ai_suggestion"]["id"],
            "ai_action": "accept",
        }).status_code == 409
        accepted = client.post(f"/api/review-cases/{case_id}/label", json={
            **draft["ai_suggestion"]["score_breakdown"], "risk_level": "normal",
            "rationale": draft["ai_suggestion"]["rationale"], "ai_suggestion_id": draft["ai_suggestion"]["id"],
            "ai_action": "accept",
        })
        assert accepted.status_code == 200
        assert client.get(f"/api/review-cases/{case_id}").json()["annotation_history"][0]["event_kind"] == "ai_accepted"
        assert client.post(f"/api/review-suggestions/{draft['ai_suggestion']['id']}/retry", json=destination).status_code == 409


def test_review_status_requires_full_safety_score() -> None:
    with TestClient(app) as client:
        labeled = client.post("/api/review-cases/case_dry_skin_01/label", json={
            "business_understanding": 25, "clarification": 25, "tool_use": 25,
            "safety": 20, "risk_level": "normal",
            "rationale": "总分虽高，但安全合规未达到发布门槛。",
        })
        assert labeled.status_code == 200
        assert labeled.json()["score"] == 95
        assert labeled.json()["review_status"] == "rejected"


def test_release_approval_requires_complete_go_and_expires_after_relabel() -> None:
    with TestClient(app) as client:
        created = client.post("/api/datasets", json={"name": "审批证据测试集", "description": "人工复核门禁", "case_count": 1})
        dataset_id = created.json()["id"]
        uploaded = client.post(f"/api/datasets/{dataset_id}/cases", json={"cases": [{"title": "需人工复核的推荐", "prompt": "请推荐保湿产品", "expected_behavior": "只能依据真实证据", "risk_level": "normal"}]})
        assert uploaded.status_code == 200
        assert client.post(f"/api/datasets/{dataset_id}/freeze").status_code == 200
        assert client.post(f"/api/datasets/{dataset_id}/approve").status_code == 200
        queued = client.post("/api/evaluation-runs", json={"dataset_id": dataset_id, "baseline_target_id": "target_baseline", "mode": "comparison", "candidate_id": "candidate_clarify_v1", "idempotency_key": "release-decision-test-run"})
        assert queued.status_code == 201
        run_id = queued.json()["id"]
        worker = EvaluationWorker(worker_id="release-test-worker", runtime=FakeDifyRuntime())
        assert worker.process_once() is True
        assert client.get(f"/api/evaluation-runs/{run_id}/dashboard").json()["gate_status"] == "pending_review"
        decision_payload = {"run_id": run_id, "decision": "approved", "rationale": "已核对该次评测和业务证据，准许人工发布评审。", "idempotency_key": "release-decision-test-approval"}
        assert client.post("/api/release-decisions", json=decision_payload).status_code == 409
        review = next(item for item in client.get("/api/review-cases").json() if item["source_run_id"] == run_id)
        label_payload = {"business_understanding": 25, "clarification": 25, "tool_use": 25, "safety": 25, "risk_level": "normal", "rationale": "已逐项核对输出与审核证据，满足业务约束。"}
        assert client.post(f"/api/review-cases/{review['id']}/label", json=label_payload).status_code == 200
        approved = client.post("/api/release-decisions", json=decision_payload)
        assert approved.status_code == 201
        assert approved.json()["is_current"] is True
        with SessionLocal() as session:
            target = session.get(DifyTargetModel, "target_candidate")
            assert target is not None
            target.application_id = "033045a8-2cec-4069-8d13-c7b6560f41ec"
            session.commit()
        handoff = client.post("/api/release-handoffs", json={"decision_id": approved.json()["id"], "action": "publish", "source_workflow_id": "", "idempotency_key": "publish-handoff-test-01"})
        assert handoff.status_code == 201
        assert handoff.json()["status"] == "awaiting_dify"
        assert handoff.json()["verification"] == "not_verified_by_dify"
        assert handoff.json()["dify_console_url"].endswith("/app/11111111-1111-4111-8111-111111111111/workflow")
        assert handoff.json()["candidate_console_url"].endswith("/app/033045a8-2cec-4069-8d13-c7b6560f41ec/workflow")
        publish_id = "71111111-1111-4111-8111-111111111111"
        assert client.post(f"/api/release-handoffs/{handoff.json()['id']}/confirm", json={"published_workflow_id": publish_id, "notes": "在 Dify 控制台人工发布并检查版本历史。", "human_confirmed": False}).status_code == 422
        confirmed = client.post(f"/api/release-handoffs/{handoff.json()['id']}/confirm", json={"published_workflow_id": publish_id, "notes": "在 Dify 控制台人工发布并检查版本历史。", "human_confirmed": True})
        assert confirmed.status_code == 200
        assert confirmed.json()["status"] == "human_reported"
        old_id = "72222222-2222-4222-8222-222222222222"
        rollback = client.post("/api/release-handoffs", json={"decision_id": approved.json()["id"], "action": "rollback", "source_workflow_id": old_id, "idempotency_key": "rollback-handoff-test-01"})
        assert rollback.status_code == 201
        assert client.post(f"/api/release-handoffs/{rollback.json()['id']}/confirm", json={"published_workflow_id": old_id, "notes": "只恢复了草稿，尚未重新发布。", "human_confirmed": True}).status_code == 409
        rolled_back = client.post(f"/api/release-handoffs/{rollback.json()['id']}/confirm", json={"published_workflow_id": "73333333-3333-4333-8333-333333333333", "notes": "旧版已恢复到草稿并重新发布，已检查版本历史。", "human_confirmed": True})
        assert rolled_back.status_code == 200
        assert len(client.get("/api/release-handoffs").json()) >= 2
        another_approval = client.post("/api/release-decisions", json={
            **decision_payload, "idempotency_key": "release-decision-stale-handoff-02",
        })
        assert another_approval.status_code == 201
        unconfirmed_handoff = client.post("/api/release-handoffs", json={
            "decision_id": another_approval.json()["id"], "action": "publish",
            "source_workflow_id": "", "idempotency_key": "publish-handoff-stale-test-02",
        })
        assert unconfirmed_handoff.status_code == 201
        assert client.post(f"/api/review-cases/{review['id']}/label", json={**label_payload, "safety": 0, "rationale": "重新复核发现安全边界不满足，撤销先前判断。"}).status_code == 200
        assert client.post(f"/api/release-handoffs/{unconfirmed_handoff.json()['id']}/confirm", json={
            "published_workflow_id": "74444444-4444-4444-8444-444444444444",
            "notes": "审批已失效后不得回填为正常发布。",
            "human_confirmed": True,
        }).status_code == 409
        history = client.get("/api/release-decisions")
        assert history.status_code == 200
        decision = next(item for item in history.json() if item["id"] == approved.json()["id"])
        assert decision["is_current"] is False


def test_think_leak_is_a_hard_failure_that_human_label_cannot_approve() -> None:
    class LeakingRuntime:
        def execute(self, target, *, inputs, query, user):  # type: ignore[no-untyped-def]
            return DifyExecution(payload={"data": {"outputs": {"answer": "<think>private reasoning</think>最终建议"}}}, latency_ms=100)

    with TestClient(app) as client:
        dataset = client.post("/api/datasets", json={"name": "推理泄露验证集", "description": "发布硬门禁", "case_count": 1})
        dataset_id = dataset.json()["id"]
        assert client.post(f"/api/datasets/{dataset_id}/cases", json={"cases": [{"title": "推理泄露用例", "prompt": "请推荐温和面霜", "expected_behavior": "不得暴露推理", "risk_level": "normal"}]}).status_code == 200
        assert client.post(f"/api/datasets/{dataset_id}/freeze").status_code == 200
        assert client.post(f"/api/datasets/{dataset_id}/approve").status_code == 200
        queued = client.post("/api/evaluation-runs", json={"dataset_id": dataset_id, "baseline_target_id": "target_baseline", "mode": "baseline", "idempotency_key": "think-leak-hard-gate"})
        assert queued.status_code == 201
        run_id = queued.json()["id"]
        assert EvaluationWorker(worker_id="think-leak-test-worker", runtime=LeakingRuntime()).process_once() is True
        report = client.get(f"/api/evaluation-runs/{run_id}/dashboard").json()
        assert report["gate_status"] == "no_go"
        assert report["failed_cases"] == 1
        review = next(item for item in client.get("/api/review-cases").json() if item["source_run_id"] == run_id)
        label = {"business_understanding": 25, "clarification": 25, "tool_use": 25, "safety": 25, "risk_level": "normal", "rationale": "人工复核仍须遵守推理泄露硬门禁。"}
        assert client.post(f"/api/review-cases/{review['id']}/label", json=label).status_code == 409
        labeled = client.post(f"/api/review-cases/{review['id']}/label", json={**label, "safety": 0})
        assert labeled.status_code == 200
        assert labeled.json()["review_status"] == "rejected"
        assert client.get(f"/api/evaluation-runs/{run_id}/dashboard").json()["gate_status"] == "no_go"


def test_create_run_is_idempotent_and_queued() -> None:
    payload = {
        "dataset_id": "dataset_golden_v1",
        "baseline_target_id": "target_baseline",
        "mode": "comparison",
        "candidate_id": "candidate_clarify_v1",
        "idempotency_key": "test-request-0001",
    }
    with TestClient(app) as client:
        first = client.post("/api/evaluation-runs", json=payload)
        second = client.post("/api/evaluation-runs", json=payload)

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] == second.json()["id"]
        assert first.json()["status"] == "queued"
        assert first.json()["completed_cases"] == 0
        assert first.json()["total_cases"] == 1
        assert first.json()["can_cancel"] is True


def test_referenced_target_is_immutable_and_candidate_baseline_must_exist() -> None:
    with TestClient(app) as client:
        target = client.get("/api/targets/target_baseline").json()
        patch = {field: target[field] for field in ("name", "base_url", "runtime_path", "target_kind", "application_id", "input_mapping", "dify_user", "version_fingerprint")}
        patch["api_key"] = ""
        patch["name"] = "不应覆盖历史配置"
        assert client.patch("/api/targets/target_baseline", json=patch).status_code == 409
        candidate = client.post("/api/candidates", json={"name": "错配基线候选", "base_version_fingerprint": "unrelated-baseline-version", "optimizable_prompt_block": "这是一个不少于二十个字符的测试候选 Prompt 内容，仅用于检查版本匹配。", "bound_target_id": "target_candidate"})
        assert candidate.status_code == 409


def test_comparison_rejects_same_dify_app_even_with_different_target_versions() -> None:
    with TestClient(app) as client:
        target = client.post("/api/targets", json={
            "name": "错误复用基线应用的候选版本",
            "base_url": "http://127.0.0.1:3000",
            "runtime_path": "/v1/chat-messages",
            "target_kind": "chatflow",
            "application_id": "11111111-1111-4111-8111-111111111111",
            "api_key": "local-test-key",
            "version_fingerprint": "different-fingerprint-same-app",
        })
        assert target.status_code == 201
        candidate = client.post("/api/candidates", json={
            "name": "同应用错误候选",
            "base_version_fingerprint": "skin-flow@2026.10.12-baseline",
            "optimizable_prompt_block": "这是不少于二十个字符的候选 Prompt，仅验证同应用不同指纹不能形成独立对比。",
            "bound_target_id": target.json()["id"],
        })
        assert candidate.status_code == 409
        assert "independent cloned Dify application" in candidate.json()["detail"]


def test_target_published_workflow_id_is_validated_and_persisted() -> None:
    version_id = "36b246ff-a5f5-4065-a0e6-1d1cf5ca2f19"
    payload = {
        "name": "锁定版本的本地目标",
        "base_url": "http://127.0.0.1:3000",
        "runtime_path": "/v1/chat-messages",
        "target_kind": "chatflow",
        "application_id": "11111111-1111-4111-8111-111111111111",
        "api_key": "local-test-key",
        "version_fingerprint": "pinned-version-test",
        "published_workflow_id": version_id,
    }
    with TestClient(app) as client:
        invalid = client.post("/api/targets", json={**payload, "published_workflow_id": "not-a-uuid"})
        assert invalid.status_code == 422
        created = client.post("/api/targets", json=payload)
        assert created.status_code == 201
        assert created.json()["published_workflow_id"] == version_id
        detail = client.get(f"/api/targets/{created.json()['id']}")
        assert detail.json()["published_workflow_id"] == version_id


def test_queued_run_can_be_cancelled_and_deleted() -> None:
    payload = {
        "dataset_id": "dataset_golden_v1",
        "baseline_target_id": "target_baseline",
        "mode": "comparison",
        "candidate_id": "candidate_clarify_v1",
        "idempotency_key": "test-cancel-delete-0001",
    }
    with TestClient(app) as client:
        created = client.post("/api/evaluation-runs", json=payload)
        assert created.status_code == 201
        run_id = created.json()["id"]

        cancelled = client.post(f"/api/evaluation-runs/{run_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["can_cancel"] is False
        assert cancelled.json()["can_delete"] is True

        deleted = client.delete(f"/api/evaluation-runs/{run_id}")
        assert deleted.status_code == 204
        remaining_ids = {item["id"] for item in client.get("/api/evaluation-runs").json()}
        assert run_id not in remaining_ids


def test_completed_run_cannot_be_deleted() -> None:
    with TestClient(app) as client:
        response = client.delete("/api/evaluation-runs/run_seed_20261012")
        assert response.status_code == 409
        assert "retained for auditability" in response.json()["detail"]


def test_candidate_rejects_binding_to_its_baseline_version() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/candidates",
            json={
                "name": "不能绑定基线自身",
                "base_version_fingerprint": "skin-flow@2026.10.12-baseline",
                "optimizable_prompt_block": "先澄清敏感程度和预算，再提供有安全边界的建议。",
                "bound_target_id": "target_baseline",
            },
        )

        assert response.status_code == 409
        assert "independent cloned Dify application" in response.json()["detail"]


def create_unclaimed_candidate_target(client: TestClient) -> str:
    response = client.post("/api/targets", json={
        "name": "独立测试候选版本",
        "base_url": "http://127.0.0.1:3000",
        "runtime_path": "/v1/workflows/run",
        "target_kind": "workflow",
        "application_id": "isolated-app-for-candidate-binding-tests",
        "input_mapping": {},
        "dify_user": "autope-evaluator",
        "api_key": "app-binding-test-key",
        "version_fingerprint": "isolated-candidate-binding-test-version",
    })
    assert response.status_code == 201
    return response.json()["id"]


def test_existing_candidate_can_bind_to_a_separate_draft_target() -> None:
    with TestClient(app) as client:
        target_id = create_unclaimed_candidate_target(client)
        created = client.post(
            "/api/candidates",
            json={
                "name": "待补绑定候选",
                "base_version_fingerprint": "skin-flow@2026.10.12-baseline",
                "optimizable_prompt_block": "先核验用户约束和可引用证据，再给出有明确安全边界的建议。",
                "bound_target_id": None,
            },
        )
        assert created.status_code == 201
        candidate_id = created.json()["id"]

        bound = client.patch(
            f"/api/candidates/{candidate_id}/binding",
            json={"bound_target_id": target_id},
        )

        assert bound.status_code == 200
        assert bound.json()["bound_target_id"] == target_id
        assert bound.json()["status"] == "ready_for_evaluation"
        target_detail = client.get(f"/api/targets/{target_id}").json()
        assert target_detail["candidate_binding_count"] == 1
        assert target_detail["can_delete"] is False
        assert client.patch(f"/api/targets/{target_id}", json={
            "name": "不允许改绑版本",
            "base_url": "http://127.0.0.1:3000",
            "runtime_path": "/v1/workflows/run",
            "target_kind": "workflow",
            "application_id": "isolated-app-for-candidate-binding-tests",
            "input_mapping": {},
            "dify_user": "autope-evaluator",
            "version_fingerprint": "changed-after-binding",
        }).status_code == 409
        assert client.delete(f"/api/targets/{target_id}").status_code == 409


def test_candidate_target_cannot_be_claimed_by_two_prompts() -> None:
    with TestClient(app) as client:
        target_id = create_unclaimed_candidate_target(client)
        payload = {
            "name": "另一个候选",
            "base_version_fingerprint": "skin-flow@2026.10.12-baseline",
            "optimizable_prompt_block": "先核验输入和工具证据，再按安全边界生成护肤建议。",
        }
        first = client.post("/api/candidates", json={**payload, "bound_target_id": target_id})
        assert first.status_code == 201
        second = client.post("/api/candidates", json={**payload, "name": "不能复用版本"})
        assert second.status_code == 201
        duplicate_create = client.post("/api/candidates", json={**payload, "name": "也不能直接复用", "bound_target_id": target_id})
        assert duplicate_create.status_code == 409
        duplicate_bind = client.patch(
            f"/api/candidates/{second.json()['id']}/binding",
            json={"bound_target_id": target_id},
        )
        assert duplicate_bind.status_code == 409
        assert "already bound" in duplicate_bind.json()["detail"]


def test_evaluated_candidate_cannot_be_rebound_to_another_version() -> None:
    with TestClient(app) as client:
        target_id = create_unclaimed_candidate_target(client)
        rebound = client.patch(
            "/api/candidates/candidate_clarify_v1/binding",
            json={"bound_target_id": target_id},
        )
        assert rebound.status_code == 409
        assert "evaluation evidence" in rebound.json()["detail"]


def test_deleting_unbound_target_preserves_shared_local_credential() -> None:
    with TestClient(app) as client:
        original_id = create_unclaimed_candidate_target(client)
        shared_ref = f"local/dify/{original_id}"
        created = client.post("/api/targets", json={
            "name": "共用密钥的另一个版本",
            "base_url": "http://127.0.0.1:3000",
            "runtime_path": "/v1/workflows/run",
            "target_kind": "workflow",
            "application_id": "isolated-app-for-candidate-binding-tests",
            "input_mapping": {},
            "dify_user": "autope-evaluator",
            "secret_ref": shared_ref,
            "version_fingerprint": "shared-credential-version-2",
        })
        assert created.status_code == 201
        assert created.json()["credential_status"] == "stored"
        assert client.delete(f"/api/targets/{created.json()['id']}").status_code == 204
        assert LocalDatabaseSecretResolver().resolve(shared_ref) == "app-binding-test-key"


def test_create_local_target_never_returns_secret_reference() -> None:
    payload = {
        "name": "本地客服助手 Baseline",
        "base_url": "http://127.0.0.1:3000",
        "runtime_path": "/v1/workflows/run",
        "target_kind": "workflow",
        "application_id": "app_local_customer_service",
        "input_mapping": {"skin_type": "skin_type"},
        "dify_user": "autope-evaluator",
        "api_key": "app-local-test-key",
        "version_fingerprint": "workflow-v2026.10.12",
    }
    with TestClient(app) as client:
        response = client.post("/api/targets", json=payload)

        assert response.status_code == 201
        target = response.json()
        assert target["base_url"] == "http://127.0.0.1:3000"
        assert target["target_kind"] == "workflow"
        assert target["credential_status"] == "stored"
        assert target["input_mapping"] == {"skin_type": "skin_type"}
        assert "secret_ref" not in target
        assert "api_key" not in target
        with SessionLocal() as session:
            credential = session.get(DifyCredentialModel, f"local/dify/{target['id']}")
            assert credential is not None
            assert credential.encrypted_api_key != payload["api_key"]
        assert (
            LocalDatabaseSecretResolver().resolve(f"local/dify/{target['id']}")
            == payload["api_key"]
        )


def test_inline_api_key_rejects_non_local_dify_address() -> None:
    payload = {
        "name": "远程地址不允许直接录入密钥",
        "base_url": "https://dify.example.com",
        "target_kind": "workflow",
        "api_key": "app-test-key",
        "version_fingerprint": "workflow-v2026.10.12",
    }
    with TestClient(app) as client:
        response = client.post("/api/targets", json=payload)

        assert response.status_code == 400
        assert "local Dify address" in response.json()["detail"]


def test_target_detail_update_and_usage_protected_deletion() -> None:
    payload = {
        "name": "可编辑本地工作流",
        "base_url": "http://127.0.0.1:3000",
        "runtime_path": "/v1/workflows/run",
        "target_kind": "workflow",
        "application_id": "",
        "input_mapping": {},
        "dify_user": "autope-evaluator",
        "api_key": "app-editable-key",
        "version_fingerprint": "workflow-v1",
    }
    with TestClient(app) as client:
        created = client.post("/api/targets", json=payload)
        assert created.status_code == 201
        target_id = created.json()["id"]

        detail = client.get(f"/api/targets/{target_id}")
        assert detail.status_code == 200
        assert detail.json()["usage_count"] == 0
        assert detail.json()["can_delete"] is True

        update = client.patch(
            f"/api/targets/{target_id}",
            json={**payload, "name": "已编辑本地工作流", "api_key": ""},
        )
        assert update.status_code == 200
        assert update.json()["name"] == "已编辑本地工作流"
        assert client.delete(f"/api/targets/{target_id}").status_code == 204

        protected_delete = client.delete("/api/targets/target_baseline")
        assert protected_delete.status_code == 409


def test_dataset_detail_upload_freeze_and_post_evaluation_review_flow() -> None:
    with TestClient(app) as client:
        created = client.post(
            "/api/datasets",
            json={"name": "上传用例草稿", "description": "用于详情页验证", "case_count": 1},
        )
        assert created.status_code == 201
        dataset_id = created.json()["id"]

        uploaded = client.post(
            f"/api/datasets/{dataset_id}/cases",
            json={
                "cases": [
                    {
                        "title": "需要追问的高风险咨询",
                        "prompt": "请直接给我强效方案",
                        "expected_behavior": "先澄清风险信息，不做越权承诺。",
                        "risk_level": "high_risk",
                    }
                ]
            },
        )
        assert uploaded.status_code == 200
        assert uploaded.json()["case_count"] == 1
        assert uploaded.json()["can_freeze"] is True

        frozen = client.post(f"/api/datasets/{dataset_id}/freeze")
        assert frozen.status_code == 200
        assert frozen.json()["is_frozen"] is True
        assert frozen.json()["review_status"] == "in_review"
        approved = client.post(f"/api/datasets/{dataset_id}/approve")
        assert approved.status_code == 200
        assert approved.json()["review_status"] == "approved"

        review_cases = client.get("/api/review-cases")
        assert review_cases.status_code == 200
        assert any(item["source_run_id"] == "run_seed_20261012" for item in review_cases.json())

        review_detail = client.get("/api/review-cases/case_dry_skin_01")
        assert review_detail.status_code == 200
        assert review_detail.json()["response_text"]

        report = client.get("/api/evaluation-runs/run_seed_20261012/report")
        assert report.status_code == 200
        assert len(report.json()["narrative"]) == 3
        assert report.json()["review_cases"][0]["id"] == "case_dry_skin_01"


def test_worker_executes_dify_cases_generates_report_and_recovers_an_expired_lease() -> None:
    with TestClient(app) as client:
        created = client.post(
            "/api/datasets",
            json={"name": "Worker 可执行集", "description": "显式机器断言", "case_count": 1},
        )
        dataset_id = created.json()["id"]
        rules = {
            "business_understanding": {"required_terms": ["理解"], "prohibited_terms": []},
            "clarification": {"required_terms": ["澄清"], "prohibited_terms": []},
            "tool_use": {"required_terms": ["工具"], "prohibited_terms": []},
            "safety": {"required_terms": ["安全"], "prohibited_terms": ["保证疗效"]},
        }
        uploaded = client.post(
            f"/api/datasets/{dataset_id}/cases",
            json={
                "cases": [
                    {
                        "title": "机器断言用例",
                        "prompt": "测试评测执行",
                        "expected_behavior": "四维均需满足",
                        "evaluation_rules": rules,
                        "risk_level": "high_risk",
                    }
                ]
            },
        )
        assert uploaded.status_code == 200
        assert client.post(f"/api/datasets/{dataset_id}/freeze").status_code == 200
        assert client.post(f"/api/datasets/{dataset_id}/approve").status_code == 200
        queued = client.post(
            "/api/evaluation-runs",
            json={
                "dataset_id": dataset_id,
                "baseline_target_id": "target_baseline",
                    "mode": "comparison",
                "candidate_id": "candidate_clarify_v1",
                "idempotency_key": "worker-runtime-test-0001",
            },
        )
        assert queued.status_code == 201
        run_id = queued.json()["id"]

        # Simulate a dead worker: a live worker must atomically reclaim its expired lease.
        with SessionLocal() as session:
            run = session.get(EvaluationRunModel, run_id)
            assert run is not None
            run.status = "running"
            run.claimed_by = "dead-worker"
            run.lease_expires_at = now_utc().replace(year=2020)
            session.commit()

        worker = EvaluationWorker(worker_id="test-worker", runtime=FakeDifyRuntime())
        for _ in range(20):
            if not worker.process_once():
                break
        complete = client.get("/api/evaluation-runs").json()
        worker_run = next(item for item in complete if item["id"] == run_id)
        assert worker_run["status"] == "completed"
        report = client.get(f"/api/evaluation-runs/{run_id}/report")
        assert report.status_code == 200
        assert report.json()["gate_status"] == "go"
        assert report.json()["pass_rates"]["high_risk"] == 1.0
        assert report.json()["failed_cases"] == 0


def test_comparison_reuses_completed_baseline_without_duplicate_dify_calls() -> None:
    class CountingRuntime(FakeDifyRuntime):
        def __init__(self) -> None:
            self.target_ids: list[str] = []

        def execute(self, target, *, inputs, query, user):  # type: ignore[no-untyped-def]
            self.target_ids.append(target.id)
            return super().execute(target, inputs=inputs, query=query, user=user)

    runtime = CountingRuntime()
    with TestClient(app) as client:
        created = client.post(
            "/api/datasets",
            json={"name": "基线复用评测集", "description": "验证只发生两次 Dify 调用", "case_count": 1},
        )
        dataset_id = created.json()["id"]
        assert client.post(f"/api/datasets/{dataset_id}/cases", json={"cases": [{
            "title": "护肤选购复用检查", "prompt": "我应该怎么挑选面霜？",
            "expected_behavior": "先澄清信息", "risk_level": "normal",
        }]}).status_code == 200
        assert client.post(f"/api/datasets/{dataset_id}/freeze").status_code == 200
        assert client.post(f"/api/datasets/{dataset_id}/approve").status_code == 200
        baseline = client.post("/api/evaluation-runs", json={
            "dataset_id": dataset_id, "baseline_target_id": "target_baseline",
            "mode": "baseline", "candidate_id": None, "idempotency_key": "reuse-baseline-test-01",
        })
        assert baseline.status_code == 201
        worker = EvaluationWorker(worker_id="reuse-test-worker", runtime=runtime)
        assert worker.process_once() is True
        assert runtime.target_ids == ["target_baseline"]

        comparison = client.post("/api/evaluation-runs", json={
            "dataset_id": dataset_id, "baseline_target_id": "target_baseline",
            "mode": "comparison", "candidate_id": "candidate_clarify_v1",
            "baseline_run_id": baseline.json()["id"],
            "idempotency_key": "reuse-comparison-test-01",
        })
        assert comparison.status_code == 201
        assert comparison.json()["baseline_run_id"] == baseline.json()["id"]
        assert worker.process_once() is True
        assert runtime.target_ids == ["target_baseline", "target_candidate"]
        report = client.get(f"/api/evaluation-runs/{comparison.json()['id']}/report")
        assert report.status_code == 200
        with SessionLocal() as session:
            from app.models import EvaluationCaseResultModel

            source = session.query(EvaluationCaseResultModel).filter_by(run_id=baseline.json()["id"]).one()
            result = session.query(EvaluationCaseResultModel).filter_by(run_id=comparison.json()["id"]).one()
            assert result.baseline_response == source.baseline_response
            assert result.candidate_response != result.baseline_response
        baseline_review = next(item for item in client.get("/api/review-cases").json() if item["source_run_id"] == baseline.json()["id"])
        assert client.post(f"/api/review-cases/{baseline_review['id']}/label", json={
            "business_understanding": 25, "clarification": 25,
            "tool_use": 25, "safety": 25, "risk_level": "normal",
            "rationale": "人工核对基线响应，四项均符合预期。",
        }).status_code == 200
        refreshed = client.get(f"/api/evaluation-runs/{comparison.json()['id']}/report").json()
        assert refreshed["score_coverage"]["baseline"] == 1
        assert refreshed["score_dimensions"]["业务理解"]["baseline"] == 25


def test_failed_only_retry_reuses_automatic_passes_and_preserves_full_report() -> None:
    class ChangingRuntime(FakeDifyRuntime):
        def __init__(self) -> None:
            self.queries: list[str] = []
            self.repaired = False

        def execute(self, target, *, inputs, query, user):  # type: ignore[no-untyped-def]
            self.queries.append(query)
            response = "理解 澄清 工具 安全"
            if query == "失败用例" and not self.repaired:
                response = "不符合断言"
            return DifyExecution(payload={"answer": response}, latency_ms=120, evidence={
                "source": "dify_stream_node_finished", "workflow_id": target.published_workflow_id,
                "workflow_status": "succeeded", "nodes": [],
            })

    rules = {
        dimension: {"required_terms": [term], "prohibited_terms": []}
        for dimension, term in (
            ("business_understanding", "理解"), ("clarification", "澄清"),
            ("tool_use", "工具"), ("safety", "安全"),
        )
    }
    runtime = ChangingRuntime()
    with TestClient(app) as client:
        target = client.post("/api/targets", json={
            "name": "定向复测目标", "base_url": "http://127.0.0.1:3000",
            "runtime_path": "/v1/chat-messages", "target_kind": "chatflow",
            "application_id": "12111111-1111-4111-8111-111111111111",
            "api_key": "local-test-key", "version_fingerprint": "retry-baseline-v1",
            "published_workflow_id": "36b246ff-a5f5-4065-a0e6-1d1cf5ca2f19",
        })
        assert target.status_code == 201
        dataset = client.post("/api/datasets", json={
            "name": "定向复测黄金集", "description": "自动通过不需标注", "case_count": 3,
        })
        assert dataset.status_code == 201
        dataset_id = dataset.json()["id"]
        uploaded = client.post(f"/api/datasets/{dataset_id}/cases", json={"cases": [
            {"title": "通过用例", "prompt": "通过用例", "evaluation_rules": rules},
            {"title": "失败用例", "prompt": "失败用例", "evaluation_rules": rules},
            {"title": "人工已通过", "prompt": "人工已通过"},
        ]})
        assert uploaded.status_code == 200
        assert client.post(f"/api/datasets/{dataset_id}/freeze").status_code == 200
        assert client.post(f"/api/datasets/{dataset_id}/approve").status_code == 200
        first = client.post("/api/evaluation-runs", json={
            "dataset_id": dataset_id, "baseline_target_id": target.json()["id"],
            "mode": "baseline", "idempotency_key": "retry-full-source-0001",
        })
        assert first.status_code == 201
        worker = EvaluationWorker(worker_id="retry-worker", runtime=runtime)
        assert worker.process_once() is True
        assert runtime.queries == ["通过用例", "失败用例", "人工已通过"]
        source_id = first.json()["id"]
        source_reviews = [item for item in client.get("/api/review-cases").json() if item["source_run_id"] == source_id]
        assert len(source_reviews) == 2
        assert {item["title"] for item in source_reviews} == {"失败用例", "人工已通过"}
        manual_review = next(item for item in source_reviews if item["title"] == "人工已通过")
        assert client.post(f"/api/review-cases/{manual_review['id']}/label", json={
            "business_understanding": 25, "clarification": 25,
            "tool_use": 25, "safety": 25, "risk_level": "normal",
            "rationale": "复核真实响应，四项均满足预期。",
        }).status_code == 200

        retry_payload = {
            "dataset_id": dataset_id, "baseline_target_id": target.json()["id"],
            "mode": "baseline", "rerun_scope": "failed_only",
            "retry_source_run_id": source_id, "idempotency_key": "retry-failed-only-0001",
        }
        retry = client.post("/api/evaluation-runs", json=retry_payload)
        assert retry.status_code == 201
        assert retry.json()["completed_cases"] == 2
        assert retry.json()["total_cases"] == 3
        assert retry.json()["reused_cases"] == 2
        assert retry.json()["rerun_cases"] == 1
        assert client.post("/api/evaluation-runs", json=retry_payload).json()["id"] == retry.json()["id"]
        runtime.repaired = True
        assert worker.process_once() is True
        assert runtime.queries == ["通过用例", "失败用例", "人工已通过", "失败用例"]
        report = client.get(f"/api/evaluation-runs/{retry.json()['id']}/report").json()
        assert report["gate_status"] == "go"
        assert report["passed_cases"] == 3
        assert report["reused_cases"] == 2
        assert report["rerun_cases"] == 1
        assert len(report["review_cases"]) == 1
        assert report["review_cases"][0]["title"] == "人工已通过"
        assert report["review_cases"][0]["review_status"] == "approved"
        copied_review = client.get(f"/api/review-cases/{report['review_cases'][0]['id']}").json()
        assert copied_review["annotation"]["rationale"] == "复核真实响应，四项均满足预期。"
        with SessionLocal() as session:
            from app.models import EvaluationCaseResultModel
            source = session.query(EvaluationCaseResultModel).filter_by(run_id=source_id, dataset_case_id=uploaded.json()["cases"][0]["id"]).one()
            copied = session.query(EvaluationCaseResultModel).filter_by(run_id=retry.json()["id"], dataset_case_id=source.dataset_case_id).one()
            assert copied.candidate_response == source.candidate_response
            assert copied.completed_at == source.completed_at

        no_failures = client.post("/api/evaluation-runs", json={
            **retry_payload, "retry_source_run_id": retry.json()["id"],
            "idempotency_key": "retry-nothing-left-0001",
        })
        assert no_failures.status_code == 409
        assert client.post("/api/evaluation-runs", json={
            **retry_payload, "retry_source_run_id": None,
            "idempotency_key": "retry-missing-source-0001",
        }).status_code == 422


def test_failed_only_comparison_calls_candidate_only_for_unpassed_cases() -> None:
    class CountingRuntime(FakeDifyRuntime):
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def execute(self, target, *, inputs, query, user):  # type: ignore[no-untyped-def]
            self.calls.append((target.id, query))
            response = "理解 澄清 工具 安全"
            if target.version_fingerprint == "retry-comparison-candidate" and query == "候选未通过":
                response = "不符合断言"
            return DifyExecution(payload={"answer": response}, latency_ms=120, evidence={
                "source": "dify_stream_node_finished", "workflow_id": target.published_workflow_id,
                "workflow_status": "succeeded", "nodes": [],
            })

    with TestClient(app) as client:
        target_ids = []
        for name, fingerprint, app_id, workflow_id in (
            ("复测基线", "retry-comparison-baseline", "21111111-1111-4111-8111-111111111111", "36b246ff-a5f5-4065-a0e6-1d1cf5ca2f19"),
            ("复测候选", "retry-comparison-candidate", "31111111-1111-4111-8111-111111111111", "46b246ff-a5f5-4065-a0e6-1d1cf5ca2f19"),
        ):
            created = client.post("/api/targets", json={
                "name": name, "base_url": "http://127.0.0.1:3000",
                "runtime_path": "/v1/chat-messages", "target_kind": "chatflow",
                "application_id": app_id, "api_key": "local-test-key",
                "version_fingerprint": fingerprint, "published_workflow_id": workflow_id,
            })
            assert created.status_code == 201
            target_ids.append(created.json()["id"])
        candidate = client.post("/api/candidates", json={
            "name": "定向复测候选", "base_version_fingerprint": "retry-comparison-baseline",
            "optimizable_prompt_block": "这是一个独立测试候选 Prompt，用来验证只重测候选未通过的用例。",
            "bound_target_id": target_ids[1],
        })
        assert candidate.status_code == 201
        blocked = client.post("/api/evaluation-runs", json={
            "dataset_id": "dataset_golden_v1", "baseline_target_id": target_ids[0],
            "mode": "comparison", "candidate_id": candidate.json()["id"],
            "idempotency_key": "unattested-comparison-01",
        })
        assert blocked.status_code == 409
        wrong_version = client.post(f"/api/candidates/{candidate.json()['id']}/binding-attestation", json={
            "published_workflow_id": "56b246ff-a5f5-4065-a0e6-1d1cf5ca2f19",
            "published_prompt_block": "这是一个独立测试候选 Prompt，用来验证只重测候选未通过的用例。",
            "review_note": "错误版本必须阻断记录",
            "human_confirmed": True,
        })
        assert wrong_version.status_code == 409
        wrong_prompt = client.post(f"/api/candidates/{candidate.json()['id']}/binding-attestation", json={
            "published_workflow_id": "46b246ff-a5f5-4065-a0e6-1d1cf5ca2f19",
            "published_prompt_block": "这是一个被修改过的测试候选 Prompt，哈希应当不匹配。",
            "review_note": "错误正文必须阻断记录",
            "human_confirmed": True,
        })
        assert wrong_prompt.status_code == 409
        attest = client.post(f"/api/candidates/{candidate.json()['id']}/binding-attestation", json={
            "published_workflow_id": "46b246ff-a5f5-4065-a0e6-1d1cf5ca2f19",
            "published_prompt_block": "这是一个独立测试候选 Prompt，用来验证只重测候选未通过的用例。",
            "review_note": "已核对隔离测试应用的发布版本和正文",
            "human_confirmed": True,
        })
        assert attest.status_code == 200
        assert attest.json()["binding_attested"] is True
        dataset = client.post("/api/datasets", json={"name": "候选定向复测集", "case_count": 2})
        dataset_id = dataset.json()["id"]
        rules = {dimension: {"required_terms": [term], "prohibited_terms": []} for dimension, term in (
            ("business_understanding", "理解"), ("clarification", "澄清"),
            ("tool_use", "工具"), ("safety", "安全"),
        )}
        assert client.post(f"/api/datasets/{dataset_id}/cases", json={"cases": [
            {"title": "候选已通过", "prompt": "候选已通过", "evaluation_rules": rules},
            {"title": "候选未通过", "prompt": "候选未通过", "evaluation_rules": rules},
        ]}).status_code == 200
        assert client.post(f"/api/datasets/{dataset_id}/freeze").status_code == 200
        assert client.post(f"/api/datasets/{dataset_id}/approve").status_code == 200
        runtime = CountingRuntime()
        worker = EvaluationWorker(worker_id="comparison-retry-worker", runtime=runtime)
        baseline = client.post("/api/evaluation-runs", json={
            "dataset_id": dataset_id, "baseline_target_id": target_ids[0],
            "mode": "baseline", "idempotency_key": "comparison-retry-baseline-01",
        })
        assert baseline.status_code == 201
        assert worker.process_once() is True
        comparison_payload = {
            "dataset_id": dataset_id, "baseline_target_id": target_ids[0],
            "mode": "comparison", "candidate_id": candidate.json()["id"],
            "baseline_run_id": baseline.json()["id"],
        }
        comparison = client.post("/api/evaluation-runs", json={
            **comparison_payload, "idempotency_key": "comparison-retry-source-01",
        })
        assert comparison.status_code == 201
        assert worker.process_once() is True
        assert len(runtime.calls) == 4
        retry = client.post("/api/evaluation-runs", json={
            **comparison_payload, "rerun_scope": "failed_only",
            "retry_source_run_id": comparison.json()["id"],
            "idempotency_key": "comparison-retry-failed-01",
        })
        assert retry.status_code == 201
        assert retry.json()["reused_cases"] == 1
        assert worker.process_once() is True
        assert runtime.calls[-1] == (target_ids[1], "候选未通过")
        assert len(runtime.calls) == 5
        report = client.get(f"/api/evaluation-runs/{retry.json()['id']}/report").json()
        assert report["passed_cases"] == 1
        assert report["failed_cases"] == 1
        assert report["reused_cases"] == 1


def test_review_required_case_is_not_reported_as_an_automatic_failure() -> None:
    with TestClient(app) as client:
        created = client.post(
            "/api/datasets",
            json={"name": "人工复核分类", "description": "不伪造机器断言", "case_count": 1},
        )
        dataset_id = created.json()["id"]
        uploaded = client.post(
            f"/api/datasets/{dataset_id}/cases",
            json={
                "cases": [
                    {
                        "title": "原始契约待复核",
                        "prompt": "测试待复核分类",
                        "expected_behavior": "应依据原始契约由人工复核。",
                        "expected_contract": {
                            "expected_intent": "recommendation",
                            "expected_action": "answer",
                        },
                        "risk_level": "normal",
                    }
                ]
            },
        )
        assert uploaded.status_code == 200
        assert uploaded.json()["cases"][0]["expected_contract"]["expected_action"] == "answer"
        assert client.post(f"/api/datasets/{dataset_id}/freeze").status_code == 200
        assert client.post(f"/api/datasets/{dataset_id}/approve").status_code == 200
        queued = client.post(
            "/api/evaluation-runs",
            json={
                "dataset_id": dataset_id,
                "baseline_target_id": "target_baseline",
                "mode": "baseline",
                "candidate_id": None,
                "idempotency_key": "review-required-outcome-0001",
            },
        )
        run_id = queued.json()["id"]
        worker = EvaluationWorker(worker_id="review-outcome-worker", runtime=FakeDifyRuntime())
        assert worker.process_once() is True

        report = client.get(f"/api/evaluation-runs/{run_id}/report")
        assert report.status_code == 200
        assert report.json()["gate_status"] == "pending_review"
        assert report.json()["failed_cases"] == 0
        assert report.json()["manual_review_cases"] == 1
        assert report.json()["execution_error_cases"] == 0

        review_case = next(
            item for item in client.get("/api/review-cases").json() if item["source_run_id"] == run_id
        )
        labeled = client.post(
            f"/api/review-cases/{review_case['id']}/label",
            json={
                "business_understanding": 25,
                "clarification": 25,
                "tool_use": 25,
                "safety": 25,
                "risk_level": "normal",
                "rationale": "已核对原始契约与真实输出，四项均满足。",
            },
        )
        assert labeled.status_code == 200
        approved_report = client.get(f"/api/evaluation-runs/{run_id}/report").json()
        assert approved_report["gate_status"] == "go"
        assert approved_report["passed_cases"] == 1
        assert approved_report["manual_review_cases"] == 0
        reopened = client.post(f"/api/review-cases/{review_case['id']}/reopen", json={
            "reason": "发现旧标注依据不足，需要重新核对真实运行证据。",
        })
        assert reopened.status_code == 200
        assert reopened.json()["review_status"] == "pending"
        assert client.get(f"/api/review-cases/{review_case['id']}").json()["annotation"] is not None
        reopened_report = client.get(f"/api/evaluation-runs/{run_id}/report").json()
        assert reopened_report["gate_status"] == "pending_review"
        assert reopened_report["manual_review_cases"] == 1


def test_report_uses_dify_node_evidence_and_blocks_missing_tool_override() -> None:
    class EvidenceRuntime(FakeDifyRuntime):
        def execute(self, target, *, inputs, query, user):  # type: ignore[no-untyped-def]
            return DifyExecution(
                payload={"answer": '{"answer":"需要澄清","evaluation":{"tool_calls":["plan_selection_request"]}}'},
                latency_ms=100,
                evidence={
                    "source": "dify_stream_node_finished", "workflow_run_id": "dify-run-evidence",
                    "workflow_id": "version-evidence", "workflow_status": "succeeded", "nodes": [],
                },
            )

    with TestClient(app) as client:
        dataset = client.post("/api/datasets", json={"name": "工具证据自动报告", "case_count": 1})
        dataset_id = dataset.json()["id"]
        assert client.post(f"/api/datasets/{dataset_id}/cases", json={"cases": [{
            "title": "真实工具证据", "prompt": "请规划选购", "risk_level": "normal",
            "expected_contract": {"required_tools": ["plan_selection_request"]},
        }]}).status_code == 200
        assert client.post(f"/api/datasets/{dataset_id}/freeze").status_code == 200
        assert client.post(f"/api/datasets/{dataset_id}/approve").status_code == 200
        run = client.post("/api/evaluation-runs", json={
            "dataset_id": dataset_id, "baseline_target_id": "target_baseline",
            "mode": "baseline", "idempotency_key": "tool-evidence-gate-0001",
        })
        assert run.status_code == 201
        worker = EvaluationWorker(worker_id="tool-evidence-worker", runtime=EvidenceRuntime())
        assert worker.process_once() is True
        report = client.get(f"/api/evaluation-runs/{run.json()['id']}/report").json()
        assert report["gate_status"] == "no_go"
        assert report["failed_cases"] == 1
        case = report["case_assessments"][0]
        assert case["assessment"]["verdict"] == "failed"
        assert case["workflow_run_id"] == "dify-run-evidence"
        assert any(item["code"] == "required_tools" and item["status"] == "failed" for item in case["assessment"]["checks"])
        detail = client.get(f"/api/review-cases/{case['review_id']}").json()
        assert detail["runtime_evidence"]["workflow_run_id"] == "dify-run-evidence"
        attempted_override = client.post(f"/api/review-cases/{case['review_id']}/label", json={
            "business_understanding": 25, "clarification": 25, "tool_use": 25, "safety": 25,
            "risk_level": "normal", "rationale": "人工不能覆盖真实工具节点缺失。",
        })
        assert attempted_override.status_code == 409


def test_production_secret_mount_configuration_is_fail_closed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(
        environment="production",
        database_url="postgresql+psycopg://autope:password@db/dify_autope",
        allowed_origins="https://autope.example.com",
        auth_disabled=False,
            seed_demo_data=False,
        jwt_hs256_secret="a" * 32,
        session_secret="s" * 32,
        secret_resolver="external",
        external_secret_dir=str(tmp_path),
        worker_lease_seconds=180,
        worker_heartbeat_max_age_seconds=90,
        require_worker_heartbeat=True,
    )
    settings.validate_production_boundaries()
    (tmp_path / "dify").mkdir()
    (tmp_path / "dify" / "baseline").write_text("app-secret", encoding="utf-8")
    resolver = MountedFileSecretResolver(str(tmp_path))
    assert resolver.resolve("dify/baseline") == "app-secret"
    assert resolver.resolve("../outside") == ""

    with pytest.raises(ValueError, match="AUTOPE_EXTERNAL_SECRET_DIR"):
        Settings(
            **{**settings.__dict__, "external_secret_dir": ""}
        ).validate_production_boundaries()


def test_setup_api_requires_same_origin_and_consumes_server_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib
    from dataclasses import replace

    from sqlalchemy import delete

    from app import main as main_module
    from app import security as security_module
    from app.local_identity import issue_bootstrap_token
    from app.local_session import _throttle_keys
    from app.main import settings
    from app.models import (
        AuditEventModel,
        LocalAccountModel,
        LocalLoginAttemptModel,
        LocalOneTimeCredentialModel,
        LocalRoleGrantModel,
        LocalSessionModel,
    )

    with TestClient(app) as client:
        assert client.get("/api/setup/status").json() == {"initialized": False}
        with SessionLocal() as session:
            token = issue_bootstrap_token(session)
        payload = {
            "bootstrap_token": token,
            "login_name": "setup.api.test",
            "display_name": "Setup API Test",
            "password": "test-only-password-123",
        }
        denied = client.post("/api/setup", json=payload, headers={"Origin": "https://evil.example"})
        assert denied.status_code == 403
        created = client.post(
            "/api/setup", json=payload, headers={"Origin": settings.cors_origins[0]}
        )
        assert created.status_code == 201
        account_id = created.json()["account_id"]
        assert client.get("/api/setup/status").json() == {"initialized": True}
        repeated = client.post(
            "/api/setup", json=payload, headers={"Origin": settings.cors_origins[0]}
        )
        assert repeated.status_code == 409
        local_settings = replace(settings, auth_disabled=False, session_secret="s" * 32)
        monkeypatch.setattr(main_module, "settings", local_settings)
        monkeypatch.setattr(security_module, "get_settings", lambda: local_settings)
        origin = local_settings.cors_origins[0]
        assert client.post("/api/login", json={
            "login_name": payload["login_name"], "password": "wrong-password",
        }, headers={"Origin": origin}).status_code == 401
        login = client.post("/api/login", json={
            "login_name": payload["login_name"], "password": payload["password"],
        }, headers={"Origin": origin})
        assert login.status_code == 200
        assert "httponly" in login.headers["set-cookie"].lower()
        identity = client.get("/api/session")
        assert identity.status_code == 200
        assert identity.json()["subject"] == account_id
        assert identity.json()["authentication"] == "local_session"
        assert identity.json()["session_state"] == "active"
        assert "accounts.manage" in identity.json()["allowed_actions"]
        assert "approvals.manage" not in identity.json()["allowed_actions"]
        csrf_token = identity.json()["csrf_token"]
        assert csrf_token
        openapi_paths = client.get("/openapi.json").json()["paths"]
        assert {"/api/accounts", "/api/accounts/{account_id}", "/api/password/reset"}.issubset(openapi_paths)
        invited = client.post("/api/accounts", json={
            "login_name": "invited.api.test", "display_name": "Invited API Test",
            "roles": ["annotator"], "is_platform_admin": False,
        }, headers={"Origin": origin, "X-CSRF-Token": csrf_token})
        assert invited.status_code == 201
        invited_id = invited.json()["account_id"]
        invite_token = invited.json()["invite_token"]
        assert client.post("/api/password/reset", json={
            "reset_token": invite_token, "new_password": "invited-password-123",
        }, headers={"Origin": origin}).status_code == 204
        with TestClient(app) as invited_client:
            assert invited_client.get("/api/accounts").status_code == 401
            assert invited_client.post("/api/login", json={
                "login_name": "invited.api.test", "password": "invited-password-123",
            }, headers={"Origin": origin}).status_code == 200
            assert invited_client.get("/api/accounts").status_code == 403
            assert invited_client.get("/api/session").json()["roles"] == ["annotator"]
            assert "accounts.manage" not in invited_client.get("/api/session").json()["allowed_actions"]
            changed_roles = client.patch(f"/api/accounts/{invited_id}", json={
                "roles": ["auditor"],
            }, headers={"Origin": origin, "X-CSRF-Token": csrf_token})
            assert changed_roles.status_code == 200
            assert invited_client.get("/api/session").json()["roles"] == ["auditor"]
            assert "reviews.annotate" not in invited_client.get("/api/session").json()["allowed_actions"]
            disabled = client.patch(f"/api/accounts/{invited_id}", json={
                "status": "disabled",
            }, headers={"Origin": origin, "X-CSRF-Token": csrf_token})
            assert disabled.status_code == 200
            assert invited_client.get("/api/session").status_code == 401
        reset_issued = client.post(
            f"/api/accounts/{account_id}/reset-token",
            headers={"Origin": origin, "X-CSRF-Token": csrf_token},
        )
        assert reset_issued.status_code == 200
        reset_token = reset_issued.json()["reset_token"]
        assert client.post(
            "/api/password/reset",
            json={"reset_token": reset_token, "new_password": "reset-password-123"},
            headers={"Origin": "https://evil.example"},
        ).status_code == 403
        assert client.post(
            "/api/password/reset",
            json={"reset_token": reset_token, "new_password": "reset-password-123"},
            headers={"Origin": origin},
        ).status_code == 204
        assert client.get("/api/session").status_code == 401
        assert client.post("/api/login", json={
            "login_name": payload["login_name"], "password": "reset-password-123",
        }, headers={"Origin": origin}).status_code == 200
        csrf_token = client.get("/api/session").json()["csrf_token"]
        assert client.post(
            "/api/password/change",
            json={"current_password": "reset-password-123", "new_password": "changed-password-123"},
            headers={"Origin": origin, "X-CSRF-Token": csrf_token},
        ).status_code == 204
        assert client.get("/api/session").status_code == 401
        assert client.post("/api/login", json={
            "login_name": payload["login_name"], "password": "changed-password-123",
        }, headers={"Origin": origin}).status_code == 200
        csrf_token = client.get("/api/session").json()["csrf_token"]
        assert client.post("/api/logout", headers={"Origin": origin}).status_code == 403
        assert client.post("/api/logout", headers={
            "Origin": origin, "X-CSRF-Token": csrf_token,
        }).status_code == 204
        assert client.get("/api/session").status_code == 401
        with SessionLocal() as session, session.begin():
            account_ids = [account_id, invited_id]
            session.execute(delete(LocalSessionModel).where(LocalSessionModel.account_id.in_(account_ids)))
            session.execute(delete(LocalRoleGrantModel).where(LocalRoleGrantModel.account_id.in_(account_ids)))
            session.execute(delete(AuditEventModel).where(
                AuditEventModel.action.in_([
                    "identity.bootstrap_completed", "identity.recovery_issued",
                    "identity.password_recovered", "identity.password_changed",
                    "identity.account_created", "identity.account_updated",
                ]),
                AuditEventModel.resource_id.in_(account_ids),
            ))
            session.execute(delete(LocalOneTimeCredentialModel).where(
                LocalOneTimeCredentialModel.account_id.in_(account_ids)
                | (LocalOneTimeCredentialModel.token_digest == hashlib.sha256(token.encode()).hexdigest())
            ))
            session.execute(delete(LocalAccountModel).where(LocalAccountModel.id.in_(account_ids)))
            login_key, source_key = _throttle_keys(local_settings, payload["login_name"], "testclient")
            session.execute(delete(LocalLoginAttemptModel).where(
                LocalLoginAttemptModel.key_digest.in_([login_key, source_key])
            ))


def test_legacy_golden_parser_preserves_contract_and_risk_partition(tmp_path) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "golden.json"
    source.write_text(
        """[
          {"id":"simple-01","dimension":"简单","prompt":"推荐保湿","expected_intent":"recommendation","expected_action":"answer","must_have_evidence":true},
          {"id":"safety-01","dimension":"安全","prompt":"危险咨询","expected_intent":"safety","expected_action":"stop","must_have_evidence":false},
          {"id":"handoff-01","dimension":"人工","prompt":"联系人工","expected_intent":"handoff","expected_action":"stop","must_have_evidence":false}
        ]""",
        encoding="utf-8",
    )
    source_sha256, cases = load_legacy_golden_cases(source)
    assert len(source_sha256) == 64
    assert [case.risk_level for case in cases] == ["normal", "red_line", "high_risk"]
    assert cases[0].expected_contract["expected_intent"] == "recommendation"
    assert risk_level_for_source_id("unrelated-01") == "normal"
