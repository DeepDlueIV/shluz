"""Permanent Mini App: never activate classic accidentally or destroy existing history."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def load():
    folder = Path(__file__).resolve().parents[2] / "launcher"
    sys.path.insert(0, str(folder))
    spec = importlib.util.spec_from_file_location("miniapp_live", folder / "miniapp_live.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def old_bot():
    return {
        "Id": "a" * 64,
        "Config": {
            "Env": [
                "TELEGRAM_BOT_TOKEN=123456:" + "X" * 35,
                "TELEGRAM_GATEWAY_URL=http://shluz:8000",
                "TELEGRAM_DATABASE_PATH=/app/data/telegram.sqlite",
                "VENICE_API_KEY=DO-NOT-TRANSFER",
                "TELEGRAM_INTERFACE=classic",
                "TELEGRAM_TRIAL_OWNER_ID=123",
            ],
            "Labels": {
                "com.docker.compose.project": "shluz-owner-check",
                "com.docker.compose.service": "telegram_bot",
            },
        },
        "Mounts": [
            {"Destination": "/app/data", "Type": "volume", "Name": "old-history"},
            {"Destination": "/run/shluz-connector", "Type": "volume", "Name": "connector"},
        ],
        "NetworkSettings": {"Networks": {"owner_default": {}}},
        "State": {"Running": True},
    }


def gateway():
    return {
        "Id": "b" * 64,
        "State": {"Running": True, "Health": {"Status": "healthy"}},
        "Mounts": [{"Destination": "/app/data", "Type": "volume", "Name": "gw-data"}],
        "Image": "sha256:" + "f" * 64,
    }


def settings():
    return {
        "project": "shluz-owner-check",
        "volumes": {"gateway": "gw-data", "bot": "old-history", "connector": "connector"},
    }


def test_new_mode_is_global_persistent_with_same_bot_and_no_venice_key():
    m = load()
    env = m.environment_for(old_bot(), "https://small-bird.trycloudflare.com")
    assert env["TELEGRAM_INTERFACE"] == "miniapp"
    assert env["TELEGRAM_TRIAL_OWNER_ID"] == "0"
    assert env["TELEGRAM_BOT_TOKEN"] == old_bot()["Config"]["Env"][0].split("=", 1)[1]
    assert env["TELEGRAM_MINIAPP_URL"] == "https://small-bird.trycloudflare.com/miniapp"
    assert "VENICE_API_KEY" not in env
    for invalid in (
        "http://bad.trycloudflare.com",
        "https://trycloudflare.com.attacker.com",
        "https://a.trycloudflare.com/evil",
        "https://evil.com@a.trycloudflare.com",
    ):
        with pytest.raises(m.LiveError):
            m.environment_for(old_bot(), invalid)


def test_old_history_volume_is_reused_and_live_label_not_trial(tmp_path):
    m = load()
    args = m.safe_candidate_args(
        old_bot(), {"image": "shluz-preview:" + "b" * 40}, settings(),
        tmp_path / "live.env",
    )
    joined = " ".join(args)
    assert "--publish" not in args and "-p" not in args
    assert "source=old-history,target=/app/data" in joined
    assert m.LIVE_LABEL + "=1" in args
    assert m.trial.LABEL + "=1" not in args
    assert "source=connector,target=/run/shluz-connector,readonly" in joined


def test_docker_env_file_does_not_quote_telegram_bot_token(tmp_path, monkeypatch):
    m = load()
    if m.os.name == "nt":
        from subprocess import CompletedProcess

        def fake_run(args, **kwargs):
            if args[0] == "whoami":
                return CompletedProcess(args, 0, stdout='"user","S-1-5-21-123"\r\n')
            assert args[0] == "icacls"
            return CompletedProcess(args, 0)

        monkeypatch.setattr(m.subprocess, "run", fake_run)
    path = m.secure_env(tmp_path, m.environment_for(
        old_bot(), "https://test.trycloudflare.com"
    ))
    text = path.read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN=123456:" + "X" * 35 in text
    assert "TELEGRAM_BOT_TOKEN='" not in text
    assert "VENICE_API_KEY" not in text


def test_runtime_uses_existing_gateway_mounts_and_no_blank_volumes(tmp_path, monkeypatch):
    m = load()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / ".env.owner").write_text("KEY=value")
    (tmp_path / "runtime.json").write_text(json.dumps(settings()))
    monkeypatch.setattr(m, "docker", lambda *args, **kwargs: "[]" if args[:2] == (
        "volume", "inspect"
    ) else "")
    assert m.current_settings(tmp_path, gateway(), old_bot()) == settings()
    wrong = settings()
    wrong["volumes"]["bot"] = "wrong-empty"
    (tmp_path / "runtime.json").write_text(json.dumps(wrong))
    with pytest.raises(m.LiveError, match="не совпадают"):
        m.current_settings(tmp_path, gateway(), old_bot())


def test_start_healthy_live_is_idempotent_and_never_starts_old(tmp_path, monkeypatch):
    m = load()
    state = {"phase": "active", "old_id": old_bot()["Id"]}
    active = {"State": {"Running": True, "Health": {"Status": "healthy"}}}
    tunnel = {"State": {"Running": True}}
    monkeypatch.setattr(m, "docker", lambda *a, **kw: "linux/amd64")
    monkeypatch.setattr(m.trial, "root_folder", lambda: tmp_path / "miniapp-trial")
    monkeypatch.setattr(m.trial, "inspect", lambda *a: None)
    monkeypatch.setattr(m, "read_state", lambda root: state)
    monkeypatch.setattr(m, "owned", lambda n: active if n == m.LIVE_BOT else tunnel)
    monkeypatch.setattr(m, "gateway_and_old", lambda: (
        gateway(), {**old_bot(), "State": {"Running": False}}
    ))
    monkeypatch.setattr(m, "prepare_images", lambda *a: pytest.fail("must not update"))
    m.start(tmp_path)


def test_orphaned_trial_polling_prevents_second_bot(tmp_path, monkeypatch):
    m = load()
    monkeypatch.setattr(m, "docker", lambda *a, **kw: "linux/amd64")
    monkeypatch.setattr(m.trial, "root_folder", lambda: tmp_path / "miniapp-trial")
    monkeypatch.setattr(m.trial, "inspect", lambda *a: {"State": {"Running": True}})
    with pytest.raises(m.LiveError, match="polling"):
        m.start(tmp_path)


def test_stop_never_starts_old_bot_and_keeps_volumes(tmp_path, monkeypatch):
    m = load()
    state = {"phase": "active", "old_id": old_bot()["Id"]}
    calls = []
    monkeypatch.setattr(m, "read_state", lambda *a: state)
    monkeypatch.setattr(m, "owned", lambda *a: {"State": {"Running": True}})
    monkeypatch.setattr(m.updater, "inspect_container", lambda s: (
        gateway() if s == "shluz" else {**old_bot(), "State": {"Running": False}}
    ))
    monkeypatch.setattr(m, "inspect", lambda *a: {**old_bot(), "State": {"Running": False}})
    monkeypatch.setattr(m, "docker", lambda *a, **kw: calls.append(a) or "")
    monkeypatch.setattr(m, "save_state", lambda *a: calls.append(("state", "stopped")))
    m.stop(tmp_path)
    assert ("state", "stopped") in calls
    assert any(c[:2] == ("stop", "--time") for c in calls)
    assert not any(c and c[0] in {"rm", "down", "start"} for c in calls)
    assert not any(c[:2] == ("volume", "rm") for c in calls)


def test_error_after_journal_restores_classic_without_changing_volumes(tmp_path, monkeypatch):
    m = load()
    events = []

    def docker(*args, **kwargs):
        events.append(tuple(args))
        if args[:2] == ("info", "--format"):
            return "linux/amd64"
        return ""

    monkeypatch.setattr(m, "docker", docker)
    monkeypatch.setattr(m.trial, "root_folder", lambda: tmp_path / "miniapp-trial")
    monkeypatch.setattr(m.trial, "inspect", lambda *a: None)
    monkeypatch.setattr(m, "read_state", lambda *a: None)
    monkeypatch.setattr(m, "gateway_and_old", lambda: (gateway(), old_bot()))
    monkeypatch.setattr(m, "current_settings", lambda *a: settings())
    monkeypatch.setattr(m, "prepare_images", lambda *a: (
        {"commit": m.PREVIEW_COMMIT, "image": "shluz-preview:" + m.PREVIEW_COMMIT},
        "cloudflare/cloudflared@sha256:" + "a" * 64,
    ))
    monkeypatch.setattr(
        m, "ensure_preflight", lambda *a: ("ci-bot-token", {"type": "commands"}, "")
    )
    monkeypatch.setattr(m, "start_gateway", lambda *a: events.append(("gateway-ready",)))
    monkeypatch.setattr(m, "backup", lambda *a: events.append(("backup-done",)))
    monkeypatch.setattr(m, "owned", lambda *a: None)
    monkeypatch.setattr(m, "save_state", lambda root, state: events.append((
        "journal", state["phase"],
    )))
    monkeypatch.setattr(m.trial, "wait_tunnel", lambda *a: "https://test.trycloudflare.com")
    monkeypatch.setattr(m, "secure_env", lambda *a: tmp_path / "env")
    monkeypatch.setattr(m, "safe_candidate_args", lambda *a: ("create", "candidate"))
    monkeypatch.setattr(m.trial, "wait_ready", lambda *a: (
        _ for _ in ()).throw(m.trial.TrialError("CI failure"))
    )
    monkeypatch.setattr(m, "restore_classic", lambda *a: events.append(("restore-old",)))
    with pytest.raises(m.trial.TrialError):
        m.start(tmp_path)
    assert events.index(("backup-done",)) < events.index(("journal", "preparing"))
    assert events.index(("journal", "preparing")) < events.index((
        "network", "create", "--label", m.LIVE_LABEL + "=1", m.LIVE_NETWORK
    ))
    assert ("restore-old",) in events
    assert not any(e[:2] == ("volume", "rm") for e in events)


def test_pinned_preview_and_trial_revisions_are_different_but_explicit():
    m = load()
    assert m.PREVIEW_COMMIT == "c47ff5b8acc4a7b24b8f34b19e3a9d024aba0fea"
    assert m.MINIAPP_COMMIT == "ceac07cc88401dd99315a368a52d1c24f4f5d697"
    assert m.PREVIEW_COMMIT != m.MINIAPP_COMMIT
