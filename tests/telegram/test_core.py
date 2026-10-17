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


def test_owner_retention_scrubs_automatic_titles(tmp_path):
    from telegram_bot.storage import Store

    store = Store(tmp_path / "retention.sqlite")
    store.ensure_user(10, "Alice")
    turn = store.begin_turn(10, 1, "private confidential title", "mock-chat")
    store.complete_turn(turn, "old answer")
    with store.connection() as db:
        db.execute("UPDATE turns SET created_at=?", (time.time() - 40 * 86400,))
    store.cleanup(retention_days=30)
    assert "private confidential" not in str(store.dialogs(10))


def test_owner_saved_turn_limit_is_enforced_when_saving(tmp_path):
    from telegram_bot.storage import Store

    store = Store(tmp_path / "bounded.sqlite", max_saved_turns=2)
    store.ensure_user(10, "Alice")
    for update_id in range(4):
        turn = store.begin_turn(10, update_id, f"question{update_id}", "mock-chat")
        store.complete_turn(turn, f"answer{update_id}")
    assert len(store.history(10)) == 4
    assert store.history(10)[0]["content"] == "question2"


def test_owner_retry_keeps_old_answer_until_success_then_replaces_context(tmp_path):
    from telegram_bot.storage import Store

    store = Store(tmp_path / "revisions.sqlite")
    store.ensure_user(100, "Alice")
    first = store.begin_turn(100, 1, "question", "mock-chat")
    store.complete_turn(first, "original")
    assert hasattr(store, "retry_source"), "Store needs a validated retry source"
    assert store.retry_source(100, first)["id"] == first
    retry = store.begin_turn(100, 2, "question", "mock-chat", replaces_turn_id=first)
    assert store.history(100)[-1]["content"] == "original"
    store.complete_turn(retry, "new version")
    assert store.history(100) == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "new version"},
    ]
    assert store.turn_for(100, first)["assistant_content"] == "original"
    assert store.turn_for(200, first) is None
    assert "original" in store.export_dialog(100)
    assert "new version" in store.export_dialog(100)
    assert store.retry_source(100, first) is None
    assert store.retry_source(100, retry)["id"] == retry
    unknown = store.begin_turn(100, 3, "uncertain", "mock-chat")
    store.fail_turn(unknown, "unknown")
    assert store.retry_source(100, retry) is None


def test_owner_failed_retry_preserves_context_and_foreign_retry_is_rejected(tmp_path):
    from telegram_bot.storage import Store

    store = Store(tmp_path / "retry-failure.sqlite")
    store.ensure_user(100, "Alice")
    store.ensure_user(200, "Bob")
    first = store.begin_turn(100, 1, "q", "mock-chat")
    store.complete_turn(first, "original")
    assert hasattr(store, "retry_source")
    with pytest.raises(ValueError):
        store.begin_turn(200, 2, "q", "mock-chat", replaces_turn_id=first)
    retry = store.begin_turn(100, 3, "q", "mock-chat", replaces_turn_id=first)
    store.fail_turn(retry, "failed")
    assert store.history(100)[-1]["content"] == "original"
    assert store.retry_source(100, first)


def test_owner_rating_rename_and_forgetting_are_scoped_and_dedup_survives(tmp_path):
    from telegram_bot.storage import Store

    store = Store(tmp_path / "privacy.sqlite")
    alice = store.ensure_user(100, "Alice")
    store.ensure_user(200, "Bob")
    store.bind_account(100, "account-100")
    store.claim_update(1)
    first = store.begin_turn(100, 1, "private Alice", "mock-chat")
    store.complete_turn(first, "answer Alice")
    bob = store.begin_turn(200, 2, "private Bob", "mock-chat")
    store.complete_turn(bob, "answer Bob")
    assert hasattr(store, "rate_answer")
    assert not store.rate_answer(200, first, 1)
    assert store.rate_answer(100, first, 1)
    assert store.rate_answer(100, first, -1)
    with pytest.raises(ValueError):
        store.rate_answer(100, first, 8)
    assert store.turn_for(100, first)["feedback"] == -1
    assert not store.rename_dialog(200, alice["active_dialog"], "hijacked")
    assert store.rename_dialog(100, alice["active_dialog"], "Работа")
    store.cleanup()
    assert store.dialogs(100)[0]["title"] == "Работа"
    with pytest.raises(ValueError):
        store.rename_dialog(100, alice["active_dialog"], " " * 3)
    metrics = store.metrics()
    assert "private" not in str(metrics) and "Alice" not in str(metrics)
    assert sum(item["negative"] for item in metrics) == 1
    store.forget_dialogs(100)
    assert store.history(100) == []
    assert len(store.dialogs(100)) == 1
    assert store.turn_for(100, first) is None
    assert store.profile(100)["account_id"] == "account-100"
    assert not store.claim_update(1)
    assert "private Bob" in store.export_dialog(200)


