"""Сквозные проверки HTTP → Venice adapter → HTTP с локальным echo на клиенте."""

import asyncio
import json
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from app.db.models import Account, ModelPolicy, Plan, Subscription, UsageEvent
from app.db.repositories import create_api_token
from app.factory import create_app
from app.providers.registry import ProviderRegistry
from app.providers.venice import VeniceProvider
from tests.agent.test_contract import CALL, TOOL

MODEL = "zai-org-glm-5-2"


class Bytes(httpx.AsyncByteStream):
    def __init__(self, wire):
        self.wire = wire.encode()
        self.closed = False

    async def __aiter__(self):
        for i in range(0, len(self.wire), 7):
            yield self.wire[i : i + 7]

    async def aclose(self):
        self.closed = True


def sse(items, done=True):
    return "".join("data: " + json.dumps(i, ensure_ascii=False) + "\n\n" for i in items) + (
        "data: [DONE]\n\n" if done else ""
    )


def tool_frames():
    return [
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": CALL["id"],
                                "type": "function",
                                "function": {
                                    "name": CALL["function"]["name"],
                                    "arguments": '{"text":',
                                },
                            }
                        ],
                    },
                }
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"hello"}'}}]},
                }
            ]
        },
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        {
            "choices": [],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            "cost": {"usd": "0.002", "diem": "0"},
        },
    ]


@pytest.fixture
def gateway(test_settings):
    posts, bodies = [], []
    queue = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": MODEL,
                            "type": "text",
                            "model_spec": {"capabilities": {"supportsFunctionCalling": True}},
                        }
                    ]
                },
            )
        payload = json.loads(request.content)
        posts.append(payload)
        assert request.headers["authorization"] == "Bearer synthetic-agent-key"
        reply = queue.pop(0)
        if isinstance(reply, int):
            return httpx.Response(reply, json={"error": {"message": "PRIVATE"}})
        if payload["stream"]:
            body = Bytes(reply)
            bodies.append(body)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)
        return httpx.Response(200, json={**reply, "id": "provider-" + str(len(posts))})

    settings = test_settings.model_copy(update={"venice_api_key": SecretStr("synthetic-agent-key")})
    sync = httpx.Client(base_url=settings.venice_base_url, transport=httpx.MockTransport(handler))
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = VeniceProvider(settings, client=sync, async_client=upstream)
    app = create_app(settings, registry=ProviderRegistry([provider]))
    with app.state.database.session() as db:
        account = Account(display_name="Agent contract")
        plan = Plan(
            code="agent", name="Agent", monthly_request_limit=10, markup_percent=Decimal("20")
        )
        db.add_all([account, plan])
        db.flush()
        db.add(Subscription(account_id=account.id, plan_id=plan.id, source="harness"))
        db.add(ModelPolicy(id="channel:harness", models_json=json.dumps([MODEL])))
        create_api_token(
            db,
            account_id=account.id,
            source="harness",
            raw_token="shluz_" + "a" * 43,
            name="Synthetic personal token",
        )
    with TestClient(app) as c:
        yield c, {"Authorization": "Bearer shluz_" + "a" * 43}, queue, posts, bodies, account.id
    sync.close()
    asyncio.run(upstream.aclose())


def request(stream=False):
    return {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Echo hello"}],
        "tools": [TOOL],
        "tool_choice": "auto",
        "max_tokens": 64,
        "stream": stream,
        "stream_options": {"include_usage": True},
    }


def json_reply(tool=True, known=True):
    message = (
        {"role": "assistant", "content": None, "tool_calls": [CALL]}
        if tool
        else {"role": "assistant", "content": "hello from tool"}
    )
    result = {
        "id": "provider-id",
        "choices": [
            {"index": 0, "message": message, "finish_reason": "tool_calls" if tool else "stop"}
        ],
    }
    if known:
        result.update(
            usage={"prompt_tokens": 12, "completion_tokens": 8}, cost={"usd": "0.002", "diem": 0}
        )
    return result


