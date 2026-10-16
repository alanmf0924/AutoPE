"""Service-side role and review-object boundaries, without network runtimes."""

from __future__ import annotations

from dataclasses import replace

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
    CandidateGenerationJobModel,
    DatasetModel,
    DifyTargetModel,
    EvaluationRunModel,
    LocalAccountModel,
    LocalRoleGrantModel,
    PromptCandidateModel,
    ReleaseDecisionModel,
    ReleaseHandoffModel,
    ReviewAnnotationModel,
    ReviewCaseAssignmentModel,
)
from app.repository import now_utc, seed_development_data
from app.security import Actor, current_actor

ROLES = ("operator", "annotator", "prompt_owner", "approver", "auditor")
MUTABLE_MODELS = (
    CandidateGenerationJobModel, DatasetModel, DifyTargetModel,
    EvaluationRunModel, PromptCandidateModel, ReleaseDecisionModel, ReleaseHandoffModel,
    ReviewAnnotationModel, ReviewCaseAssignmentModel,
)
REVIEW_ID = "case_dry_skin_01"
RESTRICTED_TEXT = "已识别为干敏肌"
LABEL = {
    "business_understanding": 20,
    "clarification": 20,
    "tool_use": 20,
    "safety": 25,
    "risk_level": "normal",
    "rationale": "测试用人工评语，不应在拒绝请求中落库。",
}


