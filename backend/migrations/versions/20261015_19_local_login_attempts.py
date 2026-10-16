"""Persist local login throttling buckets across API restarts.

Revision ID: 20261015_19
Revises: 20261015_18
"""

import sqlalchemy as sa
from alembic import op

revision = "20261015_19"
down_revision = "20261015_18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "local_login_attempts",
        sa.Column("key_digest", sa.String(64), primary_key=True),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("local_login_attempts")
