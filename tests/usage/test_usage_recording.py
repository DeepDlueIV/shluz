from decimal import Decimal
from importlib import import_module

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select

from app.config import Settings
from app.db.database import Database
from app.db.models import UsageEvent
from app.db.repositories import ensure_bootstrap_account
from app.factory import create_app
from app.providers.base import ChatRequest, ChatResult, ModelInfo
from app.providers.registry import ProviderRegistry


def _database(tmp_path) -> Database:
    settings = Settings(
        database_url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'usage.db'}")
    )
    database = Database(settings)
    database.create_schema()
    with database.session() as session:
        ensure_bootstrap_account(session)
    return database


def _usage_service(database: Database):
    service_module = import_module("app.usage.service")
    return service_module.UsageService(database)


def test_usage_service_records_cost_tokens_and_attribution_without_prompt_text(tmp_path):
    database = _database(tmp_path)
    service = _usage_service(database)
    result = ChatResult(
        content="Ответ, который тоже не должен сохраняться",
        prompt_tokens=120,
        completion_tokens=35,
        request_id="venice-request-1",
        cost_usd=Decimal("0.0042"),
        cost_diem=Decimal("0"),
    )

    service.record_success(
        account_id="bootstrap",
        source="bootstrap",
        provider="venice",
        model="venice-uncensored",
        result=result,
    )

    with database.session() as session:
        event = session.scalar(select(UsageEvent))

    assert event is not None
    assert event.account_id == "bootstrap"
    assert event.source == "bootstrap"
    assert event.provider == "venice"
    assert event.model == "venice-uncensored"
    assert event.provider_request_id == "venice-request-1"
    assert event.prompt_tokens == 120
    assert event.completion_tokens == 35
    assert event.cost_usd == Decimal("0.00420000")
    assert event.cost_diem == Decimal("0E-8")
    assert event.status == "success"
    assert not hasattr(event, "prompt")
    assert not hasattr(event, "messages")
    assert not hasattr(event, "content")


def test_usage_service_deduplicates_provider_request_id(tmp_path):
    database = _database(tmp_path)
    service = _usage_service(database)
    result = ChatResult(
        content="Ответ",
        request_id="same-provider-request",
        cost_usd=Decimal("0.01"),
    )

    for _ in range(2):
        service.record_success(
            account_id="bootstrap",
            source="bootstrap",
            provider="venice",
            model="model-a",
            result=result,
        )

    with database.session() as session:
        event_count = session.scalar(select(func.count()).select_from(UsageEvent))

    assert event_count == 1


class CostedProvider:
    @property
    def name(self) -> str:
        return "costed"

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="costed-chat", provider=self.name)]

    def chat_completion(self, request: ChatRequest) -> ChatResult:
        return ChatResult(
            content="Готово",
            prompt_tokens=9,
            completion_tokens=4,
            request_id="costed-request-1",
            cost_usd=Decimal("0.0025"),
        )


def test_chat_endpoint_records_one_successful_usage_event(tmp_path):
    settings = Settings(
        bootstrap_api_token=SecretStr("test-api-token"),
        admin_username="test-admin",
        admin_password=SecretStr("test-admin-password"),
        database_url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'api-usage.db'}"),
    )
    database = Database(settings)
    app = create_app(
        settings,
        registry=ProviderRegistry([CostedProvider()]),
        database=database,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test-api-token"},
        json={
            "model": "costed-chat",
            "messages": [{"role": "user", "content": "Сделай работу"}],
        },
    )

    assert response.status_code == 200
    with database.session() as session:
        events = list(session.scalars(select(UsageEvent)))

    assert len(events) == 1
    assert events[0].account_id == "bootstrap"
    assert events[0].provider == "costed"
    assert events[0].model == "costed-chat"
    assert events[0].prompt_tokens == 9
    assert events[0].completion_tokens == 4
    assert events[0].cost_usd == Decimal("0.00250000")
