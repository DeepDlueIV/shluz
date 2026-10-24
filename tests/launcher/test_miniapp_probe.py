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
        lambda *a, **kw: {
            "State": {
                "Running": True,
                "Health": {"Status": "healthy" if healthy else "unhealthy"},
            }
        },
    )
    monkeypatch.setattr(m, "probe_local_panel", lambda *a, **kw: local)
    monkeypatch.setattr(m, "probe_windows_https", lambda *a, **kw: host)
    monkeypatch.setattr(m, "probe_container_https", lambda *a, **kw: container)
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

def test_tunnel_waits_for_edge_registration_not_only_allocated_url(monkeypatch):
    import subprocess

    m = load()
    seen = []
    outputs = [
        b'https://test.trycloudflare.com',
        b'https://test.trycloudflare.com\nRegistered tunnel connection connIndex=0',
    ]

    def logs(*args, **kwargs):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, outputs[min(len(seen) - 1, 1)], b'')

    monkeypatch.setattr(m.subprocess, 'run', logs)
    monkeypatch.setattr(m, 'inspect', lambda *a, **kw: {'State': {'Running': True}})
    monkeypatch.setattr(m.time, 'sleep', lambda _: None)
    assert m.wait_tunnel('tunnel', require_registered=True) == 'https://test.trycloudflare.com'
    assert len(seen) == 2


def test_unregistered_edge_is_not_reported_ready_and_logs_are_not_exposed(monkeypatch):
    import subprocess

    m = load()
    now = [0.0]
    monkeypatch.setattr(m.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(m.time, 'sleep', lambda value: now.__setitem__(0, now[0] + value))
    monkeypatch.setattr(m, 'inspect', lambda *a, **kw: {'State': {'Running': True}})
    monkeypatch.setattr(m.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(
        a, 0, b'https://test.trycloudflare.com PRIVATE_LOG_DETAIL', b''))
    with pytest.raises(m.TrialError, match='Cloudflare edge') as error:
        m.wait_tunnel('tunnel', require_registered=True, timeout_seconds=2)
    assert 'PRIVATE_LOG_DETAIL' not in str(error.value)
    assert now[0] <= 2


@pytest.mark.parametrize('failure,expected', [
    ('dns', 'dns'), ('tls', 'tls'), ('timeout', 'timeout'), ('http', 'http:403'),
])
def test_host_probe_reports_only_safe_error_category(monkeypatch, failure, expected):
    import socket
    import ssl
    import urllib.error

    m = load()
    exceptions = {
        'dns': urllib.error.URLError(socket.gaierror('private DNS detail')),
        'tls': urllib.error.URLError(ssl.SSLCertVerificationError('private TLS detail')),
        'timeout': TimeoutError('private timeout detail'),
        'http': urllib.error.HTTPError('https://private.example', 403, 'secret', None, None),
    }
    def fail(*a, **kw):
        raise exceptions[failure]
    monkeypatch.setattr(m.urllib.request, 'urlopen', fail)
    diagnostic = {}
    assert not m.probe_windows_https('https://test.trycloudflare.com', diagnostics=diagnostic)
    assert diagnostic == {'windows': expected}


def test_readiness_has_shared_deadline_not_attempts_times_network_timeout(monkeypatch):
    m = load()
    now = [0.0]
    count = [0]
    monkeypatch.setattr(m.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(m.time, 'sleep', lambda value: now.__setitem__(0, now[0] + value))
    monkeypatch.setattr(m, 'inspect', lambda *a, **kw: {
        'State': {'Running': True, 'Health': {'Status': 'healthy'}}})
    monkeypatch.setattr(m, 'probe_local_panel', lambda *a, **kw: True)
    def probe(*a, timeout, diagnostics, **kw):
        count[0] += 1
        now[0] += timeout
        diagnostics['windows'] = 'timeout'
        return False
    monkeypatch.setattr(m, 'probe_windows_https', probe)
    monkeypatch.setattr(m, 'probe_container_https', probe)
    with pytest.raises(m.TrialError, match='HTTPS-туннель'):
        m.wait_ready('bot', 'https://test.trycloudflare.com', attempts=90, timeout_seconds=6)
    assert now[0] <= 6
    assert count[0] <= 2

def test_trial_tunnel_uses_available_auto_transport(monkeypatch, tmp_path):
    m = load()
    original_read = Path.read_text
    monkeypatch.setattr(
        Path, 'read_text', lambda path, **kw:
        '{}' if path.name == 'trial-manifest.json' else original_read(path, **kw))
    monkeypatch.setattr(m, 'validate_manifest', lambda _: {
        'commit': 'a' * 40, 'image': 'test-image', 'cloudflared_image': 'test-cloudflared'})
    old = {'Id': 'a' * 64, 'State': {'Running': False}, 'Config': {'Env': []},
           'Mounts': [{'Destination': '/app/data', 'Type': 'volume', 'Name': 'history'}]}
    monkeypatch.setattr(m.updater, 'inspect_container', lambda role:
                        old if role == 'telegram_bot' else {'State': {'Running': True}})
    monkeypatch.setattr(m, 'cleanup', lambda _: None)
    monkeypatch.setattr(m, 'owner_id', lambda _: 100)
    monkeypatch.setattr(m, 'telegram', lambda *a, **kw: {'id': 100})
    monkeypatch.setattr(m, 'candidate_args', lambda *a: [])
    monkeypatch.setattr(m, 'inspect', lambda *a, **kw: None)
    monkeypatch.setattr(m.updater, 'download_image', lambda *a: None)
    monkeypatch.setattr(m.updater, 'prepare_image', lambda *a: None)
    command = []
    def docker(*args, **kw):
        if args[0] == 'info':
            return 'linux/amd64'
        if args[:2] == ('run', '-d'):
            command.extend(args)
            raise m.TrialError('diagnostic stop before launching any service')
        return ''
    monkeypatch.setattr(m, 'docker', docker)
    with pytest.raises(m.TrialError, match='diagnostic stop'):
        m.run_trial(tmp_path)
    assert command[command.index('--protocol') + 1] == 'auto'
    assert '--no-tls-verify' not in command
