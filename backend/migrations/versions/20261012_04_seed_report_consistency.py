"""align seeded report failure count with review detail

Revision ID: 20261012_04
Revises: 20261012_03
Create Date: 2026-10-12
"""

from alembic import op

revision = "20261012_04"
down_revision = "20261012_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE evaluation_reports SET failed_cases = 1 WHERE run_id = 'run_seed_20261012'")


def downgrade() -> None:
    op.execute("UPDATE evaluation_reports SET failed_cases = 2 WHERE run_id = 'run_seed_20261012'")
