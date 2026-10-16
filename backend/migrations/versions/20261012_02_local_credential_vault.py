"""add local Dify credential vault and runtime configuration

Revision ID: 20261012_02
Revises: 20261012_01
Create Date: 2026-10-12
"""

import sqlalchemy as sa
from alembic import op

revision = "20261012_02"
down_revision = "20261012_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dify_targets",
        sa.Column("application_id", sa.String(length=160), nullable=False, server_default=""),
    )
    op.add_column(
        "dify_targets",
        sa.Column("input_mapping", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "dify_targets",
        sa.Column(
            "dify_user",
            sa.String(length=160),
            nullable=False,
            server_default="autope-evaluator",
        ),
    )
    op.create_table(
        "dify_credentials",
        sa.Column("secret_ref", sa.String(length=120), primary_key=True),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("dify_credentials")
    op.drop_column("dify_targets", "dify_user")
    op.drop_column("dify_targets", "input_mapping")
    op.drop_column("dify_targets", "application_id")
