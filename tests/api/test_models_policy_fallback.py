"""Личные токены: отсутствие явных политик при активной подписке открывает реестр."""

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.db.database import Database
from app.db.models import Account, ModelPolicy, Plan, Subscription
from app.db.repositories import create_api_token
from app.factory import create_app
from app.providers.base import ChatRequest, ChatResult, ModelInfo
from app.providers.registry import ProviderRegistry


class StubProvider:
    """Реестр провайдера, не совпадающий с demo-заглушкой mock-chat."""

    @property
    def name(self) -> str:
        return "stub"

    def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(id="stub-alpha", provider=self.name),
            ModelInfo(id="stub-beta", provider=self.name),
        ]

    def chat_completion(self, request: ChatRequest) -> ChatResult:
        return ChatResult(content=f"stub reply: {request.model}")


def _client(tmp_path) -> TestClient:
    settings = Settings(
        bootstrap_api_token=SecretStr("bootstrap"),
        admin_password=SecretStr("admin-password"),
        database_url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'policy.db'}"),
    )
    app = create_app(
        settings,
        registry=ProviderRegistry([StubProvider()]),
        database=Database(settings),
    )
    return TestClient(app)


def _personal_access(client: TestClient, raw_token: str) -> str:
    database = client.app.state.database
    with database.session() as session:
        account = Account(display_name="Policy user")
        plan = Plan(code="policy-plan", name="Policy plan")
        session.add_all([account, plan])
        session.flush()
        session.add(Subscription(account_id=account.id, plan_id=plan.id))
        create_api_token(
            session,
            account_id=account.id,
            name="Harness",
            source="harness",
            raw_token=raw_token,
        )
        return raw_token


def _models(client: TestClient, token: str):
    return client.get("/v1/models", headers={"Authorization": f"Bearer {token}"})


def _chat(client: TestClient, token: str, model: str):
    return client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": model, "messages": [{"role": "user", "content": "Привет"}]},
    )


def test_subscription_without_policies_lists_whole_registry(tmp_path):
    client = _client(tmp_path)
    token = _personal_access(client, "shluz_policy_open_token")

    response = _models(client, token)

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]] == ["stub-alpha", "stub-beta"]
    assert _chat(client, token, "stub-alpha").status_code == 200


def test_channel_and_plan_policies_intersect(tmp_path):
    client = _client(tmp_path)
    token = _personal_access(client, "shluz_policy_intersect_token")
    plan_id = None
    database = client.app.state.database
    with database.session() as session:
        plan = session.query(Plan).filter_by(code="policy-plan").one()
        plan_id = plan.id
        session.add_all(
            [
                ModelPolicy(id="channel:harness", models_json='["stub-alpha", "stub-beta"]'),
                ModelPolicy(id=f"plan:{plan_id}", models_json='["stub-beta"]'),
            ]
        )

    listed = _models(client, token)

    assert [item["id"] for item in listed.json()["data"]] == ["stub-beta"]
    assert _chat(client, token, "stub-beta").status_code == 200
    denied = _chat(client, token, "stub-alpha")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "model_not_allowed"


def test_empty_intersection_returns_clear_error(tmp_path):
    client = _client(tmp_path)
    token = _personal_access(client, "shluz_policy_empty_token")
    database = client.app.state.database
    with database.session() as session:
        plan = session.query(Plan).filter_by(code="policy-plan").one()
        session.add_all(
            [
                ModelPolicy(id="channel:harness", models_json='["stub-alpha"]'),
                ModelPolicy(id=f"plan:{plan.id}", models_json='["stub-beta"]'),
            ]
        )

    response = _models(client, token)

    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "no_models_provisioned"
    assert "provisioned" in error["message"]


def test_account_without_subscription_and_demo_reports_clear_error(tmp_path):
    client = _client(tmp_path)
    database = client.app.state.database
    with database.session() as session:
        account = Account(display_name="Bare user")
        session.add(account)
        session.flush()
        create_api_token(
            session,
            account_id=account.id,
            name="Harness",
            source="harness",
            raw_token="shluz_policy_bare_token",
        )

    response = _models(client, "shluz_policy_bare_token")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "no_models_provisioned"
