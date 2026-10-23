"""Проверка готовности тестовой Mini App без живых секретов и сети."""

import importlib.util
import sys
from pathlib import Path

import pytest


def load():
    root = Path(__file__).resolve().parents[2] / "launcher"
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location("miniapp_trial_probe", root / "miniapp_trial.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepared(monkeypatch, *, healthy=True, local=True, host=False, container=False):
    m = load()
    monkeypatch.setattr(
        m, "inspect",
        lambda _: {
            "State": {
                "Running": True,
                "Health": {"Status": "healthy" if healthy else "unhealthy"},
            }
        },
    )
    monkeypatch.setattr(m, "probe_local_panel", lambda _: local)
    monkeypatch.setattr(m, "probe_windows_https", lambda _: host)
    monkeypatch.setattr(m, "probe_container_https", lambda *_: container)
    monkeypatch.setattr(m.time, "sleep", lambda _: None)
    return m


def test_container_public_probe_can_validate_if_windows_proxy_blocks(monkeypatch):
    m = prepared(monkeypatch, container=True)
    assert m.wait_ready("bot", "https://test.trycloudflare.com", attempts=1) is None


def test_no_https_probe_means_no_launch(monkeypatch):
    m = prepared(monkeypatch)
    with pytest.raises(m.TrialError, match="HTTPS-туннель"):
        m.wait_ready("bot", "https://test.trycloudflare.com", attempts=1)


def test_unhealthy_bot_reported_not_misclassified_as_https(monkeypatch):
    m = prepared(monkeypatch, healthy=False)
    with pytest.raises(m.TrialError, match="Telegram-бот"):
        m.wait_ready("bot", "https://test.trycloudflare.com", attempts=1)


def test_local_panel_required_even_if_public_probe_says_yes(monkeypatch):
    m = prepared(monkeypatch, local=False, host=True, container=True)
    with pytest.raises(m.TrialError, match="HTTP-панель"):
        m.wait_ready("bot", "https://test.trycloudflare.com", attempts=1)


def test_container_probe_returns_only_fixed_status(monkeypatch):
    m = load()
    commands = []

    def fake_docker(*args, **kwargs):
        commands.append((args, kwargs))
        return "ready"

    monkeypatch.setattr(m, "docker", fake_docker)
    assert m.probe_container_https("candidate", "https://test.trycloudflare.com")
    assert commands[0][0][:2] == ("exec", "candidate")
    assert commands[0][1].get("optional") is True
