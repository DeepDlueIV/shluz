"""Полный tool-цикл через API: user → assistant(tool_calls) → tool → финал."""

import json

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.db.models import Account, Plan, Subscription
from app.db.repositories import create_api_token
from app.factory import create_app
from app.providers.base import ChatRequest, ChatResult, ModelInfo, StreamChunk
from app.providers.registry import ProviderRegistry

TOOL_CALL = {
    "id": "call_7",
    "type": "function",
    "function": {"name": "get_weather", "arguments": '{"city": "СПб"}'},
}


class LoopProvider:
    """Первый вызов просит инструмент, второй отвечает текстом и запоминает историю."""

    name = "loop"

    def __init__(self):
        self.calls: list[ChatRequest] = []
        self.turn = 0

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="loop-chat", provider=self.name)]

    def chat_completion(self, request: ChatRequest) -> ChatResult:
        self.turn += 1
        self.calls.append(request)
        if self.turn == 1:
            return ChatResult(
                content="", prompt_tokens=5, completion_tokens=2, tool_calls=(TOOL_CALL,)
            )
        return ChatResult(
            content="В СПб сейчас дождь, 12 градусов",
            prompt_tokens=9,
            completion_tokens=6,
            request_id=f"up-{self.turn}",
        )

    async def chat_stream(self, request: ChatRequest):
        self.turn += 1
        self.calls.append(request)
        if self.turn == 1:
            yield StreamChunk(tool_calls=({"index": 0, **TOOL_CALL},), finish_reason="tool_calls")
        else:
            yield StreamChunk(content="В СПб сейчас ")
            yield StreamChunk(content="дождь, 12 градусов")
        yield StreamChunk(
            prompt_tokens=5, completion_tokens=4, request_id=f"up-{self.turn}", done=True
        )


def _client(tmp_path, provider) -> TestClient:
    settings = Settings(
        bootstrap_api_token=SecretStr("bootstrap"),
        admin_username="test-admin",
        admin_password=SecretStr("admin-password"),
        database_url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'tool-loop.db'}"),
    )
    app = create_app(settings, registry=ProviderRegistry([provider]))
    client = TestClient(app)
    database = client.app.state.database
    raw_token = "shluz_tool_loop_token"
    with database.session() as session:
        account = Account(display_name="Loop user")
        plan = Plan(code="loop-plan", name="Loop plan")
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
    headers = {"Authorization": f"Bearer {raw_token}"}
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Погода в городе",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    return client, headers, tools


def _second_request(first_response: dict, *, stream: bool) -> dict:
    assistant = first_response["choices"][0]["message"]
    return {
        "model": "loop-chat",
        "stream": stream,
        "messages": [
            {"role": "user", "content": "Какая погода в СПб?"},
            {
                "role": "assistant",
                "content": assistant["content"] or None,
                "tool_calls": assistant["tool_calls"],
            },
            {"role": "tool", "tool_call_id": TOOL_CALL["id"], "content": "дождь, 12C"},
        ],
    }


def test_full_tool_loop_returns_final_answer(tmp_path):
    provider = LoopProvider()
    client, headers, tools = _client(tmp_path, provider)

    first = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={
            "model": "loop-chat",
            "messages": [{"role": "user", "content": "Какая погода в СПб?"}],
            "tools": tools,
        },
    )
    assert first.status_code == 200
    assert first.json()["choices"][0]["finish_reason"] == "tool_calls"

    second = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={**_second_request(first.json(), stream=False), "tools": tools},
    )

    assert second.status_code == 200
    assert second.json()["choices"][0]["message"]["content"] == "В СПб сейчас дождь, 12 градусов"
    history = provider.calls[1]
    assert history.messages[1].tool_calls[0]["id"] == TOOL_CALL["id"]
    assert history.messages[1].tool_calls[0]["function"]["name"] == "get_weather"
    assert history.messages[2].role == "tool"
    assert history.messages[2].tool_call_id == TOOL_CALL["id"]


def _collect_tool_calls_from_stream(body: str) -> list[dict]:
    """Собрать assistant.tool_calls из SSE-дельт, как это делает клиент."""
    merged: dict[int, dict] = {}
    for line in body.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        frame = json.loads(line[len("data: ") :])
        for choice in frame.get("choices", []):
            for fragment in (choice.get("delta") or {}).get("tool_calls") or []:
                index = fragment.get("index", 0)
                item = merged.setdefault(
                    index,
                    {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
                )
                if fragment.get("id"):
                    item["id"] = fragment["id"]
                if fragment.get("type"):
                    item["type"] = fragment["type"]
                function = fragment.get("function") or {}
                if function.get("name"):
                    item["function"]["name"] = function["name"]
                if function.get("arguments"):
                    item["function"]["arguments"] += function["arguments"]
    return [merged[index] for index in sorted(merged)]


def test_full_tool_loop_streams_final_answer(tmp_path):
    provider = LoopProvider()
    client, headers, tools = _client(tmp_path, provider)

    first = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={
            "model": "loop-chat",
            "stream": True,
            "messages": [{"role": "user", "content": "Какая погода в СПб?"}],
            "tools": tools,
        },
    )
    assert first.status_code == 200
    tool_calls = _collect_tool_calls_from_stream(first.text)
    assert tool_calls[0]["function"]["name"] == "get_weather"

    second = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={
            "model": "loop-chat",
            "stream": True,
            "messages": [
                {"role": "user", "content": "Какая погода в СПб?"},
                {"role": "assistant", "content": None, "tool_calls": tool_calls},
                {"role": "tool", "tool_call_id": TOOL_CALL["id"], "content": "дождь, 12C"},
            ],
            "tools": tools,
        },
    )

    assert second.status_code == 200
    frames = [
        json.loads(line[len("data: ") :])
        for line in second.text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    text = "".join(
        frame["choices"][0]["delta"].get("content", "") for frame in frames if frame.get("choices")
    )
    assert text == "В СПб сейчас дождь, 12 градусов"
    assert provider.calls[1].messages[1].tool_calls[0]["id"] == TOOL_CALL["id"]
