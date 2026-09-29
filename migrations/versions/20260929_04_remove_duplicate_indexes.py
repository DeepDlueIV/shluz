"""Remove indexes duplicated by unique constraints.

Revision ID: 20260929_04
Revises: 20260929_03
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260929_04"
down_revision: str | None = "20260929_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_plans_code", table_name="plans")
    op.drop_index("ix_api_tokens_token_hash", table_name="api_tokens")


def downgrade() -> None:
    op.create_index("ix_plans_code", "plans", ["code"], unique=True)
    op.create_index(
        "ix_api_tokens_token_hash",
        "api_tokens",
        ["token_hash"],
        unique=True,
    )