@pytest.mark.parametrize("stream", [False, True])
def test_full_agent_cycle_preserves_tools_and_accounts_two_requests(gateway, stream):
    c, headers, queue, posts, bodies, aid = gateway
    queue.append(sse(tool_frames()) if stream else json_reply())
    payload = request(stream)
    result = c.post("/v1/chat/completions", headers=headers, json=payload)
    assert result.status_code == 200, result.text
    if stream:
        frames = [
            json.loads(s[6:])
            for s in result.text.splitlines()
            if s.startswith("data: ") and s != "data: [DONE]"
        ]
        fragments = [
            t for f in frames for ch in f["choices"] for t in ch["delta"].get("tool_calls", [])
        ]
        assert fragments[0]["id"] == CALL["id"]
        assert all(t["index"] == 0 for t in fragments)
        assert (
            "".join(t.get("function", {}).get("arguments", "") for t in fragments)
            == CALL["function"]["arguments"]
        )
        assert any(ch["finish_reason"] == "tool_calls" for f in frames for ch in f["choices"])
        assert result.text.endswith("data: [DONE]\n\n")
        message = {"role": "assistant", "content": None, "tool_calls": [CALL]}
    else:
        assert result.json()["choices"][0]["finish_reason"] == "tool_calls"
        message = result.json()["choices"][0]["message"]
        assert message["content"] is None and message["tool_calls"] == [CALL]
    # Единственный инструмент исполняется здесь, в контролируемом клиенте, не в шлюзе.
    output = json.loads(message["tool_calls"][0]["function"]["arguments"])["text"]
    payload["messages"] += [
        message,
        {"role": "tool", "tool_call_id": CALL["id"], "content": output},
    ]
    queue.append(
        sse(
            [
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "hello from tool"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 8},
                    "cost": {"usd": "0.002", "diem": 0},
                }
            ]
        )
        if stream
        else json_reply(False)
    )
    final = c.post("/v1/chat/completions", headers=headers, json=payload)
    assert final.status_code == 200 and "hello from tool" in final.text
    assert len(posts) == 2
    assert posts[0]["tools"] == [TOOL] and posts[0]["tool_choice"] == "auto"
    assert posts[1]["messages"][-1] == payload["messages"][-1]
    with c.app.state.database.session() as db:
        rows = list(db.scalars(select(UsageEvent).where(UsageEvent.account_id == aid)))
        assert len(rows) == 2 and all(r.status == "success" for r in rows)
        assert all(r.source == "harness" and r.prompt_tokens == 12 for r in rows)
        assert sum(r.billed_usd for r in rows) == Decimal("0.00480000")
    assert all(b.closed for b in bodies)


def test_unknown_json_usage_is_null_not_invented_zero(gateway):
    c, h, queue, posts, _, _ = gateway
    queue.append(json_reply(known=False))
    r = c.post("/v1/chat/completions", headers=h, json=request())
    assert r.status_code == 200
    assert r.json()["usage"] is None
    usage = c.get("/v1/me/usage", headers=h).json()["data"][0]
    assert usage["prompt_tokens"] is None and usage["charged_usd"] is None


def test_failure_during_tool_arguments_no_done_no_replay(gateway):
    c, h, queue, posts, bodies, _ = gateway
    queue.append(sse(tool_frames()[:2], done=False))
    r = c.post("/v1/chat/completions", headers=h, json=request(True))
    assert r.status_code == 200 and "tool_calls" in r.text
    assert "stream_interrupted" in r.text and "[DONE]" not in r.text
    assert len(posts) == 1 and bodies[0].closed
    with c.app.state.database.session() as db:
        rows = list(db.scalars(select(UsageEvent)))
        assert len(rows) == 1 and rows[0].status == "interrupted"
        assert rows[0].cost_known is False


@pytest.mark.parametrize("status,expected", [(401, 502), (402, 503), (429, 429), (500, 502)])
def test_pre_stream_errors_no_retries_or_secrets(gateway, status, expected):
    c, h, queue, posts, _, _ = gateway
    queue.append(status)
    r = c.post("/v1/chat/completions", headers=h, json=request(True))
    assert r.status_code == expected and "PRIVATE" not in r.text
    assert len(posts) == 1


def test_unsupported_fields_are_not_silently_dropped_or_billed(gateway):
    c, h, _, posts, _, _ = gateway
    r = c.post("/v1/chat/completions", headers=h, json={**request(), "functions": [TOOL]})
    assert r.status_code == 422
    assert not posts
    with c.app.state.database.session() as db:
        assert list(db.scalars(select(UsageEvent))) == []


