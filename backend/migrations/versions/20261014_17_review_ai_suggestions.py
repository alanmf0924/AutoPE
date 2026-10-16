"""Keep AI review drafts separate from human decisions.

Revision ID: 20261014_17
Revises: 20261014_16
"""

import sqlalchemy as sa
from alembic import op

revision = "20261014_17"
down_revision = "20261014_16"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    if "review_ai_suggestions" not in sa.inspect(connection).get_table_names():
        op.create_table(
        "review_ai_suggestions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("case_id", sa.String(64), sa.ForeignKey("review_cases.id"), nullable=False, unique=True),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("evaluation_runs.id"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("score_breakdown", sa.JSON(), nullable=False),
        sa.Column("risk_level", sa.String(32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("endpoint", sa.String(2048), nullable=False),
        sa.Column("requested_by", sa.String(160), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        )
    indexes = {item["name"] for item in sa.inspect(connection).get_indexes("review_ai_suggestions")}
    if "ix_review_ai_suggestions_case_id" not in indexes:
        op.create_index("ix_review_ai_suggestions_case_id", "review_ai_suggestions", ["case_id"])
    if "ix_review_ai_suggestions_run_id" not in indexes:
        op.create_index("ix_review_ai_suggestions_run_id", "review_ai_suggestions", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_review_ai_suggestions_run_id", table_name="review_ai_suggestions")
    op.drop_index("ix_review_ai_suggestions_case_id", table_name="review_ai_suggestions")
    op.drop_table("review_ai_suggestions")
