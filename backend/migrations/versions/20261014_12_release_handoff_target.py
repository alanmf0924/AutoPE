"""Separate the production Dify app from the isolated candidate app in handoffs.

Revision ID: 20261014_12
Revises: 20261014_11
"""

import sqlalchemy as sa
from alembic import op

revision = "20261014_12"
down_revision = "20261014_11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("release_handoffs")}
    if "candidate_app_id" not in columns:
        op.add_column("release_handoffs", sa.Column("candidate_app_id", sa.String(160), nullable=False, server_default=""))
    if "candidate_console_url" not in columns:
        op.add_column("release_handoffs", sa.Column("candidate_console_url", sa.String(2048), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("release_handoffs", "candidate_console_url")
    op.drop_column("release_handoffs", "candidate_app_id")