def test_owner_gateway_has_total_deadline_and_does_not_retry():
    from telegram_bot.gateway import GatewayClient, GatewayError

    async def scenario():
        calls = []

        async def slow(request):
            calls.append(request)
            await asyncio.sleep(0.1)
            return httpx.Response(200, json={"choices": [{"message": {"content": "late"}}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as http:
            gateway = GatewayClient("http://gateway", timeout=0.02, http=http)
            with pytest.raises(GatewayError) as exc:
                await gateway.chat(TOKEN, "mock-chat", [{"role": "user", "content": "q"}], "id")
            assert exc.value.code == "outcome_unknown"
        assert len(calls) == 1

    asyncio.run(scenario())


def test_owner_gateway_bounds_response_bytes_before_parsing():
    from telegram_bot.gateway import GatewayClient, GatewayError

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"x" * 100000)
            )
        ) as http:
            gateway = GatewayClient("http://gateway", http=http, max_response_chars=20)
            with pytest.raises(GatewayError) as exc:
                await gateway.chat(TOKEN, "mock-chat", [{"role": "user", "content": "q"}], "id")
            assert exc.value.code == "response_too_large"

    asyncio.run(scenario())


def test_owner_version_one_database_migrates_without_losing_history(tmp_path):
    import sqlite3

    from telegram_bot.storage import Store

    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE profiles(user_id INTEGER PRIMARY KEY, name TEXT NOT NULL, model TEXT,
                style TEXT NOT NULL DEFAULT 'balanced',active_dialog TEXT,account_id TEXT,
                created_at REAL NOT NULL,updated_at REAL NOT NULL);
            CREATE TABLE dialogs(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES profiles,
                title TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL);
            CREATE TABLE turns(id TEXT PRIMARY KEY,update_id INTEGER UNIQUE NOT NULL,
                dialog_id TEXT NOT NULL REFERENCES dialogs ON DELETE CASCADE,model TEXT NOT NULL,
                user_content TEXT NOT NULL,assistant_content TEXT,status TEXT NOT NULL,
                created_at REAL NOT NULL,updated_at REAL NOT NULL);
            CREATE TABLE processed_updates(update_id INTEGER PRIMARY KEY,created_at REAL NOT NULL);
            INSERT INTO profiles VALUES(100,'Alice','mock-chat','balanced','d','a',1,1);
            INSERT INTO dialogs VALUES('d',100,'Question',1,1);
            INSERT INTO turns VALUES(
                't',1,'d','mock-chat','Old question','Old answer','complete',1,1);
            INSERT INTO processed_updates VALUES(1,1);
            PRAGMA user_version=1;
        """)
    store = Store(path)
    assert store.history(100)[-1]["content"] == "Old answer"
    assert store.rate_answer(100, "t", 1)
    assert not store.claim_update(1)
    with store.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert Store(path).turn_for(100, "t")["feedback"] == 1


def test_owner_confirmations_expire_and_are_bound_to_user(monkeypatch):
    from telegram_bot.features import Confirmations

    clock = [100.0]
    monkeypatch.setattr("telegram_bot.features.time.monotonic", lambda: clock[0])
    confirmations = Confirmations()
    key = confirmations.issue(100, "forget")
    assert confirmations.take(200, "forget", key) is None
    assert confirmations.take(100, "retry", key) is None
    clock[0] += 121
    assert confirmations.take(100, "forget", key) is None


def test_owner_profile_warns_about_low_quota_and_shows_expiry():
    from telegram_bot.ui import profile_text

    remote = {
        "account": {"display_name": "Alice"},
        "plan": {"name": "Beta", "ends_at": "2026-12-10T12:00:00+00:00"},
        "allowance": {
            "scope": "account",
            "resets_at": "2026-11-01T00:00:00+00:00",
            "requests": {"limit": 10, "used": 9, "pending": 0, "remaining": 1},
            "credits": {"limit": None, "used": 0, "pending": 0, "remaining": None},
        },
    }
    text = profile_text(remote, {"user_id": 100, "model": "mock-chat"})
    assert "10.12.2026" in text
    assert "⚠️" in text
    assert "общие" in text


def test_owner_failed_requests_are_bounded_too(tmp_path):
    from telegram_bot.storage import Store

    store = Store(tmp_path / "errors.sqlite", max_saved_turns=2)
    store.ensure_user(100, "Alice")
    for update in range(5):
        turn = store.begin_turn(100, update, "private failed message", "mock-chat")
        store.fail_turn(turn, "unknown")
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 2
