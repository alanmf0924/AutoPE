"""Assign review cases to verified local annotators.

Revision ID: 20261015_20
Revises: 20261015_19
"""

import sqlalchemy as sa
from alembic import op

revision = "20261015_20"
down_revision = "20261015_19"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_case_assignments",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("case_id", sa.String(64), sa.ForeignKey("review_cases.id"), nullable=False),
        sa.Column("account_id", sa.String(64), sa.ForeignKey("local_accounts.id"), nullable=False),
        sa.Column("assigned_by", sa.String(64), sa.ForeignKey("local_accounts.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_id", "account_id", name="uq_review_case_assignment"),
    )
    op.create_index("ix_review_case_assignments_case_id", "review_case_assignments", ["case_id"])
    op.create_index("ix_review_case_assignments_account_id", "review_case_assignments", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_review_case_assignments_account_id", table_name="review_case_assignments")
    op.drop_index("ix_review_case_assignments_case_id", table_name="review_case_assignments")
    op.drop_table("review_case_assignments")
