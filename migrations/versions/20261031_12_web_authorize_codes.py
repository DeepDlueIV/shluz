"""Одноразовые коды authorize-флоу для настольных клиентов."""

import sqlalchemy as sa
from alembic import op

revision = "20261031_12"
down_revision = "20261020_11"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "web_authorize_codes",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "account_id", sa.String(64), sa.ForeignKey("accounts.id"), nullable=False
        ),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("client", sa.String(32), nullable=False),
        sa.Column("redirect_port", sa.Integer(), nullable=True),
        sa.Column("created", sa.Integer(), nullable=False),
        sa.Column("expires", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False),
    )
    op.create_index(
        "ix_web_authorize_codes_account_id", "web_authorize_codes", ["account_id"]
    )
    op.create_index("ix_web_authorize_codes_expires", "web_authorize_codes", ["expires"])


def downgrade():
    op.drop_table("web_authorize_codes")
