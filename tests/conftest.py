import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.factory import create_app


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        bootstrap_api_token=SecretStr("test-api-token"),
        admin_username="test-admin",
        admin_password=SecretStr("test-admin-password"),
        database_url=SecretStr("sqlite+pysqlite:///:memory:"),
    )


@pytest.fixture
def client(test_settings: Settings) -> TestClient:
    return TestClient(create_app(test_settings))


@pytest.fixture
def authorized_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-api-token"}
