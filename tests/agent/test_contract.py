"""Минимальный wire-контракт OpenCode; ни одного настоящего ключа или инструмента."""

import json

import pytest
from pydantic import ValidationError

from app.api.schemas import ChatCompletionRequestSchema

TOOL = {
    "type": "function",
    "function": {
        "name": "noclip_test_echo",
        "description": "Echo text",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
}
CALL = {
    "id": "call_demo_1",
    "type": "function",
    "function": {"name": "noclip_test_echo", "arguments": '{"text":"hello"}'},
}
BASE = {"model": "mock-chat", "messages": [{"role": "user", "content": "hello"}]}


def test_tool_history_and_definitions_survive_schema():
    data = {
        **BASE,
        "tools": [TOOL],
        "tool_choice": {"type": "function", "function": {"name": "noclip_test_echo"}},
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            {"role": "assistant", "content": None, "tool_calls": [CALL]},
            {"role": "tool", "tool_call_id": "call_demo_1", "content": "hello"},
        ],
    }
    parsed = ChatCompletionRequestSchema.model_validate(data)
    encoded = parsed.model_dump(exclude_none=True)
    assert encoded["messages"][1]["tool_calls"] == [CALL]
    assert encoded["messages"][2]["tool_call_id"] == "call_demo_1"
    assert encoded["tools"] == [TOOL]
    assert parsed.input_chars >= len(json.dumps(TOOL, ensure_ascii=False, separators=(",", ":")))


@pytest.mark.parametrize(
    "patch",
    [
        {"functions": [{"name": "old"}]},
        {"n": 2},
        {"response_format": {"type": "json_object"}},
        {"venice_parameters": {"enable_web_search": "on"}},
        {"tools": [{"type": "web_search"}]},
        {"tools": [TOOL], "tool_choice": {"type": "function", "function": {"name": "absent"}}},
        {
            "messages": [
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}
            ]
        },
        {"messages": [{"role": "user", "content": None}]},
        {"messages": [{"role": "assistant", "content": None}]},
        {"messages": [{"role": "tool", "content": "orphan", "tool_call_id": "call_missing"}]},
        {"messages": [{"role": "user", "content": "x", "tool_call_id": "wrong"}]},
        {"messages": [{"role": "assistant", "content": None, "tool_calls": [CALL]}]},
        {"max_tokens": 12, "max_completion_tokens": 13},
    ],
)
def test_unsupported_or_inconsistent_requests_are_explicit(patch):
    with pytest.raises(ValidationError):
        ChatCompletionRequestSchema.model_validate({**BASE, **patch})


def test_mock_repeated_round_has_new_call_id():
    from app.providers.base import ChatMessage, ChatRequest
    from app.providers.mock import MockProvider

    model = MockProvider()
    history = [ChatMessage("user", "first")]
    first = model.chat_completion(ChatRequest("mock-chat", history, options={"tools": [TOOL]}))
    history.extend(
        [
            ChatMessage("assistant", None, tool_calls=first.tool_calls),
            ChatMessage("tool", "hello", tool_call_id=first.tool_calls[0]["id"]),
            ChatMessage("user", "second"),
        ]
    )
    second = model.chat_completion(ChatRequest("mock-chat", history, options={"tools": [TOOL]}))
    assert second.tool_calls[0]["id"] != first.tool_calls[0]["id"]
