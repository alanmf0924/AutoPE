"""Persist Dify runtime evidence and conservative automatic assessment.

Revision ID: 20261014_14
Revises: 20261014_13
"""

import sqlalchemy as sa
from alembic import op

revision = "20261014_14"
down_revision = "20261014_13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("evaluation_case_results")}
    if "runtime_evidence" not in columns:
        op.add_column("evaluation_case_results", sa.Column("runtime_evidence", sa.JSON(), nullable=False, server_default="{}"))
    if "automatic_assessment" not in columns:
        op.add_column("evaluation_case_results", sa.Column("automatic_assessment", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("evaluation_case_results", "automatic_assessment")
    op.drop_column("evaluation_case_results", "runtime_evidence")
