"""Приёмка владельца не должна незаметно трогать production или вызывать платную модель."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_check():
    path = ROOT / "scripts" / "gateway_owner_check.py"
    assert path.is_file(), "Owner needs an executable gateway check"
    spec = importlib.util.spec_from_file_location("owner_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_owner_check_preparation_is_isolated_and_never_overwrites(tmp_path):
    check = load_check()
    (tmp_path / ".env").write_text("production-secret")
    path = check.prepare(tmp_path)
    assert path == tmp_path / ".env.owner"
    data = path.read_text()
    assert "SHLUZ_ACTIVE_PROVIDER=mock" in data
    assert "CHANGE_ME" not in data
    assert "production-secret" not in data
    assert (tmp_path / ".env").read_text() == "production-secret"
    with pytest.raises(FileExistsError):
        check.prepare(tmp_path)
    assert path.read_text() == data


def test_owner_check_requires_personal_auth_and_opt_in_to_mock():
    check = load_check()
    calls = []
    used = [0]

    def transport(base, method, path, token=None, payload=None):
        calls.append((method, path, token, payload))
        if path == "/health":
            return 200, {"status": "ok"}
        if path == "/admin" or not token or token == "invalid-owner-check":
            return 401, {}
        if path == "/v1/models":
            return 200, {"data": [{"id": "mock-chat"}, {"id": "paid-model"}]}
        if path == "/v1/me":
            return 200, {
                "account": {"id": "a"},
                "plan": {"name": "test"},
                "allowance": {"requests": {"used": used[0]}},
            }
        if path == "/v1/chat/completions":
            assert payload["model"] == "mock-chat"
            used[0] += 1
            return 200, {"choices": [{"message": {"content": "Shluz mock reply: owner-check"}}]}
        raise AssertionError(path)

    result = check.check_gateway("http://127.0.0.1:18000", "shluz_test", request=transport)
    assert all(item["ok"] for item in result)
    assert not any(method == "POST" for method, *_ in calls)
    result = check.check_gateway(
        "http://127.0.0.1:18000",
        "shluz_test",
        send_mock=True,
        request=transport,
    )
    assert all(item["ok"] for item in result)
    assert used == [1]


def test_owner_check_will_not_send_when_me_denies_access():
    check = load_check()
    calls = []

    def transport(base, method, path, token=None, payload=None):
        calls.append(method)
        if path == "/health":
            return 200, {"status": "ok"}
        if path == "/v1/models" and token == "shluz_test":
            return 200, {"data": [{"id": "mock-chat"}]}
        return (403 if path == "/v1/me" else 401), {}

    result = check.check_gateway(
        "http://localhost:18000", "shluz_test", send_mock=True, request=transport
    )
    assert any(not item["ok"] for item in result)
    assert "POST" not in calls


def test_owner_compose_uses_separate_local_port_database_and_settings():
    import yaml

    path = ROOT / "compose.owner.yaml"
    assert path.is_file()
    config = yaml.safe_load(path.read_text())
    service = config["services"]["shluz"]
    assert service["env_file"] == [".env.owner"]
    assert service["ports"] == ["127.0.0.1:18000:8000"]
    assert "owner_data:/app/data" in service["volumes"]
    assert set(config["services"]) == {"shluz"}


def test_owner_report_is_read_only_and_excludes_conversations(tmp_path):
    from telegram_bot.storage import Store

    assert (ROOT / "telegram_bot" / "report.py").is_file()
    missing = tmp_path / "missing.sqlite"
    result = subprocess.run(
        [sys.executable, "-m", "telegram_bot.report", "--database", str(missing)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0 and not missing.exists()
    path = tmp_path / "report.sqlite"
    store = Store(path)
    store.ensure_user(100, "Alice secret")
    turn = store.begin_turn(100, 1, "confidential prompt", "mock-chat")
    store.complete_turn(turn, "confidential answer")
    store.rate_answer(100, turn, -1)
    before = path.read_bytes()
    result = subprocess.run(
        [sys.executable, "-m", "telegram_bot.report", "--database", str(path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "confidential" not in result.stdout
    assert "Alice" not in result.stdout
    report = json.loads(result.stdout)
    assert report["financial_report"] is False
    assert report["by_model"][0]["negative"] == 1
    assert path.read_bytes() == before
