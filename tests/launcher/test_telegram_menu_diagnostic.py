"""Read-only Telegram menu diagnostic: scopes, token redaction and no mutations."""

from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest


def load():
    path = Path(__file__).resolve().parents[2] / "diagnostics" / "telegram_menu.py"
    spec = importlib.util.spec_from_file_location("telegram_menu_diagnostic", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def old_container():
    return {
        "Config": {
            "Labels": {
                "com.docker.compose.project": "shluz-owner-check",
                "com.docker.compose.service": "telegram_bot",
            },
            "Env": ["TELEGRAM_BOT_TOKEN=98765:TOP-SECRET-SYNTHETIC"],
        },
        "State": {"Running": False},
    }


def test_global_and_personal_bot_menu_scopes_are_not_conflated(tmp_path, monkeypatch):
    m = load()
    expected = "https://new-instance.trycloudflare.com/miniapp"
    env = tmp_path / "live-miniapp" / "live.env"
    env.parent.mkdir()
    env.write_text(
        "TELEGRAM_BOT_TOKEN=ANOTHER-SECRET\n"
        "TELEGRAM_MINIAPP_URL=" + expected + "\n",
        encoding="utf-8",
    )
    owner = tmp_path / "miniapp-trial" / "owner.json"
    owner.parent.mkdir()
    owner.write_text('{"telegram_id": 10101}', encoding="utf-8")
    monkeypatch.setattr(m, "owned_bot", lambda: (old_container(), "verified"))
    monkeypatch.setattr(m, "inspect", lambda *a: None)
    monkeypatch.setattr(m, "runtime_state", lambda *a, **k: "stopped")
    monkeypatch.setattr(m, "other_running_pollers", lambda token: "0")
    seen = []

    def api(token, method, payload=None):
        assert token == "98765:TOP-SECRET-SYNTHETIC"
        seen.append((method, payload))
        if method == "getMe":
            return {"id": 98765}, "ok"
        if method == "getWebhookInfo":
            return {"url": ""}, "ok"
        if not payload:
            return {"type": "web_app", "web_app": {"url": expected}}, "ok"
        return {"type": "commands"}, "ok"

    info = m.inspect_result(tmp_path, api=api)
    assert info["global_menu"] == "match"
    assert info["owner_menu"] == "commands"
    assert info["owner_scope"] == "known"
    assert info["interpretation"] == "owner_chat_menu_override"
    assert seen[-1] == ("getChatMenuButton", {"chat_id": 10101})
    report = m.format_report(info)
    assert "global_menu: match" in report
    assert "owner_menu: commands" in report
    assert "98765" not in report and "10101" not in report
    assert expected not in report
    assert "ANOTHER-SECRET" not in report


@pytest.mark.parametrize(("global_type", "owner_type", "diagnosis"), [
    ("commands", "default", "global_menu_not_web_app"),
    ("web_app_other_url", "default", "global_web_app_url_mismatch"),
    ("match", "default", "menu_configured"),
    ("match", "web_app_other_url", "owner_chat_menu_override"),
])
def test_menu_status_with_owner_scope(tmp_path, monkeypatch, global_type, owner_type, diagnosis):
    m = load()
    expected = "https://new.trycloudflare.com/miniapp"
    env_path = tmp_path / "live-miniapp" / "live.env"
    env_path.parent.mkdir()
    env_path.write_text("TELEGRAM_MINIAPP_URL=" + expected + "\n", encoding="utf-8")
    owner_path = tmp_path / "miniapp-trial" / "owner.json"
    owner_path.parent.mkdir()
    owner_path.write_text('{"telegram_id": 400}', encoding="utf-8")
    monkeypatch.setattr(m, "owned_bot", lambda: (old_container(), "verified"))
    monkeypatch.setattr(m, "inspect", lambda *a: None)
    monkeypatch.setattr(m, "runtime_state", lambda *a, **kw: "stopped")
    monkeypatch.setattr(m, "other_running_pollers", lambda token: "0")

    def response_for(status):
        if status == "match":
            return {"type": "web_app", "web_app": {"url": expected}}
        if status == "web_app_other_url":
            return {"type": "web_app", "web_app": {"url": "https://old.example/miniapp"}}
        return {"type": status}

    def api(token, method, payload=None):
        if method == "getMe":
            return {"id": 1}, "ok"
        if method == "getWebhookInfo":
            return {"url": ""}, "ok"
        return response_for(owner_type if payload else global_type), "ok"

    info = m.inspect_result(tmp_path, api=api)
    assert info["global_menu"] == global_type
    assert info["owner_menu"] == owner_type
    assert info["interpretation"] == diagnosis


def test_probe_reports_safe_http_code_without_leaking_token(monkeypatch):
    m = load()
    token = "1000:VERY-SENSITIVE-BOT-TOKEN"

    def denied(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 400, "SECRET URL", {}, None)

    monkeypatch.setattr(m.urllib.request, "urlopen", denied)
    _, result = m.telegram_request(token, "getChatMenuButton")
    assert result == "api_400"
    with pytest.raises(ValueError):
        m.telegram_request(token, "setChatMenuButton")
    report = m.format_report({
        "diagnostic": m.PRODUCT,
        "global_menu": result,
        "interpretation": "needs_detailed_local_codex_review",
    })
    assert token not in report
    assert "SECRET" not in report
    assert "api_400" in report


def test_bot_api_error_and_success_without_full_raw_payload(monkeypatch):
    m = load()
    request_values = []

    def success(request, timeout):
        request_values.append((request.full_url, json.loads(request.data), timeout))
        return io.BytesIO(
            b'{"ok":true,"result":{"type":"web_app","web_app":{"url":"https://x.test/miniapp"}}}'
        )

    monkeypatch.setattr(m.urllib.request, "urlopen", success)
    data, state = m.telegram_request("123:secret", "getChatMenuButton", {"chat_id": 44})
    assert state == "ok"
    assert data["type"] == "web_app"
    assert request_values[0][1] == {"chat_id": 44}
    assert request_values[0][2] <= 8


def test_read_only_docker_diagnostics_no_secret_in_output(tmp_path, monkeypatch):
    m = load()
    cmds = []
    old = old_container()
    live = {
        "Config": {
            "Labels": {"io.noclip.permanent-miniapp": "1"},
            "Env": ["TELEGRAM_BOT_TOKEN=98765:TOP-SECRET-SYNTHETIC"],
        },
        "State": {"Running": False},
    }

    def docker(*args, timeout=15):
        cmds.append(args)
        if args == ("ps", "-aq", "--no-trunc",
                    "--filter", "label=com.docker.compose.project=shluz-owner-check",
                    "--filter", "label=com.docker.compose.service=telegram_bot"):
            return "old-id\n"
        if args == ("ps", "-q", "--no-trunc"):
            return ""
        if args[:1] == ("inspect",):
            name = args[1]
            obj = old if name == "old-id" else live if name == "noclip-miniapp" else None
            return json.dumps([obj]) if obj else None
        raise AssertionError("Mutating Docker method attempted: " + str(args))

    monkeypatch.setattr(m, "docker", docker)
    result = m.inspect_result(
        tmp_path, api=lambda token, method, payload=None: (
            {"id": 1} if method == "getMe" else {"url": ""} if method == "getWebhookInfo"
            else {"type": "commands"}, "ok"
        ),
    )
    report = m.format_report(result)
    assert "old-id" not in report
    assert "98765" not in report and "TOP-SECRET" not in report
    assert result["interpretation"] == "global_menu_not_web_app"
    assert all(args[0] in {"inspect", "ps"} for args in cmds)
    assert "telegram_auth: valid" in report


def test_report_never_renders_unknown_raw_values(tmp_path):
    m = load()
    malware = "https://api.telegram.org/bot999:ACTUAL-SECRET/getChatMenuButton"
    report = m.format_report({
        "diagnostic": m.PRODUCT,
        "time_utc": "2026-10-24 10:00:00 UTC",
        "global_menu": malware,
        "owner_scope": malware,
        "interpretation": malware,
        "possible_same_token_running": malware,
    })
    assert malware not in report
    assert report.count("invalid") >= 4


def test_expected_url_rejects_malformed_and_symlink_config(tmp_path):
    m = load()
    folder = tmp_path / "live-miniapp"
    folder.mkdir()
    p = folder / "live.env"
    p.write_text("TELEGRAM_MINIAPP_URL=https://evil.example/miniapp\n")
    assert m.expected_miniapp(tmp_path) == (None, "invalid")
    p.write_text(
        "TELEGRAM_MINIAPP_URL=https://first.trycloudflare.com/miniapp\n"
        "TELEGRAM_MINIAPP_URL=https://second.trycloudflare.com/miniapp\n"
    )
    assert m.expected_miniapp(tmp_path) == (None, "invalid")


def test_owner_id_never_printed_and_parse_is_strict(tmp_path, monkeypatch):
    m = load()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    folder = tmp_path / "miniapp-trial"
    folder.mkdir()
    (folder / "owner.json").write_text('{"telegram_id": 111}', encoding="utf-8")
    assert m.owner_chat_id(tmp_path) == 111
    (folder / "owner.json").write_text('{"telegram_id": true}', encoding="utf-8")
    assert m.owner_chat_id(tmp_path) is None
