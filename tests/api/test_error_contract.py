"""Контракт ошибок /v1/*: OpenAI-стиль, без эха содержимого запроса."""



def test_validation_error_on_api_is_openai_style_and_never_echoes_prompt(client):
    secret_prompt = "SECRET-PROMPT-CONTENT-42"
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test-api-token"},
        json={"model": "mock-chat", "messages": [{"role": "user", "content": 123}], "x": secret_prompt},
    )

    assert response.status_code == 422
    body = response.json()
    assert "error" in body, "API-клиент должен получать OpenAI-стиль, не FastAPI detail"
    assert body["error"]["code"] == "invalid_request"
    assert body["error"]["type"] == "invalid_request_error"
    assert secret_prompt not in response.text, "содержимое запроса не возвращается в ошибке"


def test_http_exception_on_api_is_openai_style(client):
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test-api-token"},
        json={"model": "missing-model", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "model_not_found"
    assert body["error"]["type"] in {"invalid_request_error", "model_not_found"}


def test_unauthorized_error_shape_is_openai_style(client):
    response = client.get(
        "/v1/models",
        headers={"Authorization": "Bearer shluz_unknown_token"},
    )

    assert response.status_code == 401
    body = response.json()
    assert "error" in body
    assert isinstance(body["error"], dict)
