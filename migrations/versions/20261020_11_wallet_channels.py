"""Optional wallet and channel-specific plans; no balances created or demo resets."""

import sqlalchemy as sa
from alembic import op

revision = "20261020_11"
down_revision = "20261020_10"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "plans",
        sa.Column("billing_mode", sa.String(20), nullable=False, server_default="subscription"),
    )
    op.add_column(
        "subscriptions", sa.Column("source", sa.String(32), nullable=False, server_default="*")
    )
    op.add_column(
        "usage_events", sa.Column("quota_scope", sa.String(32), nullable=False, server_default="*")
    )
    op.create_table(
        "wallets",
        sa.Column("account_id", sa.String(64), sa.ForeignKey("accounts.id"), primary_key=True),
        sa.Column("balance_usd", sa.BigInteger(), nullable=False),
        sa.Column("display_unit", sa.String(16), nullable=False),
    )
    op.create_table(
        "wallet_entries",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("account_id", sa.String(64), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("amount_usd", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("note", sa.String(200), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_wallet_entries_account_id", "wallet_entries", ["account_id"])
    op.create_table(
        "wallet_holds",
        sa.Column("event_id", sa.String(64), sa.ForeignKey("usage_events.id"), primary_key=True),
        sa.Column("account_id", sa.String(64), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("amount_usd", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("rule_id", sa.String(300), nullable=False),
    )
    op.create_index("ix_wallet_holds_account_id", "wallet_holds", ["account_id"])
    op.create_index("ix_wallet_holds_state", "wallet_holds", ["state"])
    op.create_table(
        "wallet_model_bounds",
        sa.Column("id", sa.String(300), primary_key=True),
        sa.Column("max_cost_usd", sa.BigInteger(), nullable=False),
        sa.Column("max_input_chars", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("reviewed", sa.Boolean(), nullable=False),
        sa.Column("expires", sa.Integer(), nullable=False),
    )


def downgrade():
    # Финансовые записи не удалять молча ради отката кода.
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM wallet_entries")).scalar():
        raise RuntimeError(
            "Wallet history exists: restore a coordinated backup, do not discard ledger"
        )
    for name in ("wallet_holds", "wallet_entries", "wallet_model_bounds", "wallets"):
        op.drop_table(name)
    for table, col in [
        ("usage_events", "quota_scope"),
        ("subscriptions", "source"),
        ("plans", "billing_mode"),
    ]:
        with op.batch_alter_table(table) as batch:
            batch.drop_column(col)
