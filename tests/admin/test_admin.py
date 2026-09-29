def test_admin_requires_http_basic_credentials(client):
    response = client.get("/admin")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


def test_admin_rejects_wrong_credentials(client):
    response = client.get("/admin", auth=("test-admin", "wrong-password"))

    assert response.status_code == 401


def test_admin_dashboard_is_human_readable_and_hides_secrets(client, test_settings):
    response = client.get(
        "/admin",
        auth=("test-admin", "test-admin-password"),
    )

    assert response.status_code == 200
    assert "Shluz" in response.text
    assert test_settings.environment in response.text
    assert test_settings.service_version in response.text
    assert "mock" in response.text
    assert "1" in response.text
    assert 'href="/docs"' in response.text
    assert 'href="/health"' in response.text
    assert test_settings.bootstrap_api_token.get_secret_value() not in response.text
    assert test_settings.admin_password.get_secret_value() not in response.text
