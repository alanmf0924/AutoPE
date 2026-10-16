"""Record only verified contributors to current approval evidence.

Revision ID: 20261015_21
Revises: 20261015_20
"""

import sqlalchemy as sa
from alembic import op

revision = "20261015_21"
down_revision = "20261015_20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evidence_contributions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("resource_type", sa.String(32), nullable=False),
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("version_key", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(64), sa.ForeignKey("local_accounts.id"), nullable=False),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_evidence_contributions_resource_type", "evidence_contributions", ["resource_type"])
    op.create_index("ix_evidence_contributions_resource_id", "evidence_contributions", ["resource_id"])


def downgrade() -> None:
    op.drop_index("ix_evidence_contributions_resource_id", table_name="evidence_contributions")
    op.drop_index("ix_evidence_contributions_resource_type", table_name="evidence_contributions")
    op.drop_table("evidence_contributions")
