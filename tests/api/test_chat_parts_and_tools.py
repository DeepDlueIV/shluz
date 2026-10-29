"""OpenAI-формат частей контента и проброс tools через шлюз (Cherry Studio)."""

import json

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.db.models import Account, Plan, Subscription
from app.db.repositories import create_api_token
from app.factory import create_app
from app.providers.base import ChatRequest, ChatResult, ModelInfo, StreamChunk
from app.providers.registry import ProviderRegistry


def _client(tmp_path, *providers) -> TestClient:
    settings = Settings(
        bootstrap_api_token=SecretStr("bootstrap"),
        admin_username="test-admin",
        admin_password=SecretStr("admin-password"),
        database_url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'chat-parts.db'}"),
    )
    if providers:
        app = create_app(settings, registry=ProviderRegistry(list(providers)))
    else:
        app = create_app(settings)
    return TestClient(app)


def test_text_content_parts_are_accepted_and_normalized(tmp_path):
    client = _client(tmp_path)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer bootstrap"},
        json={
            "model": "mock-chat",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Привет"},
                        {"type": "text", "text": "мир"},
                    ],
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "Shluz mock reply: Привет\nмир"


def test_stream_accepts_content_parts(tmp_path):
    client = _client(tmp_path)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer bootstrap"},
        json={
            "model": "mock-chat",
            "stream": True,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "Привет"}]}],
        },
    )

    assert response.status_code == 200
    frames = [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    contents = "".join(
        frame["choices"][0]["delta"].get("content", "") for frame in frames if frame.get("choices")
    )
    assert "Shluz mock reply: Привет" in contents


class ToolsProvider:
    name = "tools"

    def __init__(self):
        self.captured: ChatRequest | None = None

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="tools-chat", provider=self.name)]

    def chat_completion(self, request: ChatRequest) -> ChatResult:
        self.captured = request
        return ChatResult(
            content="",
            prompt_tokens=3,
            completion_tokens=2,
            tool_calls=(
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city": "СПб"}'},
                },
            ),
        )


def test_tools_reach_provider_and_tool_calls_return(tmp_path):
    provider = ToolsProvider()
    client = _client(tmp_path, provider)
    database = client.app.state.database
    raw_token = "shluz_tools_personal_token"
    with database.session() as session:
        account = Account(display_name="Tools user")
        plan = Plan(code="tools-plan", name="Tools plan")
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

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "model": "tools-chat",
            "messages": [{"role": "user", "content": "Погода?"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Погода в городе",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            "tool_choice": "auto",
        },
    )

    assert response.status_code == 200
    assert provider.captured is not None
    assert provider.captured.tools[0]["function"]["name"] == "get_weather"
    assert provider.captured.tool_choice == "auto"
    body = response.json()
    assert body["choices"][0]["finish_reason"] == "tool_calls"
    assert body["choices"][0]["message"]["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city": "СПб"}'},
        }
    ]


class StreamingToolsProvider(ToolsProvider):
    async def chat_stream(self, request: ChatRequest):
        self.captured = request
        yield StreamChunk(
            tool_calls=(
                {
                    "index": 0,
                    "id": "call_9",
                    "type": "function",
                    "function": {"name": "calc", "arguments": '{"a":'},
                },
            )
        )
        yield StreamChunk(
            tool_calls=({"index": 0, "function": {"arguments": "1}"}},),
            finish_reason="tool_calls",
        )
        yield StreamChunk(prompt_tokens=2, completion_tokens=1, request_id="upstream-1", done=True)


def test_stream_aggregates_and_emits_tool_call_fragments(tmp_path):
    provider = StreamingToolsProvider()
    client = _client(tmp_path, provider)
    database = client.app.state.database
    raw_token = "shluz_tools_stream_token"
    with database.session() as session:
        account = Account(display_name="Tools stream user")
        plan = Plan(code="tools-stream", name="Tools stream plan")
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

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "model": "tools-chat",
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [{"role": "user", "content": "Считай"}],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "calc", "parameters": {"type": "object"}},
                }
            ],
        },
    )

    assert response.status_code == 200
    assert provider.captured is not None
    assert provider.captured.tools[0]["function"]["name"] == "calc"
    frames = [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    deltas = [frame["choices"][0]["delta"] for frame in frames if frame.get("choices")]
    tool_frames = [delta for delta in deltas if "tool_calls" in delta]
    assert tool_frames and tool_frames[0]["tool_calls"][0]["function"]["name"] == "calc"
    finishes = [frame["choices"][0]["finish_reason"] for frame in frames if frame.get("choices")]
    assert "tool_calls" in finishes
    usage = [frame for frame in frames if frame.get("usage")]
    assert usage and usage[-1]["usage"]["prompt_tokens"] == 2
