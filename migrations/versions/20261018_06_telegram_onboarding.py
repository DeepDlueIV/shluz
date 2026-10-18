"""Добавить закрытые заявки Telegram и одноразовые приглашения, сохранив все аккаунты.

Revision ID: 20261018_06
Revises: 20260930_05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261018_06"
down_revision: str | None = "20260930_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_admissions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("telegram_id", sa.String(32), nullable=False, unique=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("username", sa.String(200), nullable=True),
        sa.Column("account_id", sa.String(64), sa.ForeignKey("accounts.id", ondelete="RESTRICT")),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("legacy_token_id", sa.String(64), nullable=True),
        sa.Column("session_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    for column in ("account_id", "status"):
        op.create_index(f"ix_telegram_admissions_{column}", "telegram_admissions", [column])
    op.create_table(
        "telegram_invites",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "plan_id", sa.String(64), sa.ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("account_id", sa.String(64), sa.ForeignKey("accounts.id", ondelete="RESTRICT")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_telegram_id", sa.String(32), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_table(
        "telegram_integration",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    if op.get_bind().dialect.name == "postgresql":
        for table in ("telegram_admissions", "telegram_invites", "telegram_integration"):
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")
            for role in ("anon", "authenticated"):
                op.execute(f"""DO $$ BEGIN
                    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                        REVOKE ALL ON TABLE {table} FROM {role};
                    END IF;
                END $$;""")


def downgrade() -> None:
    op.drop_table("telegram_integration")
    op.drop_table("telegram_invites")
    op.drop_table("telegram_admissions")
