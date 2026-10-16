"""Keep append-only review revisions without changing the current annotation lookup.

Revision ID: 20261014_15
Revises: 20261014_14
"""

import json
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "20261014_15"
down_revision = "20261014_14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if not inspector.has_table("review_annotation_revisions"):
        op.create_table(
            "review_annotation_revisions",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("case_id", sa.String(64), sa.ForeignKey("review_cases.id"), nullable=False),
            sa.Column("reviewer_id", sa.String(160), nullable=False),
            sa.Column("event_kind", sa.String(32), nullable=False),
            sa.Column("score_breakdown", sa.JSON(), nullable=False),
            sa.Column("risk_level", sa.String(32), nullable=False),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "ix_review_annotation_revisions_case_id" not in {
        item["name"] for item in sa.inspect(connection).get_indexes("review_annotation_revisions")
    }:
        op.create_index("ix_review_annotation_revisions_case_id", "review_annotation_revisions", ["case_id"])
    revisions = sa.Table("review_annotation_revisions", sa.MetaData(), autoload_with=connection)
    existing_ids = set(connection.execute(sa.select(revisions.c.id)).scalars())
    annotations = connection.execute(sa.text(
        "SELECT id, case_id, reviewer_id, score_breakdown, risk_level, rationale, created_at "
        "FROM review_annotations"
    )).mappings()
    rows = [{
        "id": f"revision_{item['id']}",
        "case_id": item["case_id"],
        "reviewer_id": item["reviewer_id"],
        "event_kind": "legacy_snapshot",
        "score_breakdown": json.loads(item["score_breakdown"]) if isinstance(item["score_breakdown"], str) else item["score_breakdown"],
        "risk_level": item["risk_level"],
        "rationale": item["rationale"],
        "created_at": datetime.fromisoformat(item["created_at"]) if isinstance(item["created_at"], str) else item["created_at"],
    } for item in annotations if f"revision_{item['id']}" not in existing_ids]
    if rows:
        op.bulk_insert(revisions, rows)


def downgrade() -> None:
    op.drop_index("ix_review_annotation_revisions_case_id", table_name="review_annotation_revisions")
    op.drop_table("review_annotation_revisions")
