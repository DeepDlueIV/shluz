"""Durable stream timing and accounting certainty; no content capture."""

import sqlalchemy as sa
from alembic import op

revision = "20261019_08"
down_revision = "20261018_07"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "usage_events",
        sa.Column("streamed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "usage_events",
        sa.Column("usage_known", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("usage_events", sa.Column("first_token_ms", sa.Float(), nullable=True))
    op.add_column("usage_events", sa.Column("duration_ms", sa.Float(), nullable=True))


def downgrade():
    for name in ("duration_ms", "first_token_ms", "usage_known", "streamed"):
        op.drop_column("usage_events", name)
