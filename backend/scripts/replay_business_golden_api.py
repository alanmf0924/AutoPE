"""Create the approved ten-case Dify business contract set via public AutoPE APIs."""

from __future__ import annotations

import json

import httpx

from app.real_business_golden import SOURCE_VERSION, business_golden_cases


def main() -> None:
    cases = business_golden_cases()
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=15, trust_env=False) as client:
        existing = client.get("/api/datasets")
        existing.raise_for_status()
        if existing.json():
            raise RuntimeError("Dataset list is not empty; refusing to create a duplicate business golden set.")
        created = client.post("/api/datasets", json={
            "name": "护肤选购业务契约 · 正式流程复测",
            "description": f"来源版本：{SOURCE_VERSION}；已发布 Dify Chatflow 的业务流程契约，无虚构 SKU/价格/库存。",
            "case_count": len(cases),
        })
        created.raise_for_status()
        dataset_id = created.json()["id"]
        uploaded = client.post(f"/api/datasets/{dataset_id}/cases", json={"cases": [{
            "title": case.title,
            "prompt": case.prompt,
            "expected_behavior": json.dumps(case.expected_contract, ensure_ascii=False, sort_keys=True),
            "expected_contract": case.expected_contract,
            "evaluation_rules": {},
            "risk_level": case.risk_level,
        } for case in cases]})
        uploaded.raise_for_status()
        frozen = client.post(f"/api/datasets/{dataset_id}/freeze")
        frozen.raise_for_status()
        approved = client.post(f"/api/datasets/{dataset_id}/approve")
        approved.raise_for_status()
        print(json.dumps({"dataset_id": dataset_id, "case_count": len(cases),
                          "is_frozen": approved.json()["is_frozen"],
                          "review_status": approved.json()["review_status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
