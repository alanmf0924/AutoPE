"""persistent control plane foundation

Revision ID: 20261012_01
Revises:
Create Date: 2026-10-12
"""

import sqlalchemy as sa
from alembic import op

revision = "20261012_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dify_targets",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("runtime_path", sa.String(length=256), nullable=False),
        sa.Column("target_kind", sa.String(length=32), nullable=False),
        sa.Column("secret_ref", sa.String(length=120), nullable=False),
        sa.Column("version_fingerprint", sa.String(length=180), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "datasets",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.Column("review_status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_frozen", sa.Boolean(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "prompt_candidates",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("base_version_fingerprint", sa.String(length=180), nullable=False),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "review_cases",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("dataset_id", sa.String(length=64), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("review_status", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_review_cases_dataset_id", "review_cases", ["dataset_id"])
    op.create_table(
        "review_annotations",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "case_id", sa.String(length=64), sa.ForeignKey("review_cases.id"), nullable=False
        ),
        sa.Column("reviewer_id", sa.String(length=160), nullable=False),
        sa.Column("score_breakdown", sa.JSON(), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_id", "reviewer_id", name="uq_review_annotation_reviewer"),
    )
    op.create_index("ix_review_annotations_case_id", "review_annotations", ["case_id"])
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("dataset_id", sa.String(length=64), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column(
            "baseline_target_id",
            sa.String(length=64),
            sa.ForeignKey("dify_targets.id"),
            nullable=False,
        ),
        sa.Column(
            "candidate_target_id",
            sa.String(length=64),
            sa.ForeignKey("dify_targets.id"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            sa.String(length=64),
            sa.ForeignKey("prompt_candidates.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False, unique=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "evaluation_reports",
        sa.Column(
            "run_id", sa.String(length=64), sa.ForeignKey("evaluation_runs.id"), primary_key=True
        ),
        sa.Column("gate_status", sa.String(length=16), nullable=False),
        sa.Column("pass_rates", sa.JSON(), nullable=False),
        sa.Column("score_dimensions", sa.JSON(), nullable=False),
        sa.Column("p95_latency_ms", sa.Integer(), nullable=False),
        sa.Column("average_cost_usd", sa.Float(), nullable=False),
        sa.Column("failed_cases", sa.Integer(), nullable=False),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("actor_id", sa.String(length=160), nullable=False),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_request_id", "audit_events", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_request_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("evaluation_reports")
    op.drop_table("evaluation_runs")
    op.drop_index("ix_review_annotations_case_id", table_name="review_annotations")
    op.drop_table("review_annotations")
    op.drop_index("ix_review_cases_dataset_id", table_name="review_cases")
    op.drop_table("review_cases")
    op.drop_table("prompt_candidates")
    op.drop_table("datasets")
    op.drop_table("dify_targets")
