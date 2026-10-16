from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models import DatasetCaseModel, DatasetModel, DifyTargetModel, PromptCandidateModel
from app.repository import audit, new_id, now_utc


class OptimizeDifyPromptSignature:  # loaded into DSPy dynamically to avoid importing a model in tests
    pass


@dataclass(frozen=True)
class GeneratedCandidate:
    candidate: PromptCandidateModel
    rationale: str


def _business_brief() -> str:
    return """You are optimizing one LLM system prompt inside an existing Dify Chatflow for a skincare shopping assistant.
The workflow already invokes these tools, in this order when the request is in scope:
plan_selection_request, search_selection_candidates, get_selection_availability, build_selection_bundle, retrieve_selection_evidence.
The prompt must use the Dify built-in user message variable exactly as {{#sys.query#}} and the configured evidence context exactly as {{#context#}}. Do not use a custom start-node variable such as [用户输入/query].
Only make concrete recommendations from the supplied tool outputs and cited reviewed evidence. Never invent SKU, price, stock, ingredients, evidence, order status, or product efficacy.
Orders, logistics, payment, refund, invoice, and cart actions are out of scope. For them, state that only skincare shopping advice is supported and direct the user to customer service.
For discomfort, rash, or other safety risk, do not diagnose or prescribe; recommend professional assistance. Do not expose chain-of-thought, tool JSON, or internal rules.
If candidate data, availability, a compliant bundle, or reviewed evidence is absent, return the corresponding no-recommendation fallback instead of guessing.
Write concise Chinese. Return only the production-ready prompt text, with no Markdown fence, explanation, or placeholder product facts."""


def _case_contracts(session: Session, dataset_id: str) -> str:
    cases = list(
        session.query(DatasetCaseModel)
        .filter(DatasetCaseModel.dataset_id == dataset_id)
        .order_by(DatasetCaseModel.position)
    )
    if not cases:
        raise ValueError("The business golden dataset contains no cases.")
    return json.dumps(
        [
            {
                "prompt": case.prompt,
                "risk_level": case.risk_level,
                "expected_contract": case.expected_contract,
            }
            for case in cases
        ],
        ensure_ascii=False,
        sort_keys=True,
    )


def _validate_candidate(prompt: str) -> str:
    candidate = prompt.strip()
    required = (
        "{{#sys.query#}}",
        "{{#context#}}",
        "plan_selection_request",
        "search_selection_candidates",
        "get_selection_availability",
        "build_selection_bundle",
        "retrieve_selection_evidence",
        "订单",
        "退款",
        "发票",
        "专业人士",
    )
    missing = [term for term in required if term not in candidate]
    if missing:
        raise ValueError(f"DSPy output is missing mandatory business constraints: {', '.join(missing)}")
    if "[用户输入/query]" in candidate or len(candidate) > 12000:
        raise ValueError("DSPy output contains an invalid input variable or exceeds the prompt size limit.")
    return candidate


def generate_candidate(session: Session, *, dataset: DatasetModel, baseline: DifyTargetModel) -> GeneratedCandidate:
    try:
        import dspy
    except ImportError as error:  # pragma: no cover - installed in the runtime project
        raise RuntimeError("dspy-ai is not installed.") from error

    model = os.getenv("AUTOPE_DSPY_MODEL", "").strip()
    api_key = os.getenv("AUTOPE_DSPY_API_KEY", os.getenv("OPENAI_API_KEY", "")).strip()
    base_url = os.getenv("AUTOPE_DSPY_BASE_URL", "").strip()
    if not model or not api_key:
        raise RuntimeError("AUTOPE_DSPY_MODEL and AUTOPE_DSPY_API_KEY must be configured.")
    lm_kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        lm_kwargs["api_base"] = base_url
    dspy.configure(lm=dspy.LM(model, **lm_kwargs))

    class OptimizeDifyPrompt(dspy.Signature):
        """Produce a safe Chinese Dify system prompt that meets every supplied workflow contract."""

        business_brief: str = dspy.InputField()
        golden_contracts: str = dspy.InputField()
        optimized_prompt: str = dspy.OutputField()
        rationale: str = dspy.OutputField(desc="Short Chinese rationale, no hidden reasoning.")

    result = dspy.Predict(OptimizeDifyPrompt)(
        business_brief=_business_brief(), golden_contracts=_case_contracts(session, dataset.id)
    )
    prompt_block = _validate_candidate(str(result.optimized_prompt))
    existing = session.query(PromptCandidateModel).filter(
        PromptCandidateModel.base_version_fingerprint == baseline.version_fingerprint,
        PromptCandidateModel.prompt_sha256
        == hashlib.sha256(prompt_block.encode()).hexdigest(),
    ).one_or_none()
    if existing is not None:
        return GeneratedCandidate(candidate=existing, rationale=str(result.rationale).strip())
    candidate = PromptCandidateModel(
        id=new_id("candidate"),
        name=f"DSPy 业务流程候选 · {now_utc().astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()}",
        base_version_fingerprint=baseline.version_fingerprint,
        prompt_block=prompt_block,
        prompt_sha256=hashlib.sha256(prompt_block.encode()).hexdigest(),
        status="proposed",
        bound_target_id=None,
        created_at=now_utc(),
    )
    session.add(candidate)
    audit(
        session,
        actor_id="system:dspy-business-candidate",
        action="candidate.generated",
        resource_type="prompt_candidate",
        resource_id=candidate.id,
        request_id=new_id("dspy_request"),
        detail={
            "dataset_id": dataset.id,
            "baseline_version": baseline.version_fingerprint,
            "prompt_sha256": candidate.prompt_sha256,
            "binding": "none",
        },
        identity_source="service", object_version=candidate.prompt_sha256,
    )
    session.commit()
    return GeneratedCandidate(candidate=candidate, rationale=str(result.rationale).strip())
