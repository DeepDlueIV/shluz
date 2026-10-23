"""Изолированный сервер для настоящего JS SDK; только mock и временная БД CI."""

import os

from pydantic import SecretStr

from app.config import Settings
from app.db.models import Account, Plan, Subscription
from app.db.repositories import create_api_token
from app.factory import create_app

# Без import в production: этот модуль запускается только отдельным шагом CI.
settings = Settings(
    environment="test",
    active_provider="mock",
    venice_api_key=None,
    database_url=SecretStr(os.environ["SDK_TEST_DATABASE"]),
    bootstrap_api_token=SecretStr("sdk-only-bootstrap-unused"),
    admin_password=SecretStr("sdk-only-owner-unused"),
)
app = create_app(settings)
with app.state.database.session() as db:
    account = Account(display_name="SDK integration fixture")
    plan = Plan(code="sdk", name="SDK fixture", monthly_request_limit=8)
    db.add_all([account, plan])
    db.flush()
    db.add(Subscription(account_id=account.id, plan_id=plan.id, source="harness"))
    create_api_token(
        db,
        account_id=account.id,
        source="harness",
        name="SDK fixture",
        raw_token=os.environ["SDK_TEST_TOKEN"],
    )
