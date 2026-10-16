"""separate baseline runs from DSPy comparison candidates

Revision ID: 20261012_06
Revises: 20261012_05
Create Date: 2026-10-12
"""

import sqlalchemy as sa
from alembic import op

revision = "20261012_06"
down_revision = "20261012_05"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    candidate_columns = _columns("prompt_candidates")
    if "prompt_block" not in candidate_columns:
        op.add_column(
            "prompt_candidates",
            sa.Column("prompt_block", sa.Text(), nullable=False, server_default=""),
        )
    if "bound_target_id" not in candidate_columns:
        with op.batch_alter_table("prompt_candidates") as batch:
            batch.add_column(sa.Column("bound_target_id", sa.String(length=64), nullable=True))
            batch.create_foreign_key(
                "fk_prompt_candidates_bound_target_id",
                "dify_targets",
                ["bound_target_id"],
                ["id"],
            )
            batch.create_index("ix_prompt_candidates_bound_target_id", ["bound_target_id"])

    run_columns = _columns("evaluation_runs")
    if "run_mode" not in run_columns:
        op.add_column(
            "evaluation_runs",
            sa.Column("run_mode", sa.String(length=24), nullable=False, server_default="comparison"),
        )
    with op.batch_alter_table("evaluation_runs") as batch:
        batch.alter_column("candidate_target_id", existing_type=sa.String(length=64), nullable=True)
        batch.alter_column("candidate_id", existing_type=sa.String(length=64), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("evaluation_runs") as batch:
        batch.alter_column("candidate_id", existing_type=sa.String(length=64), nullable=False)
        batch.alter_column("candidate_target_id", existing_type=sa.String(length=64), nullable=False)
    op.drop_column("evaluation_runs", "run_mode")
    with op.batch_alter_table("prompt_candidates") as batch:
        batch.drop_index("ix_prompt_candidates_bound_target_id")
        batch.drop_constraint("fk_prompt_candidates_bound_target_id", type_="foreignkey")
        batch.drop_column("bound_target_id")
    op.drop_column("prompt_candidates", "prompt_block")
