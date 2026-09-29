def test_models_returns_openai_compatible_list(client, authorized_headers):
    response = client.get("/v1/models", headers=authorized_headers)

    assert response.status_code == 200
    assert response.json() == {
        "object": "list",
        "data": [
            {
                "id": "mock-chat",
                "object": "model",
                "created": 0,
                "owned_by": "mock",
            }
        ],
    }