def test_parallel_tool_fragments_preserve_indices_ids_and_unicode(gateway):
    c, h, queue, posts, _, _ = gateway
    frames = [
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": i,
                                "id": "call_" + str(i),
                                "type": "function",
                                "function": {"name": "noclip_test_echo", "arguments": '{"text":"'},
                            }
                            for i in (0, 1)
                        ]
                    },
                }
            ]
        }
    ]
    for i, text in [(1, 'Б"}'), (0, 'A"}')]:
        frames.append(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"tool_calls": [{"index": i, "function": {"arguments": text}}]},
                    }
                ]
            }
        )
    frames.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
    queue.append(sse(frames))
    r = c.post(
        "/v1/chat/completions", headers=h, json={**request(True), "parallel_tool_calls": True}
    )
    assert r.status_code == 200 and "[DONE]" in r.text
    parsed = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: {")]
    deltas = [t for f in parsed for ch in f["choices"] for t in ch["delta"].get("tool_calls", [])]
    assert [(t["index"], t["id"]) for t in deltas if "id" in t] == [(0, "call_0"), (1, "call_1")]
    for i, expected in [(0, '{"text":"A"}'), (1, '{"text":"Б"}')]:
        assert (
            "".join(t["function"].get("arguments", "") for t in deltas if t["index"] == i)
            == expected
        )
    assert posts[0]["parallel_tool_calls"] is True


@pytest.mark.parametrize(
    "bad",
    [
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "oops"}}]},
                }
            ]
        },
        {"choices": [{"index": 0, "delta": {"audio": "PRIVATE"}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "function_call"}]},
    ],
)
def test_invalid_first_stream_frame_is_explicit_http_error(gateway, bad):
    c, h, queue, posts, _, _ = gateway
    queue.append(sse([bad]))
    r = c.post("/v1/chat/completions", headers=h, json=request(True))
    assert r.status_code == 502 and "PRIVATE" not in r.text
    assert len(posts) == 1


def test_length_finish_preserves_partial_arguments_without_claiming_tool_completion(gateway):
    c, h, queue, _, _, _ = gateway
    queue.append(
        sse([tool_frames()[0], {"choices": [{"index": 0, "delta": {}, "finish_reason": "length"}]}])
    )
    r = c.post("/v1/chat/completions", headers=h, json=request(True))
    assert r.status_code == 200 and '"finish_reason":"length"' in r.text
    assert '"finish_reason":"tool_calls"' not in r.text
    assert "[DONE]" in r.text


def test_tool_stream_without_finish_is_not_success(gateway):
    c, h, queue, _, _, _ = gateway
    queue.append(sse(tool_frames()[:2]))
    r = c.post("/v1/chat/completions", headers=h, json=request(True))
    assert "stream_interrupted" in r.text and "[DONE]" not in r.text


def test_invalid_json_after_upstream_call_keeps_usage_unknown(gateway):
    c, h, queue, posts, _, _ = gateway
    queue.append({"choices": [{"message": {"content": "PRIVATE", "audio": {}}}]})
    r = c.post("/v1/chat/completions", headers=h, json=request())
    assert r.status_code == 502 and "PRIVATE" not in r.text and len(posts) == 1
    with c.app.state.database.session() as db:
        event = db.scalar(select(UsageEvent))
        assert event.status == "interrupted" and not event.cost_known


