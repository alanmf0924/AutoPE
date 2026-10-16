"""Record human Dify publish and rollback handoffs without claiming API verification.

Revision ID: 20261014_11
Revises: 20261013_10
"""

import sqlalchemy as sa
from alembic import op

revision = "20261014_11"
down_revision = "20261013_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("release_handoffs"):
        return
    op.create_table(
        "release_handoffs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("decision_id", sa.String(64), sa.ForeignKey("release_decisions.id"), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("dify_app_id", sa.String(160), nullable=False),
        sa.Column("dify_console_url", sa.String(2048), nullable=False),
        sa.Column("source_workflow_id", sa.String(64), nullable=False),
        sa.Column("published_workflow_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.String(160), nullable=False),
        sa.Column("confirmed_by", sa.String(160), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_release_handoffs_decision_id", "release_handoffs", ["decision_id"])


def downgrade() -> None:
    op.drop_index("ix_release_handoffs_decision_id", table_name="release_handoffs")
    op.drop_table("release_handoffs")
