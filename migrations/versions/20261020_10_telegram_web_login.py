"""Одноразовые nonce официального Telegram Login; аккаунты и квоты сохраняются."""

import sqlalchemy as sa
from alembic import op

revision = "20261020_10"
down_revision = "20261019_09"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "web_telegram_login_challenges",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("nonce_hash", sa.String(64), nullable=False),
        sa.Column("created", sa.Integer(), nullable=False),
        sa.Column("expires", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    for name in ("session_id", "expires"):
        op.create_index("ix_web_telegram_login_challenges_" + name,
                        "web_telegram_login_challenges", [name])


def downgrade():
    op.drop_table("web_telegram_login_challenges")
