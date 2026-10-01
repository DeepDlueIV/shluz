import asyncio
import json
import time

import httpx
import pytest

TOKEN = "shluz_" + "a" * 43
OTHER_TOKEN = "shluz_" + "b" * 43


def test_access_file_is_closed_by_default_and_reloads(tmp_path):
    from telegram_bot.config import AccessRegistry

    path = tmp_path / "access.json"
    path.write_text(json.dumps({"100": TOKEN}))
    registry = AccessRegistry(path)
    assert registry.token_for(100).get_secret_value() == TOKEN
    assert registry.token_for(101) is None
    assert TOKEN not in repr(registry)
    path.write_text("{}")
    assert registry.token_for(100) is None
    path.write_text("invalid json")
    assert registry.token_for(100) is None


@pytest.mark.parametrize(
    "mapping",
    [
        {"100": "dev-token"},
        {"-1": TOKEN},
        {"name": TOKEN},
        {"100": TOKEN, "101": TOKEN},
        {"100": 42},
        [],
        {"01": TOKEN},
    ],
)
def test_access_file_rejects_dangerous_configuration(tmp_path, mapping):
    from telegram_bot.config import AccessRegistry, ConfigurationError

    path = tmp_path / "access.json"
    path.write_text(json.dumps(mapping))
    with pytest.raises(ConfigurationError) as exc:
        AccessRegistry(path)
    assert TOKEN not in str(exc.value)


def test_settings_validate_gateway_url_and_do_not_print_secrets():
    from pydantic import ValidationError

    from telegram_bot.config import BotSettings

    settings = BotSettings(bot_token="123:" + "x" * 35, _env_file=None)
    assert settings.gateway_url == "http://shluz:8000"
    assert "x" * 35 not in repr(settings)
    for value in ("file:///etc/passwd", "https://user:secret@example.org", "https://a/#fragment"):
        with pytest.raises(ValidationError):
            BotSettings(bot_token="123:" + "x" * 35, gateway_url=value, _env_file=None)


def test_chunks_preserve_every_character_and_respect_utf16():
    from telegram_bot.presentation import split_text

    for text in ("x" * 11000, "🚀" * 4100, ("абзац\n\n<test>👨‍💻\n" * 450)):
        chunks = split_text(text)
        assert "".join(chunks) == text
        assert all(len(chunk.encode("utf-16-le")) // 2 <= 3800 for chunk in chunks)
        assert all(chunk for chunk in chunks)
    assert split_text("") == []


def test_model_html_is_escaped_and_code_fences_are_readable():
    from telegram_bot.presentation import render_answer

    text = '<a href="https://bad.example">fake</a>\n```python\nprint("<>")\n```'
    html = render_answer(text)
    assert "<a href=" not in html
    assert "&lt;a" in html
    assert "<pre>" in html
    assert "&lt;&gt;" in html


def test_context_retains_latest_turn_and_drops_oldest_pairs():
    from telegram_bot.presentation import prepare_messages

    history = [
        {"role": role, "content": text}
        for role, text in (
            ("user", "a" * 400),
            ("assistant", "b" * 400),
            ("user", "recent"),
            ("assistant", "reply"),
        )
    ]
    messages, trimmed = prepare_messages(history, "now", style="balanced", budget=500)
    assert trimmed
    assert messages[-3:] == history[-2:] + [{"role": "user", "content": "now"}]
    assert sum(len(m["content"]) for m in messages) <= 500
    with pytest.raises(ValueError):
        prepare_messages([], "x" * 501, style="balanced", budget=500)


def test_store_is_persistent_isolated_and_deduplicates(tmp_path):
    from telegram_bot.storage import Store

    path = tmp_path / "bot.sqlite"
    store = Store(path)
    alice = store.ensure_user(100, "Alice")
    bob = store.ensure_user(200, "Bob")
    assert alice["active_dialog"] != bob["active_dialog"]
    assert store.claim_update(11)
    assert not store.claim_update(11)
    turn = store.begin_turn(100, 11, "question", "mock-chat")
    assert turn is not None
    assert store.begin_turn(100, 11, "question", "mock-chat") is None
    store.complete_turn(turn, "answer")
    assert store.history(100) == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ]
    assert store.history(200) == []
    assert not store.activate_dialog(200, alice["active_dialog"])
    assert not store.clear_dialog(200, alice["active_dialog"])
    assert "question" not in store.export_dialog(200)
    reopened = Store(path)
    assert reopened.history(100) == store.history(100)
    assert reopened.claim_update(11) is False
    assert "answer" in reopened.export_dialog(100)
    assert TOKEN not in path.read_bytes().decode("utf-8", errors="ignore")


