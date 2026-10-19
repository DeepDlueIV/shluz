"""web guest access OTP and demo safeguards

Revision ID: 20261019_09
Revises: 20261019_08
Create Date: 2026-10-19 14:05:20.847627
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261019_09"
down_revision: str | None = "20261019_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "demo_control",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("daily_usd", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("concurrent", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("idle_seconds", sa.Integer(), nullable=False),
        sa.Column("network_daily", sa.Integer(), nullable=False),
        sa.Column("sessions_hourly", sa.Integer(), nullable=False),
        sa.Column("sessions_global_daily", sa.Integer(), nullable=False),
        sa.Column("captcha_minute", sa.Integer(), nullable=False),
        sa.Column("otp_email_daily", sa.Integer(), nullable=False),
        sa.Column("otp_network_daily", sa.Integer(), nullable=False),
        sa.Column("otp_global_daily", sa.Integer(), nullable=False),
        sa.Column("otp_verify_minute", sa.Integer(), nullable=False),
        sa.Column("http_network_minute", sa.Integer(), nullable=False),
        sa.Column("http_global_minute", sa.Integer(), nullable=False),
        sa.Column("captcha_global_minute", sa.Integer(), nullable=False),
        sa.Column("otp_verify_global_minute", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "demo_model_bounds",
        sa.Column("id", sa.String(length=300), nullable=False),
        sa.Column("max_cost_usd", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("max_input_chars", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("reviewed", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "web_buckets",
        sa.Column("id", sa.String(length=160), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("expires", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("web_buckets", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_web_buckets_expires"), ["expires"], unique=False)

    op.create_table(
        "web_captcha_uses",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("expires", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("web_captcha_uses", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_web_captcha_uses_expires"), ["expires"], unique=False)

    op.create_table(
        "web_otp_challenges",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("sent", sa.Boolean(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False),
        sa.Column("expires", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("web_otp_challenges", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_web_otp_challenges_expires"), ["expires"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_web_otp_challenges_session_id"), ["session_id"], unique=False
        )

    op.create_table(
        "demo_limits",
        sa.Column("account_id", sa.String(length=64), nullable=False),
        sa.Column("requests_limit", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
        ),
        sa.PrimaryKeyConstraint("account_id"),
    )
    op.create_table(
        "web_sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.String(length=64), nullable=True),
        sa.Column("authenticated", sa.Boolean(), nullable=False),
        sa.Column("created", sa.Integer(), nullable=False),
        sa.Column("expires", sa.Integer(), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("web_sessions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_web_sessions_expires"), ["expires"], unique=False)

    op.create_table(
        "demo_holds",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.String(length=64), nullable=False),
        sa.Column("day", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("expires", sa.Integer(), nullable=False),
        sa.Column("uncertain", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["usage_events.id"],
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    with op.batch_alter_table("demo_holds", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_demo_holds_account_id"), ["account_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_demo_holds_day"), ["day"], unique=False)
        batch_op.create_index(batch_op.f("ix_demo_holds_expires"), ["expires"], unique=False)

    op.create_table(
        "web_requests",
        sa.Column("id", sa.String(length=110), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("expires", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["usage_events.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("web_requests", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_web_requests_expires"), ["expires"], unique=False)

    # Снимок сохраняет старый выданный пакет ДО смены стартовых настроек.
    op.execute(
        sa.text(
            "INSERT INTO demo_limits(account_id, requests_limit) "
            "SELECT account_id, COALESCE((SELECT demo_requests FROM product_policy "
            "WHERE id='default'), 3) FROM demo_grants"
        )
    )
    op.execute(
        sa.text("UPDATE product_policy SET demo_requests=1 WHERE id='default' AND demo_requests=3")
    )
    op.execute(
        sa.text(
            "UPDATE product_policy SET demo_max_input_chars=3000 "
            "WHERE id='default' AND demo_max_input_chars=24000"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("web_requests", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_web_requests_expires"))

    op.drop_table("web_requests")
    with op.batch_alter_table("demo_holds", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_demo_holds_expires"))
        batch_op.drop_index(batch_op.f("ix_demo_holds_day"))
        batch_op.drop_index(batch_op.f("ix_demo_holds_account_id"))

    op.drop_table("demo_holds")
    with op.batch_alter_table("web_sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_web_sessions_expires"))

    op.drop_table("web_sessions")
    op.drop_table("demo_limits")
    with op.batch_alter_table("web_otp_challenges", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_web_otp_challenges_session_id"))
        batch_op.drop_index(batch_op.f("ix_web_otp_challenges_expires"))

    op.drop_table("web_otp_challenges")
    with op.batch_alter_table("web_captcha_uses", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_web_captcha_uses_expires"))

    op.drop_table("web_captcha_uses")
    with op.batch_alter_table("web_buckets", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_web_buckets_expires"))

    op.drop_table("web_buckets")
    op.drop_table("demo_model_bounds")
    op.drop_table("demo_control")
