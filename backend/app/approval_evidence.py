"""Version-bound human contribution evidence for independent approval."""

from __future__ import annotations

import hashlib
import json

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DatasetModel,
    EvidenceContributionModel,
    ReviewAnnotationModel,
)
from app.repository import new_id, now_utc


def digest_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def record_contribution(
    session: Session, *, resource_type: str, resource_id: str, version_key: str,
    actor_id: str, action: str, verified: bool,
) -> None:
    if not verified:
        return
    session.add(EvidenceContributionModel(
        id=new_id("contribution"), resource_type=resource_type, resource_id=resource_id,
        version_key=version_key, actor_id=actor_id, action=action, created_at=now_utc(),
    ))


def contribution_rows(
    session: Session, *, resource_type: str, resource_id: str, version_key: str,
) -> list[EvidenceContributionModel]:
    session.flush()
    return list(session.scalars(select(EvidenceContributionModel).where(
        EvidenceContributionModel.resource_type == resource_type,
        EvidenceContributionModel.resource_id == resource_id,
        EvidenceContributionModel.version_key == version_key,
    ).order_by(EvidenceContributionModel.id)))


def dataset_contributors(session: Session, dataset: DatasetModel) -> set[str]:
    return {item.actor_id for item in contribution_rows(
        session, resource_type="dataset", resource_id=dataset.id,
        version_key=str(dataset.version),
    )}


def dataset_evidence_digest(session: Session, dataset: DatasetModel) -> str:
    rows = contribution_rows(
        session, resource_type="dataset", resource_id=dataset.id,
        version_key=str(dataset.version),
    )
    return digest_json({
        "dataset_id": dataset.id, "version": dataset.version,
        "content_hash": dataset.content_hash, "is_frozen": dataset.is_frozen,
        "review_status": dataset.review_status,
        "contributions": [(item.id, item.actor_id, item.action) for item in rows],
    })


def annotation_version_key(annotation: ReviewAnnotationModel) -> str:
    return digest_json({
        "reviewer_id": annotation.reviewer_id,
        "scores": annotation.score_breakdown,
        "risk_level": annotation.risk_level,
        "rationale": annotation.rationale,
    })


def require_if_match(request: Request, expected_digest: str, *, verified: bool) -> None:
    if not verified:
        return  # Compatibility for explicitly isolated, unverified local demos only.
    value = request.headers.get("if-match", "").strip()
    if not value:
        raise HTTPException(status_code=428, detail="Refresh the evidence and submit its If-Match digest.")
    if value != f'"{expected_digest}"':
        raise HTTPException(status_code=412, detail="Evidence changed; refresh before submitting.")


def require_trusted_contributors(contributors: set[str], actor_id: str) -> None:
    if not contributors:
        raise HTTPException(status_code=409, detail="Current evidence needs verified re-attestation.")
    if actor_id in contributors:
        raise HTTPException(status_code=403, detail="A current evidence contributor cannot approve it.")
