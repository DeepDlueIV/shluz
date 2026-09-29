from decimal import Decimal

from fastapi.testclient import TestClient

from app.factory import create_app
from app.providers.base import ChatRequest, ChatResult, ModelInfo
from app.providers.registry import ProviderRegistry
from app.providers.venice import (
    VeniceAccountSnapshot,
    VeniceBalance,
    VeniceRateLimit,
    VeniceUsageAnalytics,
)


class DashboardVeniceProvider:
    @property
    def name(self) -> str:
        return "venice"

    def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id="venice-uncensored",
                provider="venice",
                type="text",
                name="Venice Uncensored",
                privacy="private",
                input_price_usd=Decimal("0.5"),
                output_price_usd=Decimal("1.5"),
            )
        ]

    def list_model_catalog(self) -> list[ModelInfo]:
        return self.list_models()

    def chat_completion(self, request: ChatRequest) -> ChatResult:
        return ChatResult(content="ok")

    def get_account_snapshot(self, lookback: str = "7d") -> VeniceAccountSnapshot:
        return VeniceAccountSnapshot(
            balance=VeniceBalance(
                can_consume=True,
                consumption_currency="USD",
                usd=Decimal("25.5"),
                diem=Decimal("3"),
                diem_epoch_allocation=Decimal("100"),
            ),
            access_permitted=True,
            api_tier="paid",
            is_charged=True,
            key_expiration="2027-01-01T00:00:00.000Z",
            rate_limits=(
                VeniceRateLimit(
                    model_id="venice-uncensored",
                    limits={"RPM": 100, "TPM": 2000000},
                ),
            ),
            analytics=VeniceUsageAnalytics(
                lookback=lookback,
                total_usd=Decimal("4.25"),
                total_diem=Decimal("0"),
                total_units=21000,
                prompt_tokens=15000,
                completion_tokens=6000,
                by_model=(
                    {
                        "modelName": "Venice Uncensored",
                        "modelType": "LLM",
                        "unitType": "tokens",
                        "totalUsd": 4.25,
                        "totalDiem": 0,
                        "totalUnits": 21000,
                    },
                ),
                by_key=(
                    {
                        "apiKeyId": "key-1",
                        "description": "Production Key",
                        "totalUsd": 4.25,
                        "totalDiem": 0,
                        "totalUnits": 21000,
                    },
                ),
            ),
            warnings=(),
        )


def _admin_client(test_settings, provider) -> TestClient:
    app = create_app(test_settings, registry=ProviderRegistry([provider]))
    return TestClient(app)


def test_venice_dashboard_shows_balance_limits_models_and_usage(test_settings):
    client = _admin_client(test_settings, DashboardVeniceProvider())

    response = client.get(
        "/admin/providers/venice",
        auth=("test-admin", "test-admin-password"),
    )

    assert response.status_code == 200
    assert "Venice" in response.text
    assert "25.5" in response.text
    assert "3" in response.text
    assert "paid" in response.text
    assert "2027-01-01" in response.text
    assert "Venice Uncensored" in response.text
    assert "0.5" in response.text
    assert "1.5" in response.text
    assert "100" in response.text
    assert "2,000,000" in response.text
    assert "4.25" in response.text
    assert "21,000" in response.text
    assert "Production Key" in response.text
    assert "key-1" in response.text


def test_venice_dashboard_explains_when_provider_is_not_connected(client):
    response = client.get(
        "/admin/providers/venice",
        auth=("test-admin", "test-admin-password"),
    )

    assert response.status_code == 200
    assert "Venice не подключён" in response.text
    assert "SHLUZ_VENICE_API_KEY" in response.text


def test_venice_dashboard_never_displays_provider_key(test_settings):
    secret = "venice-secret-that-must-not-leak"
    test_settings.venice_api_key = secret
    client = _admin_client(test_settings, DashboardVeniceProvider())

    response = client.get(
        "/admin/providers/venice",
        auth=("test-admin", "test-admin-password"),
    )

    assert response.status_code == 200
    assert secret not in response.text
    assert "Ключ настроен" in response.text


def test_admin_dashboard_links_to_provider_control(client):
    response = client.get(
        "/admin",
        auth=("test-admin", "test-admin-password"),
    )

    assert response.status_code == 200
    assert 'href="/admin/providers/venice"' in response.text
    assert "Пользователи и тарифы" in response.text
    assert 'href="/admin/logs"' in response.text
    assert 'href="/admin/test"' in response.text
    assert "Приём платежей пока не подключён" in response.text
