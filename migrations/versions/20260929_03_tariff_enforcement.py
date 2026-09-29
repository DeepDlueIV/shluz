"""Add tariff enforcement and historical billing fields.

Revision ID: 20260929_03
Revises: 20260929_02
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260929_03"
down_revision: str | None = "20260929_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("plans") as batch_op:
        batch_op.add_column(
            sa.Column(
                "monthly_price_usd",
                sa.Numeric(18, 2),
                server_default="0",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("monthly_cost_limit_usd", sa.Numeric(18, 8), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "request_credit_reserve",
                sa.Integer(),
                server_default="0",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "markup_percent",
                sa.Numeric(7, 2),
                server_default="0",
                nullable=False,
            )
        )

    with op.batch_alter_table("usage_events") as batch_op:
        batch_op.add_column(sa.Column("plan_id", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column(
                "billed_usd",
                sa.Numeric(18, 8),
                server_default="0",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "pricing_markup_percent",
                sa.Numeric(7, 2),
                server_default="0",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "reserved_credits",
                sa.Integer(),
                server_default="0",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("reservation_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_usage_events_plan_id_plans",
            "plans",
            ["plan_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_usage_events_plan_id", ["plan_id"])
        batch_op.create_index(
            "ix_usage_events_reservation_expires_at",
            ["reservation_expires_at"],
        )


def downgrade() -> None:
    with op.batch_alter_table("usage_events") as batch_op:
        batch_op.drop_index("ix_usage_events_reservation_expires_at")
        batch_op.drop_index("ix_usage_events_plan_id")
        batch_op.drop_constraint("fk_usage_events_plan_id_plans", type_="foreignkey")
        batch_op.drop_column("completed_at")
        batch_op.drop_column("reservation_expires_at")
        batch_op.drop_column("reserved_credits")
        batch_op.drop_column("pricing_markup_percent")
        batch_op.drop_column("billed_usd")
        batch_op.drop_column("plan_id")

    with op.batch_alter_table("plans") as batch_op:
        batch_op.drop_column("markup_percent")
        batch_op.drop_column("request_credit_reserve")
        batch_op.drop_column("monthly_cost_limit_usd")
        batch_op.drop_column("monthly_price_usd")
