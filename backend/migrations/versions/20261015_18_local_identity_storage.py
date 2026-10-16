"""Add independent local account, role, session and one-time credential storage.

Revision ID: 20261015_18
Revises: 20261014_17
"""

import sqlalchemy as sa
from alembic import op

revision = "20261015_18"
down_revision = "20261014_17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "local_accounts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("login_name", sa.String(160), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("password_hash", sa.String(512)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("is_platform_admin", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("login_name", name="uq_local_accounts_login_name"),
    )
    op.create_table(
        "local_role_grants",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("account_id", sa.String(64), sa.ForeignKey("local_accounts.id"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("account_id", "role", name="uq_local_role_grant"),
    )
    op.create_index("ix_local_role_grants_account_id", "local_role_grants", ["account_id"])
    op.create_table(
        "local_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("account_id", sa.String(64), sa.ForeignKey("local_accounts.id"), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False),
        sa.Column("csrf_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("token_digest", name="uq_local_sessions_token_digest"),
    )
    op.create_index("ix_local_sessions_account_id", "local_sessions", ["account_id"])
    op.create_table(
        "local_one_time_credentials",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("purpose", sa.String(24), nullable=False),
        sa.Column("account_id", sa.String(64), sa.ForeignKey("local_accounts.id")),
        sa.Column("token_digest", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token_digest", name="uq_local_one_time_credentials_token_digest"),
    )
    op.create_index(
        "ix_local_one_time_credentials_account_id", "local_one_time_credentials", ["account_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_local_one_time_credentials_account_id", table_name="local_one_time_credentials")
    op.drop_table("local_one_time_credentials")
    op.drop_index("ix_local_sessions_account_id", table_name="local_sessions")
    op.drop_table("local_sessions")
    op.drop_index("ix_local_role_grants_account_id", table_name="local_role_grants")
    op.drop_table("local_role_grants")
    op.drop_table("local_accounts")
