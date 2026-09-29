import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel


app = FastAPI(title="Shluz", version="0.1.0")

EXPECTED_TOKEN = os.getenv("SHLUZ_TEST_TOKEN", "dev-token")


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/completions")
def chat_completions(
    payload: ChatCompletionRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    if authorization != f"Bearer {EXPECTED_TOKEN}":
        raise HTTPException(status_code=401, detail="Invalid token")

    last_user_message = next(
        (message.content for message in reversed(payload.messages) if message.role == "user"),
        "",
    )

    return {
        "id": "chatcmpl-shluz-demo",
        "object": "chat.completion",
        "model": payload.model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": f"Shluz mock reply: {last_user_message}",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }
