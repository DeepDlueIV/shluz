def test_missing_bearer_token_is_rejected(client):
    response = client.get("/v1/models")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "test-api-token" not in response.text


def test_wrong_bearer_token_is_rejected(client):
    response = client.get(
        "/v1/models",
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert response.status_code == 401
    assert "test-api-token" not in response.text


def test_valid_bearer_token_allows_request(client, authorized_headers):
    response = client.get("/v1/models", headers=authorized_headers)

    assert response.status_code == 200
