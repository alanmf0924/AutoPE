"""separate review-required outcomes from evaluation failures

Revision ID: 20261013_07
Revises: 20261012_06
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

revision = "20261013_07"
down_revision = "20261012_06"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    dataset_case_columns = _columns("dataset_cases")
    if "expected_contract" not in dataset_case_columns:
        op.add_column(
            "dataset_cases",
            sa.Column("expected_contract", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        )

    report_columns = _columns("evaluation_reports")
    if "passed_cases" not in report_columns:
        op.add_column(
            "evaluation_reports",
            sa.Column("passed_cases", sa.Integer(), nullable=False, server_default="0"),
        )
    if "manual_review_cases" not in report_columns:
        op.add_column(
            "evaluation_reports",
            sa.Column("manual_review_cases", sa.Integer(), nullable=False, server_default="0"),
        )
    if "execution_error_cases" not in report_columns:
        op.add_column(
            "evaluation_reports",
            sa.Column("execution_error_cases", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    report_columns = _columns("evaluation_reports")
    if "execution_error_cases" in report_columns:
        op.drop_column("evaluation_reports", "execution_error_cases")
    if "manual_review_cases" in report_columns:
        op.drop_column("evaluation_reports", "manual_review_cases")
    if "passed_cases" in report_columns:
        op.drop_column("evaluation_reports", "passed_cases")
    if "expected_contract" in _columns("dataset_cases"):
        op.drop_column("dataset_cases", "expected_contract")
