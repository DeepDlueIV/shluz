"""Повторный запуск website bridge без изменения Telegram, Vercel и контейнеров."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def load():
    folder = Path(__file__).resolve().parents[2] / "launcher"
    sys.path.insert(0, str(folder))
    spec = importlib.util.spec_from_file_location("site_bridge_test", folder / "site_bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def container():
    return {
        "Id": "owned",
        "Image": "sha256:d681c163ed411b2e82df6e4d67a83faf44019253da9fb5c938b997fcff6bd50b",
        "Config": {
            "Labels": {
                "io.noclip.site-bridge": "1",
                "org.opencontainers.image.revision": "08145402383f681f2843e325f054af3d50dac1e0",
            }
        },
        "HostConfig": {
            "PortBindings": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18001"}]}
        },
        "State": {"Running": True},
    }


def configured(tmp_path, monkeypatch):
    m = load()
    runtime = tmp_path / "config" / "site-bridge"
    runtime.mkdir(parents=True)
    for name in ["ngrok.yml", "bridge-password.txt", "traffic-policy.yml"]:
        (runtime / name).write_text("test fixture, never used for live auth")
    (tmp_path / "config" / "site-bridge.json").write_text(
        json.dumps(
            {
                "port": 18001,
                "configPath": str(runtime / "ngrok.yml"),
                "trafficPolicy": str(runtime / "traffic-policy.yml"),
            }
        )
    )
    monkeypatch.setattr(m, "inspect_gateway", lambda: container())
    monkeypatch.setattr(m, "resolve_ngrok", lambda: ("launch-ngrok", "actual-ngrok"))
    return m, m.settings(tmp_path)


def agent(cfg, pid=31):
    return {
        "pid": pid,
        "executable": "actual-ngrok",
        "args": [
            "ngrok",
            "http",
            cfg["upstream"],
            "--config",
            str(cfg["config"]),
            "--traffic-policy-file",
            str(cfg["policy"]),
            "--inspect=false",
        ],
    }


def test_foreign_label_image_or_nonlocal_port_is_rejected():
    m = load()
    for change in ["label", "image", "binding"]:
        item = container()
        if change == "label":
            item["Config"]["Labels"]["io.noclip.site-bridge"] = "other"
        elif change == "image":
            item["Image"] = "sha256:" + "f" * 64
        else:
            item["HostConfig"]["PortBindings"]["8000/tcp"][0]["HostIp"] = "0.0.0.0"
        with pytest.raises(m.BridgeError):
            m.validate_gateway(item, 18001)


def test_missing_state_reuses_owned_agent_instead_of_duplicate(tmp_path, monkeypatch):
    m, cfg = configured(tmp_path, monkeypatch)
    monkeypatch.setattr(m, "agents", lambda: [agent(cfg)])
    monkeypatch.setattr(
        m,
        "local_tunnels",
        lambda: [
            {
                "public_url": "https://assigned.ngrok-free.dev",
                "config": {"addr": cfg["upstream"], "inspect": False},
            }
        ],
    )
    monkeypatch.setattr(m, "wait_gateway", lambda *a: None)
    monkeypatch.setattr(m, "prepare_policy", lambda *a: None)
    monkeypatch.setattr(m, "spawn_agent", lambda *a: pytest.fail("duplicate agent"))
    m.start(tmp_path)
    state = json.loads((cfg["runtime"] / "state.json").read_text())
    assert state["pid"] == 31
    assert state["https_url"] == "https://assigned.ngrok-free.dev"
    assert "password" not in state


def test_existing_foreign_agent_on_4040_is_not_reused(tmp_path, monkeypatch):
    m, cfg = configured(tmp_path, monkeypatch)
    foreign = agent(cfg)
    foreign["args"][foreign["args"].index("--config") + 1] = str(tmp_path / "foreign.yml")
    monkeypatch.setattr(m, "agents", lambda: [foreign])
    monkeypatch.setattr(m, "local_tunnels", lambda: [{"config": {"addr": cfg["upstream"]}}])
    monkeypatch.setattr(m, "spawn_agent", lambda *a: pytest.fail("must not spawn"))
    with pytest.raises(m.BridgeError, match="4040"):
        m.start(tmp_path)


def test_stop_without_state_only_stops_owned_agent_and_gateway(tmp_path, monkeypatch):
    m, cfg = configured(tmp_path, monkeypatch)
    foreign = agent(cfg, 32)
    foreign["executable"] = "foreign-ngrok"
    monkeypatch.setattr(m, "agents", lambda: [agent(cfg), foreign])
    killed, commands = [], []
    monkeypatch.setattr(m.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(m, "docker", lambda *a, **kw: commands.append(a) or "")
    m.stop(tmp_path)
    assert killed == [31]
    assert commands == [("stop", "--timeout", "15", "owned")]
    assert not any("rm" in command for command in commands)


def test_stop_never_touches_foreign_gateway(tmp_path, monkeypatch):
    m, cfg = configured(tmp_path, monkeypatch)
    item = container()
    item["Config"]["Labels"]["io.noclip.site-bridge"] = "foreign"
    monkeypatch.setattr(m, "inspect_gateway", lambda: item)
    monkeypatch.setattr(m, "agents", lambda: [])
    monkeypatch.setattr(m, "docker", lambda *a, **kw: pytest.fail("foreign gateway stop"))
    with pytest.raises(m.BridgeError):
        m.stop(tmp_path)


def test_agent_ownership_requires_exact_policy_config_executable_and_inspection(
    tmp_path, monkeypatch
):
    m, cfg = configured(tmp_path, monkeypatch)
    good = agent(cfg)
    assert m.owned_agent(good, cfg, "actual-ngrok")
    for option in ["--config", "--traffic-policy-file"]:
        bad = agent(cfg)
        bad["args"][bad["args"].index(option) + 1] += ".foreign"
        assert not m.owned_agent(bad, cfg, "actual-ngrok")
    bad = agent(cfg)
    bad["args"].remove("--inspect=false")
    assert not m.owned_agent(bad, cfg, "actual-ngrok")


def test_launch_command_hidden_and_never_contains_auth_values(tmp_path, monkeypatch):
    m, cfg = configured(tmp_path, monkeypatch)
    calls = []

    class Process:
        pid = 81

    monkeypatch.setattr(
        m.subprocess, "Popen", lambda command, **kw: calls.append((command, kw)) or Process()
    )
    m.spawn_agent("launch-ngrok", cfg)
    command, kwargs = calls[0]
    assert "--inspect=false" in command
    assert command[command.index("--config") + 1] == str(cfg["config"])
    assert command[command.index("--traffic-policy-file") + 1] == str(cfg["policy"])
    assert "password" not in " ".join(command).lower()
    assert kwargs.get("creationflags", 0) != 0


def test_missing_container_never_creates_new_installation(tmp_path, monkeypatch):
    m, cfg = configured(tmp_path, monkeypatch)
    monkeypatch.setattr(m, "inspect_gateway", lambda: None)
    monkeypatch.setattr(m, "docker", lambda *a, **kw: pytest.fail("no container mutation"))
    with pytest.raises(m.BridgeError):
        m.start(tmp_path)


def test_http_error_on_ngrok_port_is_a_conflict_not_absent_agent(monkeypatch):
    import urllib.error

    m = load()

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError("http://127.0.0.1:4040/api/tunnels", 403, "hidden", None, None)

    monkeypatch.setattr(m.urllib.request, "urlopen", fail)
    with pytest.raises(m.BridgeError, match="4040"):
        m.local_tunnels()


def test_corrupt_state_is_not_needed_for_safe_stop(tmp_path, monkeypatch):
    m, cfg = configured(tmp_path, monkeypatch)
    (cfg["runtime"] / "state.json").write_text("{invalid JSON")
    monkeypatch.setattr(m, "agents", lambda: [agent(cfg)])
    monkeypatch.setattr(m.os, "kill", lambda *a: None)
    calls = []
    monkeypatch.setattr(m, "docker", lambda *a, **kw: calls.append(a) or "")
    m.stop(tmp_path)
    assert calls == [("stop", "--timeout", "15", "owned")]


def test_missing_ngrok_does_not_prevent_stopping_owned_gateway(tmp_path, monkeypatch):
    m, cfg = configured(tmp_path, monkeypatch)

    def missing():
        raise m.BridgeError("ngrok absent")

    monkeypatch.setattr(m, "resolve_ngrok", missing)
    calls = []
    monkeypatch.setattr(m, "docker", lambda *a, **kw: calls.append(a) or "")
    with pytest.raises(m.BridgeError):
        m.stop(tmp_path)
    assert calls == [("stop", "--timeout", "15", "owned")]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL and PowerShell")
def test_prepare_only_removes_foreign_acl_and_never_prints_password(tmp_path):
    import os
    import subprocess

    runtime = tmp_path / "private"
    runtime.mkdir()
    (runtime / "bridge-password.txt").write_text("a" * 64)
    subprocess.run(
        ["icacls", str(runtime), "/grant", "*S-1-1-0:(OI)(CI)R"], capture_output=True, check=True
    )
    script = Path(__file__).resolve().parents[2] / "web" / "tunnel" / "start-windows.ps1"
    shell = str(Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(script),
            "-RuntimeDirectory",
            str(runtime),
            "-PrepareOnly",
        ],
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0
    assert b"a" * 64 not in result.stdout + result.stderr
    policy = (runtime / "traffic-policy.yml").read_text()
    assert policy.count("req.url.path == '") == 3
    assert "basic-auth" in policy and "enforce: true" in policy
    query = (
        "[IO.Directory]::GetAccessControl('"
        + str(runtime).replace("'", "''")
        + "').GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])|"
        "ForEach-Object {$_.IdentityReference.Value}|ConvertTo-Json"
    )
    acl = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", query],
        capture_output=True,
        text=True,
        check=True,
    )
    identities = set(json.loads(acl.stdout))
    assert "S-1-1-0" not in identities
    assert "S-1-5-18" in identities
    assert len(identities) == 2


@pytest.mark.skipif(sys.platform != "win32", reason="Windows command-line parser")
def test_native_windows_parser_preserves_paths_with_spaces():
    import subprocess

    m = load()
    args = [
        "C:\\Program Files\\ngrok\\ngrok.exe",
        "http",
        "http://127.0.0.1:18001",
        "--config",
        "D:\\NoClip with spaces\\ngrok.yml",
        "--inspect=false",
    ]
    assert m.windows_args(subprocess.list2cmdline(args)) == args
