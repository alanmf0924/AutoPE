"""Pin Dify targets to an actual published workflow version when supplied.

Revision ID: 20261014_13
Revises: 20261014_12
"""

import sqlalchemy as sa
from alembic import op

revision = "20261014_13"
down_revision = "20261014_12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("dify_targets")}
    if "published_workflow_id" not in columns:
        op.add_column(
            "dify_targets",
            sa.Column("published_workflow_id", sa.String(36), nullable=False, server_default=""),
        )


def downgrade() -> None:
    op.drop_column("dify_targets", "published_workflow_id")
