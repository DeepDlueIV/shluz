"""Пожизненное демо и серверные права моделей, без изменения прежних подписок."""

import sqlalchemy as sa
from alembic import op

revision = "20261018_07"
down_revision = "20261018_06"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "product_policy",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("demo_requests", sa.Integer(), nullable=False),
        sa.Column("demo_models_json", sa.String(22000), nullable=False),
        sa.Column("demo_max_input_chars", sa.Integer(), nullable=False),
        sa.Column("demo_max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("demo_daily_requests", sa.Integer(), nullable=False),
        sa.Column("requests_per_minute", sa.Integer(), nullable=False),
    )
    op.create_table(
        "demo_grants",
        sa.Column(
            "account_id",
            sa.String(64),
            sa.ForeignKey("accounts.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("requests_used", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "model_policies",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("models_json", sa.String(22000), nullable=False),
    )
    op.add_column(
        "usage_events",
        sa.Column("quota_kind", sa.String(16), nullable=False, server_default="monthly"),
    )
    op.add_column(
        "usage_events",
        sa.Column("cost_known", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.execute(
        "UPDATE usage_events SET cost_known = false WHERE provider != 'mock' AND cost_usd = 0"
    )
    if op.get_bind().dialect.name == "postgresql":
        for table in ("product_policy", "demo_grants", "model_policies"):
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_column("usage_events", "cost_known")
    op.drop_column("usage_events", "quota_kind")
    op.drop_table("model_policies")
    op.drop_table("demo_grants")
    op.drop_table("product_policy")
