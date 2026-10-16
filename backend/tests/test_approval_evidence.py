"""Independent approval uses verified contributors and current evidence digests."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import get_session
from app.models import (
    AuditEventModel,
    Base,
    DatasetCaseModel,
    DatasetModel,
    DifyTargetModel,
    EvaluationCaseResultModel,
    EvaluationReportModel,
    EvaluationRunModel,
    EvidenceContributionModel,
    LocalAccountModel,
    LocalRoleGrantModel,
    PromptCandidateModel,
    ReleaseDecisionModel,
    ReleaseHandoffModel,
    ReviewAnnotationModel,
    ReviewCaseModel,
)
from app.repository import audit as write_audit
from app.repository import now_utc, seed_development_data
from app.security import Actor, current_actor

ROLES = ("operator", "annotator", "prompt_owner", "approver", "auditor")
LABEL = {
    "business_understanding": 25, "clarification": 25, "tool_use": 25, "safety": 25,
    "risk_level": "normal", "rationale": "人工复核已确认此条证据满足发布前门禁。",
}


@pytest.fixture
def governed_client(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        seed_development_data(session)
        for role in ROLES:
            session.add(LocalAccountModel(
                id=f"account-{role}", login_name=f"governed-{role}", display_name=role,
                password_hash=None, status="active", is_platform_admin=role == "operator",
                created_at=now_utc(), updated_at=now_utc(),
            ))
            session.add(LocalRoleGrantModel(
                id=f"grant-{role}", account_id=f"account-{role}", role=role,
                created_at=now_utc(),
            ))
        session.commit()

    def isolated_session():  # type: ignore[no-untyped-def]
        with factory() as session:
            yield session

    selected = {"actor": Actor(subject="account-operator", roles=frozenset({"operator"}))}
    main.app.dependency_overrides[get_session] = isolated_session
    main.app.dependency_overrides[current_actor] = lambda: selected["actor"]
    monkeypatch.setattr(main, "settings", replace(main.settings, auth_disabled=False))
    try:
        with TestClient(main.app) as client:
            yield client, selected, factory
    finally:
        main.app.dependency_overrides.clear()
        engine.dispose()


def as_role(selected: dict[str, Actor], role: str, *other_roles: str) -> None:
    selected["actor"] = Actor(
        subject=f"account-{role}", roles=frozenset({role, *other_roles}),
        is_platform_admin=role == "operator",
    )


def test_audit_readback_preserves_legacy_text_and_hides_sensitive_detail(governed_client) -> None:  # type: ignore[no-untyped-def]
    client, selected, factory = governed_client
    legacy_actor = "X-AutoPE-Actor:old-demo-user"
    sensitive = "password=not-for-audit; prompt=private-case-body"
    with factory() as session:
        session.add(AuditEventModel(
            id="audit_legacy_fixture", actor_id=legacy_actor, action="dataset.created",
            resource_type="dataset", resource_id="dataset_golden_v1",
            request_id="legacy-request", detail={"body": sensitive}, created_at=now_utc(),
        ))
        write_audit(
            session, actor_id="account-operator", action="test.sanitized",
            resource_type="dataset", resource_id="dataset_golden_v1",
            request_id="safe-request", detail={"password": sensitive, "case_count": 1},
            identity_source="local_verified", actor_roles=["operator"], object_version="1",
        )
        session.commit()
    as_role(selected, "operator")
    denied = client.get("/api/audit-events", headers={"X-Request-ID": "denied-audit-read"})
    assert denied.status_code == 403
    created = client.post("/api/datasets", json={"name": "审计核对", "case_count": 1})
    assert created.status_code == 201, created.text
    with factory() as session:
        event = session.scalar(select(AuditEventModel).where(AuditEventModel.action == "dataset.created").order_by(
            AuditEventModel.created_at.desc(),
        ))
        assert event is not None
        assert event.identity_source == "local_verified"
        assert event.actor_id == "account-operator"
        assert event.actor_roles == ["operator"]
        assert event.object_version == "1"
        assert event.outcome == "succeeded"
        sanitized = session.scalar(select(AuditEventModel).where(AuditEventModel.action == "test.sanitized"))
        assert sanitized is not None and sanitized.detail == {"case_count": 1}
    as_role(selected, "auditor")
    response = client.get("/api/audit-events")
    assert response.status_code == 200, response.text
    events = response.json()
    legacy = next(item for item in events if item["id"] == "audit_legacy_fixture")
    assert legacy["actor_id"] == legacy_actor
    assert legacy["identity_source"] == "local_unverified"
    assert "detail" not in legacy
    assert sensitive not in response.text
    assert any(item["action"] == "security.role_denied" and item["outcome"] == "denied"
               for item in events)


def test_anonymous_authentication_denial_has_no_invented_subject(governed_client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app import security

    client, _selected, factory = governed_client
    monkeypatch.setattr(security, "get_settings", lambda: replace(main.settings, auth_disabled=False))
    main.app.dependency_overrides.pop(current_actor)
    denied = client.get("/api/session", headers={"X-Request-ID": "anonymous-test"})
    assert denied.status_code == 401
    with factory() as session:
        event = session.scalar(select(AuditEventModel).where(
            AuditEventModel.request_id == "anonymous-test",
        ))
        assert event is not None
        assert event.actor_id == ""
        assert event.identity_source == "anonymous"
        assert event.outcome == "denied"


def create_approved_dataset(client: TestClient, selected: dict[str, Actor], *, risk: str = "normal") -> tuple[str, str]:
    as_role(selected, "operator")
    created = client.post("/api/datasets", json={"name": "可信审批测试集", "case_count": 1})
    assert created.status_code == 201, created.text
    dataset_id = created.json()["id"]
    uploaded = client.post(f"/api/datasets/{dataset_id}/cases", json={
        "cases": [{"title": "审批测试用例", "prompt": "咨询测试", "risk_level": risk}],
    })
    assert uploaded.status_code == 200, uploaded.text
    frozen = client.post(f"/api/datasets/{dataset_id}/freeze")
    assert frozen.status_code == 200, frozen.text
    detail = client.get(f"/api/datasets/{dataset_id}")
    assert detail.headers["etag"] == f'"{detail.json()["evidence_digest"]}"'
    case_id = detail.json()["cases"][0]["id"]
    as_role(selected, "approver")
    approved = client.post(f"/api/datasets/{dataset_id}/approve", headers={
        "If-Match": detail.headers["etag"],
    })
    assert approved.status_code == 200, approved.text
    return dataset_id, case_id


def make_completed_run(
    factory, dataset_id: str, case_id: str, *, status: str = "manual_review",
) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    run_id = "run_governed_test"
    review_id = "review_governed_test"
    with factory() as session:
        dataset = session.get(DatasetModel, dataset_id)
        candidate = session.get(PromptCandidateModel, "candidate_clarify_v1")
        baseline = session.get(DifyTargetModel, "target_baseline")
        target = session.get(DifyTargetModel, "target_candidate")
        assert dataset and candidate and baseline and target
        session.add(EvaluationRunModel(
            id=run_id, dataset_id=dataset.id, baseline_target_id=baseline.id,
            run_mode="comparison", candidate_target_id=target.id, candidate_id=candidate.id,
            status="completed", idempotency_key="governed-run-key", created_by="account-operator",
            snapshot={
                "dataset_hash": dataset.content_hash, "dataset_version": dataset.version,
                "baseline_version": baseline.version_fingerprint,
                "candidate_version": target.version_fingerprint,
                "prompt_sha256": candidate.prompt_sha256,
                "candidate_binding_attestation": dict(candidate.binding_attestation),
                "baseline_published_workflow_id": baseline.published_workflow_id,
                "candidate_published_workflow_id": target.published_workflow_id,
            },
            created_at=now_utc(), started_at=now_utc(), completed_at=now_utc(),
        ))
        session.add(EvaluationReportModel(
            run_id=run_id, gate_status="pending_review", pass_rates={}, score_dimensions={},
            p95_latency_ms=0, average_cost_usd=0.0, failed_cases=0,
            passed_cases=0, manual_review_cases=1, execution_error_cases=0,
        ))
        session.add(EvaluationCaseResultModel(
            id="result_governed_test", run_id=run_id, dataset_case_id=case_id,
            status=status, baseline_response="基线回答", candidate_response="先澄清后回答。",
            baseline_scores={}, candidate_scores={}, automatic_score_complete=False,
            passed=False, baseline_latency_ms=10, candidate_latency_ms=10,
            error_code="", error_message="", runtime_evidence={}, automatic_assessment={},
            completed_at=now_utc(),
        ))
        case = session.get(DatasetCaseModel, case_id)
        assert case is not None
        session.add(ReviewCaseModel(
            id=review_id, dataset_id=dataset_id, title=case.title,
            risk_level=case.risk_level, review_status="pending", score=0,
            source_run_id=run_id, dataset_case_id=case_id,
            request_text=case.prompt, response_text="先澄清后回答。",
            expected_behavior=case.expected_behavior, updated_at=now_utc(),
        ))
        session.commit()
    return run_id, review_id


def attest_seed_candidate(client: TestClient, selected: dict[str, Actor], factory) -> None:  # type: ignore[no-untyped-def]
    with factory() as session:
        candidate = session.get(PromptCandidateModel, "candidate_clarify_v1")
        target = session.get(DifyTargetModel, "target_candidate")
        assert candidate and target
        prompt_block = candidate.prompt_block
        workflow_id = target.published_workflow_id
    as_role(selected, "prompt_owner")
    response = client.post("/api/candidates/candidate_clarify_v1/binding-attestation", json={
        "published_workflow_id": workflow_id, "published_prompt_block": prompt_block,
        "review_note": "已人工核对当前独立候选工作流版本。", "human_confirmed": True,
    })
    assert response.status_code == 200, response.text


def assign_and_label(client: TestClient, selected: dict[str, Actor], review_id: str, *, label: dict[str, object] = LABEL) -> None:
    as_role(selected, "operator")
    assigned = client.patch(f"/api/review-cases/{review_id}/assignments", json={
        "account_ids": ["account-annotator"],
    })
    assert assigned.status_code == 200, assigned.text
    as_role(selected, "annotator")
    labeled = client.post(f"/api/review-cases/{review_id}/label", json=label)
    assert labeled.status_code == 200, labeled.text


def release_request(client: TestClient, run_id: str, digest: str | None, key: str = "governed-release-key"):
    headers = {"If-Match": f'"{digest}"'} if digest is not None else {}
    return client.post("/api/release-decisions", json={
        "run_id": run_id, "decision": "approved", "rationale": "人工确认当前 GO 证据满足发布门禁。",
        "idempotency_key": key,
    }, headers=headers)


def test_dataset_self_review_stale_digest_and_independent_approval(governed_client) -> None:  # type: ignore[no-untyped-def]
    client, selected, factory = governed_client
    as_role(selected, "operator")
    created = client.post("/api/datasets", json={"name": "自审测试集", "case_count": 1})
    dataset_id = created.json()["id"]
    assert client.post(f"/api/datasets/{dataset_id}/cases", json={
        "cases": [{"title": "自审测试用例", "prompt": "请先澄清"}],
    }).status_code == 200
    assert client.post(f"/api/datasets/{dataset_id}/freeze").status_code == 200
    digest = client.get(f"/api/datasets/{dataset_id}").json()["evidence_digest"]

    as_role(selected, "operator", "approver")
    denied = client.post(f"/api/datasets/{dataset_id}/approve", headers={"If-Match": f'"{digest}"'})
    assert denied.status_code == 403
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AuditEventModel).where(
            AuditEventModel.action == "dataset.approval_denied",
        )) == 1
        assert session.get(DatasetModel, dataset_id).review_status == "in_review"

    as_role(selected, "approver")
    assert client.post(f"/api/datasets/{dataset_id}/approve").status_code == 428
    assert client.post(f"/api/datasets/{dataset_id}/approve", headers={
        "If-Match": '"0"',
    }).status_code == 412

    as_role(selected, "operator")
    signed = client.post(f"/api/datasets/{dataset_id}/attest", headers={
        "If-Match": f'"{digest}"',
    })
    assert signed.status_code == 200
    fresh_digest = signed.json()["evidence_digest"]
    assert fresh_digest != digest
    as_role(selected, "approver")
    assert client.post(f"/api/datasets/{dataset_id}/approve", headers={
        "If-Match": f'"{digest}"',
    }).status_code == 412
    approved = client.post(f"/api/datasets/{dataset_id}/approve", headers={
        "If-Match": f'"{fresh_digest}"',
    })
    assert approved.status_code == 200, approved.text


def test_legacy_dataset_requires_verified_resign_without_rewriting_history(governed_client) -> None:  # type: ignore[no-untyped-def]
    client, selected, factory = governed_client
    with factory() as session:
        dataset = session.get(DatasetModel, "dataset_golden_v1")
        case = session.get(DatasetCaseModel, "dataset_case_dry_skin_01")
        assert dataset and case
        dataset.content_hash = main.hash_dataset_cases([case])
        dataset.review_status = "in_review"
        session.commit()
    as_role(selected, "approver")
    digest = client.get("/api/datasets/dataset_golden_v1").json()["evidence_digest"]
    missing = client.post("/api/datasets/dataset_golden_v1/approve", headers={
        "If-Match": f'"{digest}"',
    })
    assert missing.status_code == 409
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(EvidenceContributionModel)) == 0

    as_role(selected, "operator")
    signed = client.post("/api/datasets/dataset_golden_v1/attest", headers={
        "If-Match": f'"{digest}"',
    })
    assert signed.status_code == 200, signed.text
    as_role(selected, "approver")
    approved = client.post("/api/datasets/dataset_golden_v1/approve", headers={
        "If-Match": f'"{signed.json()["evidence_digest"]}"',
    })
    assert approved.status_code == 200
    with factory() as session:
        row = session.scalar(select(EvidenceContributionModel))
        assert row is not None and row.actor_id == "account-operator"
        assert row.action == "attested"


def test_release_requires_verified_candidate_review_and_independent_approver(governed_client) -> None:  # type: ignore[no-untyped-def]
    client, selected, factory = governed_client
    dataset_id, case_id = create_approved_dataset(client, selected)
    run_id, review_id = make_completed_run(factory, dataset_id, case_id)
    as_role(selected, "approver")
    report = client.get(f"/api/evaluation-runs/{run_id}/report")
    assert report.status_code == 200
    assert report.headers["etag"] == f'"{report.json()["evidence_digest"]}"'
    assert release_request(client, run_id, None).status_code == 428
    assert release_request(client, run_id, "0").status_code == 412
    assert release_request(client, run_id, report.json()["evidence_digest"]).status_code == 409

    assign_and_label(client, selected, review_id)
    as_role(selected, "approver")
    unsigned = client.get(f"/api/evaluation-runs/{run_id}/report").json()["evidence_digest"]
    assert release_request(client, run_id, unsigned).status_code == 409

    attest_seed_candidate(client, selected, factory)
    as_role(selected, "approver")
    fresh = client.get(f"/api/evaluation-runs/{run_id}/report").json()["evidence_digest"]
    assert fresh != unsigned
    assert release_request(client, run_id, unsigned).status_code == 412
    as_role(selected, "operator", "approver")
    assert release_request(client, run_id, fresh).status_code == 403
    as_role(selected, "prompt_owner", "approver")
    assert release_request(client, run_id, fresh).status_code == 403
    as_role(selected, "annotator", "approver")
    assert release_request(client, run_id, fresh).status_code == 403

    as_role(selected, "approver")
    decision = release_request(client, run_id, fresh)
    assert decision.status_code == 201, decision.text
    assert decision.json()["is_current"] is True
    assert release_request(client, run_id, None).status_code == 428
    decision_id = decision.json()["id"]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ReleaseDecisionModel)) == 1

    handoff_payload = {"decision_id": decision_id, "action": "publish", "idempotency_key": "governed-handoff-key"}
    assert client.post("/api/release-handoffs", json=handoff_payload).status_code == 428
    assert client.post("/api/release-handoffs", json=handoff_payload, headers={"If-Match": '"0"'}).status_code == 412
    handoff = client.post("/api/release-handoffs", json=handoff_payload, headers={"If-Match": f'"{fresh}"'})
    assert handoff.status_code == 201, handoff.text
    assert client.post("/api/release-handoffs", json=handoff_payload).status_code == 428
    handoff_id = handoff.json()["id"]
    confirm = {"published_workflow_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
               "notes": "人工已在 Dify 控制台执行，平台不自动发布。", "human_confirmed": True}
    assert client.post(f"/api/release-handoffs/{handoff_id}/confirm", json=confirm).status_code == 428
    confirmed = client.post(f"/api/release-handoffs/{handoff_id}/confirm", json=confirm,
                            headers={"If-Match": f'"{fresh}"'})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["verification"] == "not_verified_by_dify"
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ReleaseHandoffModel)) == 1


def test_legacy_annotation_is_preserved_but_cannot_authorize_new_release(governed_client) -> None:  # type: ignore[no-untyped-def]
    client, selected, factory = governed_client
    dataset_id, case_id = create_approved_dataset(client, selected)
    attest_seed_candidate(client, selected, factory)
    run_id, review_id = make_completed_run(factory, dataset_id, case_id)
    with factory() as session:
        result = session.get(EvaluationCaseResultModel, "result_governed_test")
        review = session.get(ReviewCaseModel, review_id)
        assert result and review
        result.status = "human_approved"
        result.passed = True
        result.candidate_scores = {key: 25 for key in (
            "business_understanding", "clarification", "tool_use", "safety",
        )}
        review.review_status = "approved"
        review.score = 100
        session.add(ReviewAnnotationModel(
            id="legacy_annotation", case_id=review_id, reviewer_id="local-operator",
            score_breakdown=dict(result.candidate_scores), risk_level="normal",
            rationale="旧本地演练评语，不能追认为真实人员。",
            created_at=now_utc() - timedelta(days=1),
        ))
        session.commit()
    as_role(selected, "approver")
    digest = client.get(f"/api/evaluation-runs/{run_id}/report").json()["evidence_digest"]
    denied = release_request(client, run_id, digest)
    assert denied.status_code == 409
    assign_and_label(client, selected, review_id)
    as_role(selected, "approver")
    refreshed = client.get(f"/api/evaluation-runs/{run_id}/report").json()["evidence_digest"]
    assert release_request(client, run_id, refreshed).status_code == 201
    with factory() as session:
        old = session.get(ReviewAnnotationModel, "legacy_annotation")
        assert old is not None and old.reviewer_id == "local-operator"
        assert old.rationale == "旧本地演练评语，不能追认为真实人员。"


def test_evidence_change_invalidates_decision_and_pending_handoff(governed_client) -> None:  # type: ignore[no-untyped-def]
    client, selected, factory = governed_client
    dataset_id, case_id = create_approved_dataset(client, selected)
    attest_seed_candidate(client, selected, factory)
    run_id, review_id = make_completed_run(factory, dataset_id, case_id)
    assign_and_label(client, selected, review_id)
    as_role(selected, "approver")
    digest = client.get(f"/api/evaluation-runs/{run_id}/report").json()["evidence_digest"]
    decision = release_request(client, run_id, digest)
    assert decision.status_code == 201, decision.text
    handoff = client.post("/api/release-handoffs", json={
        "decision_id": decision.json()["id"], "action": "publish",
        "idempotency_key": "stale-handoff-key",
    }, headers={"If-Match": f'"{digest}"'})
    assert handoff.status_code == 201, handoff.text

    attest_seed_candidate(client, selected, factory)
    as_role(selected, "approver")
    current = client.get(f"/api/evaluation-runs/{run_id}/report").json()["evidence_digest"]
    assert current != digest
    decisions = client.get("/api/release-decisions").json()
    assert next(item for item in decisions if item["id"] == decision.json()["id"])["is_current"] is False
    confirm = {"published_workflow_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
               "notes": "旧证据下的交接不得确认。", "human_confirmed": True}
    stale = client.post(f"/api/release-handoffs/{handoff.json()["id"]}/confirm", json=confirm,
                        headers={"If-Match": f'"{digest}"'})
    assert stale.status_code == 412
    not_current = client.post(f"/api/release-handoffs/{handoff.json()["id"]}/confirm", json=confirm,
                              headers={"If-Match": f'"{current}"'})
    assert not_current.status_code == 409
    with factory() as session:
        persisted = session.get(ReleaseHandoffModel, handoff.json()["id"])
        assert persisted is not None and persisted.status == "awaiting_dify"


@pytest.mark.parametrize("risk", ["high_risk", "red_line"])
def test_risk_failures_cannot_be_overridden_by_approver(governed_client, risk: str) -> None:  # type: ignore[no-untyped-def]
    client, selected, factory = governed_client
    dataset_id, case_id = create_approved_dataset(client, selected, risk=risk)
    attest_seed_candidate(client, selected, factory)
    run_id, review_id = make_completed_run(factory, dataset_id, case_id)
    assign_and_label(client, selected, review_id, label={**LABEL, "risk_level": risk})
    # Adversarial evidence: a high total score must not override a risk-specific failure.
    with factory() as session:
        result = session.get(EvaluationCaseResultModel, "result_governed_test")
        assert result is not None
        result.passed = False
        result.status = "human_rejected"
        session.commit()
    as_role(selected, "approver")
    report = client.get(f"/api/evaluation-runs/{run_id}/report").json()
    assert report["gate_status"] == "no_go"
    denied = release_request(client, run_id, report["evidence_digest"])
    assert denied.status_code == 409
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ReleaseDecisionModel)) == 0


def test_runtime_error_cannot_be_approved_after_review(governed_client) -> None:  # type: ignore[no-untyped-def]
    client, selected, factory = governed_client
    dataset_id, case_id = create_approved_dataset(client, selected)
    attest_seed_candidate(client, selected, factory)
    run_id, review_id = make_completed_run(factory, dataset_id, case_id)
    assign_and_label(client, selected, review_id)
    with factory() as session:
        result = session.get(EvaluationCaseResultModel, "result_governed_test")
        assert result is not None
        result.error_code = "dify_timeout"
        result.passed = False
        result.status = "failed"
        session.commit()
    as_role(selected, "approver")
    report = client.get(f"/api/evaluation-runs/{run_id}/report").json()
    assert report["execution_error_cases"] == 1
    denied = release_request(client, run_id, report["evidence_digest"])
    assert denied.status_code == 409
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ReleaseDecisionModel)) == 0
