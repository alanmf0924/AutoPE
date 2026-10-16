"""Persist DSPy candidate generation progress.

Revision ID: 20261013_09
Revises: 20261013_08
"""

import sqlalchemy as sa
from alembic import op

revision = "20261013_09"
down_revision = "20261013_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("candidate_generation_jobs"):
        columns = {column["name"] for column in inspector.get_columns("candidate_generation_jobs")}
        missing = {"consented_model", "consented_endpoint"} - columns
        if missing:
            count = bind.scalar(sa.text("SELECT COUNT(*) FROM candidate_generation_jobs"))
            if count:
                raise RuntimeError("Existing candidate generation jobs need manual consent review before migration.")
            if "consented_model" in missing:
                op.add_column("candidate_generation_jobs", sa.Column("consented_model", sa.String(160), nullable=False, server_default=""))
            if "consented_endpoint" in missing:
                op.add_column("candidate_generation_jobs", sa.Column("consented_endpoint", sa.String(2048), nullable=False, server_default=""))
        return
    op.create_table(
        "candidate_generation_jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("dataset_id", sa.String(64), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("baseline_target_id", sa.String(64), sa.ForeignKey("dify_targets.id"), nullable=False),
        sa.Column("consented_model", sa.String(160), nullable=False),
        sa.Column("consented_endpoint", sa.String(2048), nullable=False),
        sa.Column("candidate_id", sa.String(64), sa.ForeignKey("prompt_candidates.id")),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("claimed_by", sa.String(160)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_candidate_generation_jobs_dataset_id", "candidate_generation_jobs", ["dataset_id"])


def downgrade() -> None:
    op.drop_index("ix_candidate_generation_jobs_dataset_id", table_name="candidate_generation_jobs")
    op.drop_table("candidate_generation_jobs")
