"""Audited, invalidatable human release decisions.

Revision ID: 20261013_10
Revises: 20261013_09
"""

import sqlalchemy as sa
from alembic import op

revision = "20261013_10"
down_revision = "20261013_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("release_decisions"):
        return  # development create_all may run before Alembic
    op.create_table(
        "release_decisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("evaluation_runs.id"), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("report_digest", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(160), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_release_decisions_run_id", "release_decisions", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_release_decisions_run_id", table_name="release_decisions")
    op.drop_table("release_decisions")
