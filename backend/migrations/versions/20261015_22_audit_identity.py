"""Distinguish verified local actions from historical demo audit text.

Revision ID: 20261015_22
Revises: 20261015_21
"""

import sqlalchemy as sa
from alembic import op

revision = "20261015_22"
down_revision = "20261015_21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_events", sa.Column(
        "identity_source", sa.String(32), nullable=False,
        server_default="local_unverified",
    ))
    op.add_column("audit_events", sa.Column(
        "outcome", sa.String(24), nullable=False, server_default="succeeded",
    ))
    op.add_column("audit_events", sa.Column(
        "actor_roles", sa.JSON(), nullable=False, server_default="[]",
    ))
    op.add_column("audit_events", sa.Column(
        "object_version", sa.String(128), nullable=False, server_default="",
    ))


def downgrade() -> None:
    op.drop_column("audit_events", "object_version")
    op.drop_column("audit_events", "actor_roles")
    op.drop_column("audit_events", "outcome")
    op.drop_column("audit_events", "identity_source")
