"""add durable evaluation worker runtime

Revision ID: 20261012_05
Revises: 20261012_04
Create Date: 2026-10-12
"""

import sqlalchemy as sa
from alembic import op

revision = "20261012_05"
down_revision = "20261012_04"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    dataset_case_columns = _columns("dataset_cases")
    if "evaluation_rules" not in dataset_case_columns:
        op.add_column(
            "dataset_cases",
            sa.Column("evaluation_rules", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        )

    run_columns = _columns("evaluation_runs")
    if "claimed_by" not in run_columns:
        op.add_column("evaluation_runs", sa.Column("claimed_by", sa.String(length=160), nullable=True))
    if "lease_expires_at" not in run_columns:
        op.add_column(
            "evaluation_runs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
    if "attempt_count" not in run_columns:
        op.add_column(
            "evaluation_runs",
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        )
    if "last_error" not in run_columns:
        op.add_column(
            "evaluation_runs",
            sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        )

    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("evaluation_runs")}
    if "ix_evaluation_runs_claimed_by" not in indexes:
        op.create_index("ix_evaluation_runs_claimed_by", "evaluation_runs", ["claimed_by"])
    if "ix_evaluation_runs_lease_expires_at" not in indexes:
        op.create_index(
            "ix_evaluation_runs_lease_expires_at", "evaluation_runs", ["lease_expires_at"]
        )

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "evaluation_case_results" not in tables:
        op.create_table(
            "evaluation_case_results",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("run_id", sa.String(length=64), sa.ForeignKey("evaluation_runs.id"), nullable=False),
            sa.Column("dataset_case_id", sa.String(length=64), sa.ForeignKey("dataset_cases.id"), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("baseline_response", sa.Text(), nullable=False, server_default=""),
            sa.Column("candidate_response", sa.Text(), nullable=False, server_default=""),
            sa.Column("baseline_scores", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("candidate_scores", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("automatic_score_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("baseline_latency_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("candidate_latency_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_code", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("run_id", "dataset_case_id", name="uq_evaluation_case_result"),
        )
        op.create_index(
            "ix_evaluation_case_results_run_id", "evaluation_case_results", ["run_id"]
        )
        op.create_index(
            "ix_evaluation_case_results_dataset_case_id",
            "evaluation_case_results",
            ["dataset_case_id"],
        )
    if "worker_heartbeats" not in tables:
        op.create_table(
            "worker_heartbeats",
            sa.Column("worker_id", sa.String(length=160), primary_key=True),
            sa.Column("state", sa.String(length=32), nullable=False),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_worker_heartbeats_heartbeat_at", "worker_heartbeats", ["heartbeat_at"])


def downgrade() -> None:
    op.drop_index("ix_worker_heartbeats_heartbeat_at", table_name="worker_heartbeats")
    op.drop_table("worker_heartbeats")
    op.drop_index("ix_evaluation_case_results_dataset_case_id", table_name="evaluation_case_results")
    op.drop_index("ix_evaluation_case_results_run_id", table_name="evaluation_case_results")
    op.drop_table("evaluation_case_results")
    op.drop_index("ix_evaluation_runs_lease_expires_at", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_claimed_by", table_name="evaluation_runs")
    op.drop_column("evaluation_runs", "last_error")
    op.drop_column("evaluation_runs", "attempt_count")
    op.drop_column("evaluation_runs", "lease_expires_at")
    op.drop_column("evaluation_runs", "claimed_by")
    op.drop_column("dataset_cases", "evaluation_rules")
