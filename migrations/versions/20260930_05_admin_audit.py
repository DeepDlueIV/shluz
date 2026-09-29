"""Добавить закрытый журнал административных изменений.

Revision ID: 20260930_05
Revises: 20260929_04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260930_05"
down_revision: str | None = "20260929_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
    )
    for column in ("created_at", "action", "target_id"):
        op.create_index(f"ix_admin_events_{column}", "admin_events", [column])

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE admin_events ENABLE ROW LEVEL SECURITY")
        op.execute("REVOKE ALL ON TABLE admin_events FROM PUBLIC")
        # Обычный PostgreSQL может не содержать специальных ролей Supabase.
        op.execute("""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                    REVOKE ALL ON TABLE admin_events FROM anon;
                END IF;
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                    REVOKE ALL ON TABLE admin_events FROM authenticated;
                END IF;
            END $$;
        """)


def downgrade() -> None:
    op.drop_table("admin_events")
