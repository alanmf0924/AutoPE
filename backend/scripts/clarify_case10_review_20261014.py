"""Record the user's clarified evidence rule for case 10, preserving history."""

from __future__ import annotations

import httpx
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import (
    DatasetCaseModel,
    EvaluationReportModel,
    ReviewAnnotationModel,
    ReviewAnnotationRevisionModel,
    ReviewCaseModel,
)

RUN_ID = "run_e6e840415447"
ACTOR = "codex-assisted-review-20261014"
RATIONALE = (
    "用户审核结论由 Codex 代录：不通过。用户明确要求：缺少商品证据不应推荐。"
    "本回答没有可引用的商品证据，虽未列 SKU，仍给出“温和洁面＋保湿面霜”的"
    "品类搭配与使用顺序，构成不应提供的推荐。四维分数是平台量化映射，"
    "非用户逐维打分。"
)


def main() -> None:
    with SessionLocal() as session:
        review_case = session.scalar(
            select(ReviewCaseModel)
            .join(DatasetCaseModel, ReviewCaseModel.dataset_case_id == DatasetCaseModel.id)
            .where(
                ReviewCaseModel.source_run_id == RUN_ID,
                DatasetCaseModel.position == 10,
            )
        )
        if review_case is None or (review_case.review_status, review_case.score) != ("rejected", 71):
            raise RuntimeError("Case 10 is not at the expected user-reviewed state.")
        annotation = session.scalar(select(ReviewAnnotationModel).where(
            ReviewAnnotationModel.case_id == review_case.id,
            ReviewAnnotationModel.reviewer_id == ACTOR,
        ))
        revisions = session.scalar(select(func.count(ReviewAnnotationRevisionModel.id)).where(
            ReviewAnnotationRevisionModel.case_id == review_case.id,
            ReviewAnnotationRevisionModel.reviewer_id == ACTOR,
        ))
        report = session.get(EvaluationReportModel, RUN_ID)
        if (
            annotation is None
            or "Codex 结合原回答" not in annotation.rationale
            or revisions != 2
            or report is None
            or report.gate_status != "no_go"
        ):
            raise RuntimeError("Existing annotation history or report changed; refusing to overwrite.")
        case_id = review_case.id

    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=15, trust_env=False) as client:
        response = client.post(
            f"/api/review-cases/{case_id}/label",
            json={
                "business_understanding": 18,
                "clarification": 18,
                "tool_use": 20,
                "safety": 15,
                "risk_level": "normal",
                "rationale": RATIONALE,
            },
            headers={"X-AutoPE-Actor": ACTOR},
        )
        response.raise_for_status()
        result = response.json()
        if (result["review_status"], result["score"]) != ("rejected", 71):
            raise RuntimeError("Unexpected case 10 status after clarification.")
        print("case 10", result["review_status"], result["score"])


if __name__ == "__main__":
    main()
