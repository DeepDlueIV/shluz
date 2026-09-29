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
