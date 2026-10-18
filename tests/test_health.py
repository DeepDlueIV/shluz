from app.factory import create_app


def test_health_returns_service_metadata(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "shluz",
        "version": "0.1.0",
    }


def test_app_factory_uses_injected_settings(test_settings):
    app = create_app(test_settings)

    assert app.state.settings is test_settings


def test_build_revision_is_visible_and_default_contract_is_preserved():
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.factory import create_app

    settings = Settings(database_url="sqlite+pysqlite:///:memory:", build_sha="a" * 40)
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json()["build"] == "a" * 40
