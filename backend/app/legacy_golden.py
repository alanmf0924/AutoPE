from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import DatasetCaseModel, DatasetModel
from app.repository import audit, new_id, now_utc

MIGRATION_VERSION = "promptfoo-golden-to-dify-autope-v1"
REQUIRED_CASE_KEYS = frozenset(
    {"id", "dimension", "prompt", "expected_intent", "expected_action", "must_have_evidence"}
)


@dataclass(frozen=True)
class LegacyGoldenCase:
    source_id: str
    dimension: str
    prompt: str
    expected_contract: dict[str, Any]
    risk_level: str


def risk_level_for_source_id(source_id: str) -> str:
    if source_id.startswith("safety-"):
        return "red_line"
    if source_id.startswith("handoff-"):
        return "high_risk"
    return "normal"


def load_legacy_golden_cases(source: Path) -> tuple[str, list[LegacyGoldenCase]]:
    raw = source.read_bytes()
    source_sha256 = hashlib.sha256(raw).hexdigest()
    decoded = json.loads(raw)
    if not isinstance(decoded, list) or not decoded or len(decoded) > 500:
        raise ValueError("Golden source must be a non-empty JSON array of at most 500 cases.")
    migrated: list[LegacyGoldenCase] = []
    source_ids: set[str] = set()
    for position, item in enumerate(decoded, start=1):
        if not isinstance(item, dict) or not REQUIRED_CASE_KEYS.issubset(item):
            raise ValueError(f"Case {position} is missing a required Promptfoo golden contract field.")
        source_id = item["id"]
        dimension = item["dimension"]
        prompt = item["prompt"]
        if (
            not isinstance(source_id, str)
            or not source_id
            or not isinstance(dimension, str)
            or not dimension
            or not isinstance(prompt, str)
            or not prompt
            or source_id in source_ids
        ):
            raise ValueError(f"Case {position} has invalid or duplicate source data.")
        source_ids.add(source_id)
        contract = {key: value for key, value in item.items() if key not in {"id", "dimension", "prompt"}}
        migrated.append(
            LegacyGoldenCase(
                source_id=source_id,
                dimension=dimension,
                prompt=prompt,
                expected_contract=contract,
                risk_level=risk_level_for_source_id(source_id),
            )
        )
    return source_sha256, migrated


def migrated_expected_behavior(case: LegacyGoldenCase) -> str:
    return "原始 Promptfoo 契约（只读迁移）：\n" + json.dumps(
        {
            "source_case_id": case.source_id,
            "source_dimension": case.dimension,
            "contract": case.expected_contract,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def migrate_legacy_golden_dataset(session: Session, source: Path) -> DatasetModel:
    source_sha256, cases = load_legacy_golden_cases(source)
    existing = session.scalar(
        select(DatasetModel).where(DatasetModel.description.contains(source_sha256))
    )
    if existing is not None:
        return existing
    created_at = now_utc()
    dataset = DatasetModel(
        id=new_id("dataset"),
        name="Dify 黄金集 v1 · Promptfoo 原始镜像",
        description=(
            f"来源：{source.name}；源 SHA-256：{source_sha256}；迁移器：{MIGRATION_VERSION}。"
            "安全题映射红线，人工交接题映射高风险，其余为普通；保留原契约，未伪造 Dify 自动断言。"
        ),
        case_count=len(cases),
        review_status="approved",
        version=1,
        is_frozen=True,
        content_hash="",
        created_at=created_at,
    )
    dataset_cases = [
        DatasetCaseModel(
            id=new_id("dataset_case"),
            dataset_id=dataset.id,
            position=position,
            title=f"{case.source_id} · {case.dimension}",
            prompt=case.prompt,
            expected_behavior=migrated_expected_behavior(case),
            expected_contract={
                "source_case_id": case.source_id,
                "source_dimension": case.dimension,
                **case.expected_contract,
            },
            evaluation_rules={},
            risk_level=case.risk_level,
            created_at=created_at,
        )
        for position, case in enumerate(cases, start=1)
    ]
    normalized = [
        {
            "position": item.position,
            "title": item.title,
            "prompt": item.prompt,
            "expected_behavior": item.expected_behavior,
            "expected_contract": item.expected_contract,
            "evaluation_rules": item.evaluation_rules,
            "risk_level": item.risk_level,
        }
        for item in dataset_cases
    ]
    dataset.content_hash = hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    session.add(dataset)
    session.add_all(dataset_cases)
    audit(
        session,
        actor_id="system:legacy-golden-migration",
        action="dataset.imported",
        resource_type="dataset",
        resource_id=dataset.id,
        request_id=new_id("import_request"),
        detail={
            "migration_version": MIGRATION_VERSION,
            "source_sha256": source_sha256,
            "case_count": len(dataset_cases),
            "risk_counts": {
                risk: sum(1 for item in dataset_cases if item.risk_level == risk)
                for risk in ("normal", "high_risk", "red_line")
            },
        },
        identity_source="service", object_version=str(dataset.version),
    )
    session.commit()
    return dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate a Promptfoo golden JSON into Dify_AutoPE.")
    parser.add_argument("--source", required=True, type=Path)
    args = parser.parse_args()
    with SessionLocal() as session:
        dataset = migrate_legacy_golden_dataset(session, args.source)
    print(json.dumps({"dataset_id": dataset.id, "case_count": dataset.case_count}, ensure_ascii=False))


if __name__ == "__main__":
    main()
