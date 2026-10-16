from __future__ import annotations

import json

from sqlalchemy import select

from app.db import SessionLocal
from app.dspy_candidate import generate_candidate
from app.models import DifyTargetModel
from app.real_business_golden import create_business_golden_dataset


def main() -> None:
    with SessionLocal() as session:
        dataset = create_business_golden_dataset(session)
        baseline = session.scalar(
            select(DifyTargetModel).where(DifyTargetModel.id == "target_c82a5b06972c")
        )
        if baseline is None:
            raise RuntimeError("The configured published Dify baseline target was not found.")
        generated = generate_candidate(session, dataset=dataset, baseline=baseline)
        print(
            json.dumps(
                {
                    "dataset_id": dataset.id,
                    "dataset_cases": dataset.case_count,
                    "candidate_id": generated.candidate.id,
                    "candidate_status": generated.candidate.status,
                    "prompt_sha256": generated.candidate.prompt_sha256,
                    "rationale": generated.rationale,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