@pytest.fixture
def authorization_client(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        seed_development_data(session)
        for role in ROLES:
            session.add(LocalAccountModel(
                id=f"account-{role}", login_name=f"matrix-{role}", display_name=role,
                password_hash=None, status="active", is_platform_admin=False,
                created_at=now_utc(), updated_at=now_utc(),
            ))
            session.add(LocalRoleGrantModel(
                id=f"grant-{role}", account_id=f"account-{role}", role=role,
                created_at=now_utc(),
            ))
        session.add(LocalAccountModel(
            id="account-annotator-other", login_name="matrix-annotator-other",
            display_name="other annotator", password_hash=None, status="active",
            is_platform_admin=False, created_at=now_utc(), updated_at=now_utc(),
        ))
        session.add(LocalRoleGrantModel(
            id="grant-annotator-other", account_id="account-annotator-other",
            role="annotator", created_at=now_utc(),
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


READ_MATRIX = (
    ("/api/targets", {"operator", "prompt_owner", "approver", "auditor"}),
    ("/api/targets/target_baseline", {"operator", "prompt_owner", "approver", "auditor"}),
    ("/api/datasets", {"operator", "prompt_owner", "approver", "auditor"}),
    ("/api/datasets/dataset_golden_v1", {"operator", "prompt_owner", "approver", "auditor"}),
    ("/api/review-cases", {"operator", "annotator", "auditor"}),
    (f"/api/review-cases/{REVIEW_ID}", {"operator", "auditor"}),
    (f"/api/review-cases/{REVIEW_ID}/assignments", {"operator", "auditor"}),
    ("/api/review-suggestions/config", {"operator", "annotator", "auditor"}),
    ("/api/candidates", {"operator", "prompt_owner", "approver", "auditor"}),
    ("/api/candidates/candidate_clarify_v1", {"operator", "prompt_owner", "approver", "auditor"}),
    ("/api/candidates/generation-jobs", {"operator", "prompt_owner", "auditor"}),
    ("/api/candidates/generation-config", {"operator", "prompt_owner", "auditor"}),
    ("/api/evaluation-runs", {"operator", "prompt_owner", "approver", "auditor"}),
    ("/api/evaluation-runs/run_seed_20261012/dashboard", {"operator", "prompt_owner", "approver", "auditor"}),
    ("/api/evaluation-runs/run_seed_20261012/report", {"operator", "prompt_owner", "approver", "auditor"}),
    ("/api/release-decisions", {"operator", "approver", "auditor"}),
    ("/api/release-handoffs", {"operator", "approver", "auditor"}),
)


@pytest.mark.parametrize("path,allowed", READ_MATRIX)
@pytest.mark.parametrize("role", ROLES)
def test_read_role_matrix(authorization_client, path: str, allowed: set[str], role: str) -> None:  # type: ignore[no-untyped-def]
    client, selected, _factory = authorization_client
    selected["actor"] = Actor(subject=f"account-{role}", roles=frozenset({role}))
    response = client.get(path)
    # Annotator detail is object-scoped: this fixture has no assignment yet.
    expected = 200 if role in allowed else 403
    assert response.status_code == expected, (role, path, response.text)


WRITE_MATRIX = (
    ("POST", "/api/targets", "operator", {
        "name": "matrix target", "base_url": "https://example.invalid",
        "secret_ref": "test/secret", "version_fingerprint": "version-123",
    }),
    ("POST", "/api/datasets", "operator", {"name": "matrix dataset", "case_count": 1}),
    ("PATCH", "/api/targets/target_baseline", "operator", {
        "name": "matrix target", "base_url": "https://example.invalid",
        "version_fingerprint": "version-123",
    }),
    ("DELETE", "/api/targets/target_baseline", "operator", {}),
    ("POST", "/api/datasets/dataset_golden_v1/cases", "operator", {
        "cases": [{"title": "matrix case", "prompt": "test prompt"}],
    }),
    ("POST", "/api/datasets/dataset_golden_v1/freeze", "operator", {}),
    ("POST", "/api/datasets/dataset_golden_v1/approve", "approver", {}),
    ("PATCH", f"/api/review-cases/{REVIEW_ID}/assignments", "operator", {"account_ids": []}),
    ("POST", f"/api/review-cases/{REVIEW_ID}/label", "annotator", LABEL),
    ("POST", f"/api/review-cases/{REVIEW_ID}/reopen", "annotator", {
        "reason": "Review role test only.",
    }),
    ("POST", "/api/evaluation-runs/run_seed_20261012/review-suggestions", "operator", {
        "authorize_external_transfer": True, "consented_model": "test/model",
        "consented_endpoint": "https://example.invalid/model",
    }),
    ("POST", "/api/review-suggestions/suggestion_missing/retry", "operator", {
        "authorize_external_transfer": True, "consented_model": "test/model",
        "consented_endpoint": "https://example.invalid/model", "additional_authorized_calls": 1,
    }),
    ("POST", "/api/evaluation-runs/run_seed_20261012/review-suggestions/retry-failed", "operator", {
        "authorize_external_transfer": True, "consented_model": "test/model",
        "consented_endpoint": "https://example.invalid/model", "additional_authorized_calls": 1,
    }),
    ("POST", "/api/candidates", "prompt_owner", {
        "name": "matrix candidate", "base_version_fingerprint": "version-123",
        "optimizable_prompt_block": "A long enough test prompt block for role enforcement.",
    }),
    ("POST", "/api/candidates/generate", "prompt_owner", {
        "dataset_id": "dataset_golden_v1", "baseline_target_id": "target_baseline",
        "authorize_external_transfer": True, "consented_model": "test/model",
        "consented_endpoint": "https://example.invalid/model",
    }),
    ("POST", "/api/candidates/generation-jobs/job_missing/cancel", "prompt_owner", {}),
    ("PATCH", "/api/candidates/candidate_clarify_v1/binding", "prompt_owner", {
        "bound_target_id": "target_candidate",
    }),
    ("POST", "/api/candidates/candidate_clarify_v1/binding-attestation", "prompt_owner", {
        "published_workflow_id": "11111111-1111-4111-8111-111111111111",
        "published_prompt_block": "A long enough prompt block for this matrix test.",
        "review_note": "Test note only.", "human_confirmed": True,
    }),
    ("POST", "/api/evaluation-runs", "operator", {
        "dataset_id": "dataset_golden_v1", "baseline_target_id": "target_baseline",
        "idempotency_key": "matrix-eval-key",
    }),
    ("POST", "/api/evaluation-runs/run_seed_20261012/cancel", "operator", {}),
    ("DELETE", "/api/evaluation-runs/run_seed_20261012", "operator", {}),
    ("POST", "/api/release-decisions", "approver", {
        "run_id": "run_seed_20261012", "decision": "rejected",
        "rationale": "Role test decision, never submitted.", "idempotency_key": "matrix-decision-key",
    }),
    ("POST", "/api/release-handoffs", "approver", {
        "decision_id": "decision_not_created", "action": "publish",
        "idempotency_key": "matrix-handoff-key",
    }),
    ("POST", "/api/release-handoffs/handoff_missing/confirm", "approver", {
        "published_workflow_id": "11111111-1111-4111-8111-111111111111",
        "notes": "Human confirmation test.", "human_confirmed": True,
    }),
)


@pytest.mark.parametrize("method,path,owner,payload", WRITE_MATRIX)
@pytest.mark.parametrize("role", ROLES)
def test_write_role_matrix_denies_before_side_effects(
    authorization_client, method: str, path: str, owner: str, payload: dict[str, object], role: str,
) -> None:  # type: ignore[no-untyped-def]
    if role == owner:
        return  # Positive workflow cases are covered by API tests and assignment test below.
    client, selected, factory = authorization_client
    selected["actor"] = Actor(subject=f"account-{role}", roles=frozenset({role}))
    with factory() as session:
        before = tuple(
            session.scalar(select(func.count()).select_from(model)) for model in MUTABLE_MODELS
        )
        audit_before = session.scalar(select(func.count()).select_from(AuditEventModel))
    response = client.request(method, path, json=payload)
    assert response.status_code == 403, (role, method, path, response.text)
    with factory() as session:
        after = tuple(
            session.scalar(select(func.count()).select_from(model)) for model in MUTABLE_MODELS
        )
        denied = session.scalars(select(AuditEventModel).where(
            AuditEventModel.action == "security.role_denied",
        )).all()
    assert after == before
    assert len(denied) == (audit_before or 0) + 1
    assert denied[0].identity_source == "local_verified"
    assert denied[0].outcome == "denied"


def test_review_assignment_guards_list_detail_and_write(authorization_client) -> None:  # type: ignore[no-untyped-def]
    client, selected, factory = authorization_client
    selected["actor"] = Actor(subject="account-annotator", roles=frozenset({"annotator"}))
    assert client.get("/api/review-cases").json() == []
    detail = client.get(f"/api/review-cases/{REVIEW_ID}")
    assert detail.status_code == 403
    assert RESTRICTED_TEXT not in detail.text
    denied_label = client.post(f"/api/review-cases/{REVIEW_ID}/label", json=LABEL)
    assert denied_label.status_code == 403
    assert RESTRICTED_TEXT not in denied_label.text
    assert client.post(f"/api/review-cases/{REVIEW_ID}/reopen", json={"reason": "Review again"}).status_code == 403
    selected["actor"] = Actor(
        subject="account-operator", roles=frozenset({"operator", "annotator"}),
    )
    assert client.post(f"/api/review-cases/{REVIEW_ID}/label", json=LABEL).status_code == 403
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ReviewAnnotationModel)) == 0

    selected["actor"] = Actor(subject="account-operator", roles=frozenset({"operator"}))
    invalid = client.patch(f"/api/review-cases/{REVIEW_ID}/assignments", json={
        "account_ids": ["account-operator"],
    })
    assert invalid.status_code == 422
    assigned = client.patch(f"/api/review-cases/{REVIEW_ID}/assignments", json={
        "account_ids": ["account-annotator"],
    })
    assert assigned.status_code == 200, assigned.text
    assert assigned.json() == ["account-annotator"]
    assert client.get(f"/api/review-cases/{REVIEW_ID}/assignments").json() == ["account-annotator"]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ReviewCaseAssignmentModel)) == 1

    selected["actor"] = Actor(subject="account-annotator", roles=frozenset({"annotator"}))
    assert [item["id"] for item in client.get("/api/review-cases").json()] == [REVIEW_ID]
    visible = client.get(f"/api/review-cases/{REVIEW_ID}")
    assert visible.status_code == 200
    assert RESTRICTED_TEXT in visible.json()["response_text"]

    selected["actor"] = Actor(
        subject="account-annotator-other", roles=frozenset({"annotator"}),
    )
    assert client.get("/api/review-cases").json() == []
    other_detail = client.get(f"/api/review-cases/{REVIEW_ID}")
    assert other_detail.status_code == 403
    assert RESTRICTED_TEXT not in other_detail.text

    selected["actor"] = Actor(subject="account-operator", roles=frozenset({"operator"}))
    assert client.patch(f"/api/review-cases/{REVIEW_ID}/assignments", json={"account_ids": []}).status_code == 200
    selected["actor"] = Actor(subject="account-annotator", roles=frozenset({"annotator"}))
    assert client.get(f"/api/review-cases/{REVIEW_ID}").status_code == 403