def test_new_dialog_clear_and_failed_turns(tmp_path):
    from telegram_bot.storage import Store

    store = Store(tmp_path / "bot.sqlite")
    profile = store.ensure_user(100, "Alice")
    old = profile["active_dialog"]
    failed = store.begin_turn(100, 1, "failed prompt", "mock-chat")
    store.fail_turn(failed, "failed")
    assert store.history(100) == []
    done = store.begin_turn(100, 2, "hello", "mock-chat")
    store.complete_turn(done, "world")
    new = store.new_dialog(100)
    assert new != old
    assert store.history(100) == []
    assert len(store.dialogs(100)) == 2
    assert store.activate_dialog(100, old)
    assert len(store.history(100)) == 2
    assert store.clear_dialog(100, old)
    assert store.history(100) == []


def test_crashed_request_is_not_replayed_and_pending_is_recovered(tmp_path):
    from telegram_bot.storage import Store

    path = tmp_path / "bot.sqlite"
    store = Store(path)
    store.ensure_user(1, "A")
    turn = store.begin_turn(1, 5, "paid prompt", "mock-chat")
    restarted = Store(path)
    assert restarted.recover_pending() == 1
    assert restarted.begin_turn(1, 5, "paid prompt", "mock-chat") is None
    assert restarted.history(1) == []
    assert restarted.turn_status(turn) == "unknown"


def test_store_retention_and_bounded_dialog_count(tmp_path):
    from telegram_bot.storage import DialogLimit, Store

    store = Store(tmp_path / "bot.sqlite", max_dialogs=2)
    old_dialog = store.ensure_user(1, "A")["active_dialog"]
    turn = store.begin_turn(1, 1, "old secret", "mock-chat")
    store.complete_turn(turn, "old answer")
    store.new_dialog(1)
    with pytest.raises(DialogLimit):
        store.new_dialog(1)
    with store.connection() as db:
        db.execute("UPDATE turns SET created_at=?", (time.time() - 40 * 86400,))
    assert "old secret" in store.export_dialog(1, old_dialog)
    store.cleanup(retention_days=30)
    assert "old secret" not in store.export_dialog(1, old_dialog)
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 0


def test_gateway_never_retries_ambiguous_post_and_keeps_tokens_separate():
    from telegram_bot.gateway import GatewayClient, GatewayError

    requests = []

    async def scenario():
        def respond(request):
            requests.append(request)
            if request.url.path.endswith("completions"):
                raise httpx.ReadTimeout("test", request=request)
            return httpx.Response(200, json={"data": [{"id": "mock-chat"}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            gateway = GatewayClient("http://shluz:8000", http=http)
            assert await gateway.models(TOKEN) == ["mock-chat"]
            assert await gateway.models(OTHER_TOKEN) == ["mock-chat"]
            with pytest.raises(GatewayError) as exc:
                await gateway.chat(TOKEN, "mock-chat", [{"role": "user", "content": "q"}], "id")
            assert exc.value.code == "outcome_unknown"
        assert len(requests) == 3
        assert requests[0].headers["authorization"] == f"Bearer {TOKEN}"
        assert requests[1].headers["authorization"] == f"Bearer {OTHER_TOKEN}"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,body,code",
    [
        (401, {"detail": "secret value"}, "unauthorized"),
        (402, {"error": {"code": "credit_limit_exceeded"}}, "credit_limit_exceeded"),
        (429, {"error": {"code": "provider_rate_limit"}}, "provider_rate_limit"),
        (503, {"error": {"code": "provider_balance_exhausted"}}, "provider_unavailable"),
        (200, {"choices": []}, "invalid_response"),
        (502, {"error": {"code": ["secret-upstream-value"]}}, "provider_unavailable"),
        (429, {"error": {"code": {"unexpected": "secret"}}}, "rate_limit"),
    ],
)
def test_gateway_errors_are_structured_and_not_leaky(status, body, code):
    from telegram_bot.gateway import GatewayClient, GatewayError

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body))
        ) as http:
            gateway = GatewayClient("http://shluz:8000", http=http)
            with pytest.raises(GatewayError) as exc:
                await gateway.chat(TOKEN, "mock-chat", [{"role": "user", "content": "q"}], "id")
            assert exc.value.code == code
            assert "secret value" not in str(exc.value)

    asyncio.run(scenario())