def test_tool_schemas_and_results_count_toward_input_limit(gateway):
    c, h, _, posts, _, _ = gateway
    big_tool = {"type": "function", "function": {**TOOL["function"], "description": "x" * 200001}}
    r = c.post("/v1/chat/completions", headers=h, json={**request(), "tools": [big_tool]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "input_too_large" and not posts


def test_demo_tools_rejected_before_upstream_and_without_free_extra_grant(gateway):
    from app.product.service import grant_demo, policy

    c, h, _, posts, _, aid = gateway
    with c.app.state.database.session() as db:
        for sub in db.scalars(select(Subscription).where(Subscription.account_id == aid)):
            sub.status = "cancelled"
        cfg = policy(db)
        cfg.demo_models_json = json.dumps([MODEL])
        db.add(cfg)
        grant_demo(db, aid)
    r = c.post("/v1/chat/completions", headers=h, json=request())
    assert r.status_code == 403 and r.json()["error"]["code"] == "demo_tools_not_allowed"
    assert not posts


def test_personal_channel_and_model_policy_not_spoofable(gateway):
    c, h, _, posts, _, _ = gateway
    with c.app.state.database.session() as db:
        db.get(ModelPolicy, "channel:harness").models_json = "[]"
    r = c.post("/v1/chat/completions", headers=h, json={**request(), "source": "telegram"})
    assert r.status_code == 403 and not posts


def test_negative_model_capability_rejected_before_reservation(gateway):
    from dataclasses import replace

    c, h, _, posts, _, _ = gateway
    registry = c.app.state.provider_registry
    registry._models = [replace(m, tool_calling=False) for m in registry.list_models()]
    r = c.post("/v1/chat/completions", headers=h, json=request())
    assert r.status_code == 400 and r.json()["error"]["code"] == "tools_unsupported"
    assert not posts
    with c.app.state.database.session() as db:
        assert list(db.scalars(select(UsageEvent))) == []


def test_validation_does_not_reflect_private_tool_data(gateway):
    c, h, _, posts, _, _ = gateway
    r = c.post(
        "/v1/chat/completions",
        headers=h,
        json={**request(), "messages": [{"role": "tool", "content": "PRIVATE-FILE"}]},
    )
    assert r.status_code == 422 and "PRIVATE-FILE" not in r.text
    assert r.json()["error"]["type"] == "invalid_request_error"
    assert not posts


def test_reasoning_effort_and_replay_text_parts_are_not_dropped(gateway):
    c, h, queue, posts, _, _ = gateway
    reply = json_reply()
    reply["choices"][0]["message"]["reasoning_content"] = "synthetic reasoning"
    queue.append(reply)
    r = c.post(
        "/v1/chat/completions",
        headers=h,
        json={
            **request(),
            "reasoning_effort": "low",
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "one"}, {"type": "text", "text": "two"}],
                }
            ],
        },
    )
    assert r.status_code == 200
    assert posts[0]["reasoning_effort"] == "low"
    assert posts[0]["messages"][0]["content"][1]["text"] == "two"
    assert r.json()["choices"][0]["message"]["reasoning_content"] == "synthetic reasoning"


@pytest.mark.parametrize("stream", [False, True])
def test_agent_requires_explicit_finish_even_for_final_text(gateway, stream):
    c, h, queue, _, _, _ = gateway
    if stream:
        queue.append(sse([{"choices": [{"delta": {"content": "answer"}}]}]))
    else:
        reply = json_reply(False)
        del reply["choices"][0]["finish_reason"]
        queue.append(reply)
    r = c.post("/v1/chat/completions", headers=h, json=request(stream))
    if stream:
        assert "stream_interrupted" in r.text and "[DONE]" not in r.text
    else:
        assert r.status_code == 502


@pytest.mark.parametrize("known", [False, True])
def test_agent_payg_keeps_unknown_hold_and_settles_known_charge(gateway, known):
    from app.billing.models import WalletModelBound
    from app.billing.service import credit, snapshot
    from app.product.service import locked_session

    c, h, queue, posts, _, aid = gateway
    with locked_session(c.app.state.database) as db:
        db.scalar(select(Plan)).billing_mode = "payg"
        db.add(
            WalletModelBound(
                id="venice:" + MODEL,
                max_cost_usd=Decimal("0.01"),
                max_input_chars=4096,
                max_output_tokens=128,
                reviewed=True,
            )
        )
        credit(db, aid, Decimal("1"), "agent-credit", "test", "Isolated test")
    queue.append(json_reply(known=known))
    r = c.post("/v1/chat/completions", headers=h, json=request())
    assert r.status_code == 200 and len(posts) == 1
    with c.app.state.database.session() as db:
        assert len(list(db.scalars(select(UsageEvent)))) == 1
        wallet = snapshot(db, aid)
        if known:
            assert Decimal(wallet["balance_usd"]) == Decimal("0.9976")
            assert Decimal(wallet["reserved_usd"]) == 0
        else:
            assert Decimal(wallet["balance_usd"]) == 1
            assert Decimal(wallet["reserved_usd"]) == Decimal("0.012")
            assert r.json()["usage"] is None
