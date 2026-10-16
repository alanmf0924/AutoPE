"""Record manual candidate-to-published-version attestation.

Revision ID: 20261014_16
Revises: 20261014_15
"""

import sqlalchemy as sa
from alembic import op

revision = "20261014_16"
down_revision = "20261014_15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    columns = {item["name"] for item in sa.inspect(connection).get_columns("prompt_candidates")}
    if "binding_attestation" not in columns:
        op.add_column("prompt_candidates", sa.Column("binding_attestation", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("prompt_candidates", "binding_attestation")
