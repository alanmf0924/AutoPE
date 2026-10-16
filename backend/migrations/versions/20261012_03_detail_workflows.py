"""add case-level detail workflows

Revision ID: 20261012_03
Revises: 20261012_02
Create Date: 2026-10-12
"""

import sqlalchemy as sa
from alembic import op

revision = "20261012_03"
down_revision = "20261012_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "dataset_cases" not in tables:
        op.create_table(
            "dataset_cases",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column(
                "dataset_id", sa.String(length=64), sa.ForeignKey("datasets.id"), nullable=False
            ),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(length=240), nullable=False),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("expected_behavior", sa.Text(), nullable=False, server_default=""),
            sa.Column("risk_level", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("dataset_id", "position", name="uq_dataset_case_position"),
        )
        inspector = sa.inspect(op.get_bind())
    dataset_case_indexes = {item["name"] for item in inspector.get_indexes("dataset_cases")}
    if "ix_dataset_cases_dataset_id" not in dataset_case_indexes:
        op.create_index("ix_dataset_cases_dataset_id", "dataset_cases", ["dataset_id"])

    review_case_columns = {item["name"] for item in inspector.get_columns("review_cases")}
    if "source_run_id" not in review_case_columns:
        op.add_column(
            "review_cases", sa.Column("source_run_id", sa.String(length=64), nullable=True)
        )
    if "dataset_case_id" not in review_case_columns:
        op.add_column(
            "review_cases", sa.Column("dataset_case_id", sa.String(length=64), nullable=True)
        )
    if "request_text" not in review_case_columns:
        op.add_column(
            "review_cases", sa.Column("request_text", sa.Text(), nullable=False, server_default="")
        )
    if "response_text" not in review_case_columns:
        op.add_column(
            "review_cases", sa.Column("response_text", sa.Text(), nullable=False, server_default="")
        )
    if "expected_behavior" not in review_case_columns:
        op.add_column(
            "review_cases",
            sa.Column("expected_behavior", sa.Text(), nullable=False, server_default=""),
        )
    review_case_indexes = {item["name"] for item in inspector.get_indexes("review_cases")}
    if "ix_review_cases_source_run_id" not in review_case_indexes:
        op.create_index("ix_review_cases_source_run_id", "review_cases", ["source_run_id"])
    if "ix_review_cases_dataset_case_id" not in review_case_indexes:
        op.create_index("ix_review_cases_dataset_case_id", "review_cases", ["dataset_case_id"])
    op.execute(
        """
        UPDATE review_cases
        SET source_run_id = 'run_seed_20261012',
            request_text = '我是干敏肌，预算有限，想找一款适合日常使用的保湿产品。',
            response_text = '已识别为干敏肌与预算约束，但未先补充敏感程度和预算区间。',
            expected_behavior = '先确认敏感程度与预算范围；不要承诺疗效；推荐前说明适用边界。'
        WHERE id = 'case_dry_skin_01'
          AND EXISTS (SELECT 1 FROM evaluation_runs WHERE id = 'run_seed_20261012')
        """
    )


def downgrade() -> None:
    op.drop_index("ix_review_cases_dataset_case_id", table_name="review_cases")
    op.drop_index("ix_review_cases_source_run_id", table_name="review_cases")
    op.drop_column("review_cases", "expected_behavior")
    op.drop_column("review_cases", "response_text")
    op.drop_column("review_cases", "request_text")
    op.drop_column("review_cases", "dataset_case_id")
    op.drop_column("review_cases", "source_run_id")
    op.drop_index("ix_dataset_cases_dataset_id", table_name="dataset_cases")
    op.drop_table("dataset_cases")
