"""Add source to personal API tokens.

Revision ID: 20260929_02
Revises: 20260929_01
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260929_02"
down_revision: str | None = "20260929_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("api_tokens") as batch_op:
        batch_op.add_column(
            sa.Column(
                "source",
                sa.String(length=32),
                server_default="harness",
                nullable=False,
            )
        )
        batch_op.create_index("ix_api_tokens_source", ["source"])


def downgrade() -> None:
    with op.batch_alter_table("api_tokens") as batch_op:
        batch_op.drop_index("ix_api_tokens_source")
        batch_op.drop_column("source")
