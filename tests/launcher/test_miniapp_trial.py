"""Offline safety checks for the reversible owner trial launcher."""

import importlib.util
import sys
from pathlib import Path

import pytest


def load():
    root = Path(__file__).resolve().parents[2] / "launcher"
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location("miniapp_trial", root / "miniapp_trial.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest():
    rev = "a" * 40
    return dict(
        schema=2,
        channel="miniapp-test",
        commit=rev,
        image="shluz-preview:" + rev,
        image_size=1234,
        image_sha256="b" * 64,
        image_id="sha256:" + "c" * 64,
        image_config_digest="sha256:" + "c" * 64,
        platform="linux/amd64",
        image_url="https://github.com/DeepDlueIV/shluz/releases/download/miniapp-test-"
        + rev
        + "/image.tar.gz",
        cloudflared_image="cloudflare/cloudflared@sha256:" + "d" * 64,
    )


def test_trial_manifest_is_pinned_not_regular_preview():
    m = load()
    data = manifest()
    assert m.validate_manifest(data) == data
    for change in [
        dict(channel="preview"),
        dict(image_url="https://other.example/image"),
        dict(cloudflared_image="cloudflare/cloudflared:latest"),
        dict(image_size=True),
    ]:
        with pytest.raises(m.TrialError):
            m.validate_manifest(data | change)


def old_container():
    return {
        "Id": "a" * 64,
        "State": {"Running": True},
        "Config": {
            "Env": [
                "TELEGRAM_BOT_TOKEN=123456:" + "A" * 35,
                "TELEGRAM_DATABASE_PATH=/app/data/telegram.sqlite",
                "TELEGRAM_GATEWAY_URL=http://shluz:8000",
                "TELEGRAM_CONNECTOR_FILE=/run/shluz-connector/key",
                "VENICE_API_KEY=must-not-copy",
            ],
            "Labels": {
                "com.docker.compose.project": "shluz-owner-check",
                "com.docker.compose.service": "telegram_bot",
            },
        },
        "Mounts": [
            {"Destination": "/app/data", "Type": "volume", "Name": "old_data"},
            {"Destination": "/run/shluz-connector", "Type": "volume", "Name": "connector"},
        ],
        "NetworkSettings": {"Networks": {"old_default": {}}},
    }


def test_trial_environment_never_copies_provider_key_and_requires_owner():
    m = load()
    values = m.trial_environment(old_container(), 100, "https://test.trycloudflare.com")
    assert values["TELEGRAM_INTERFACE"] == "miniapp"
    assert values["TELEGRAM_TRIAL_OWNER_ID"] == "100"
    assert values["TELEGRAM_MINIAPP_URL"] == "https://test.trycloudflare.com/miniapp"
    assert "VENICE_API_KEY" not in values
    for owner in [0, -1, True]:
        with pytest.raises(m.TrialError):
            m.trial_environment(old_container(), owner, "https://test.trycloudflare.com")


def test_trial_never_mounts_old_history_writable_or_publishes_host_ports(tmp_path):
    m = load()
    state = dict(bot_name="noclip-miniapp-bot", network="test_net", volume="test_data")
    args = m.candidate_args(old_container(), manifest(), state, tmp_path / "trial.env")
    command = " ".join(args)
    assert "--publish" not in args and "-p" not in args
    assert "old_data" not in command
    assert "test_data" in command
    assert "source=connector,target=/run/shluz-connector,readonly" in command
    assert "--network" in args and "old_default" in args


def test_copy_history_is_private_snapshot_not_shared(tmp_path):
    import sqlite3
    import subprocess

    m = load()
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    with sqlite3.connect(old / "telegram.sqlite") as db:
        db.execute("create table sample(value text)")
        db.execute("insert into sample values('original')")
    code = m.CLONE_CODE.replace("Path('/source')", f"Path({str(old)!r})")
    code = code.replace("Path('/target')", f"Path({str(new)!r})")
    code = code.replace("os.chown(path, 10001, 10001)", "None")
    subprocess.run([sys.executable, "-c", code], check=True)
    with sqlite3.connect(new / "telegram.sqlite") as db:
        db.execute("update sample set value='trial'")
    with sqlite3.connect(old / "telegram.sqlite") as db:
        assert db.execute("select value from sample").fetchone()[0] == "original"
    assert (new / ".clone-complete").is_file()


def test_cleanup_stops_trial_before_restarting_original(tmp_path, monkeypatch):
    m = load()
    old = old_container()
    calls = []
    state = {
        "original_id": old["Id"],
        "was_running": True,
        "owner_id": 100,
        "old_menu": {"type": "commands"},
        "bot_name": "noclip-miniapp-bot",
        "tunnel_name": "noclip-miniapp-tunnel",
        "network": "trial_net",
    }
    m.save_state(tmp_path, state)

    def docker(*args, **kwargs):
        calls.append(args)
        if args[:1] == ("inspect",):
            import json

            if args[1] == old["Id"]:
                return json.dumps([old])
            return ""
        return ""

    monkeypatch.setattr(m, "docker", docker)
    monkeypatch.setattr(m, "telegram", lambda *a, **kw: calls.append(("restore_menu",)))
    m.cleanup(tmp_path)
    assert calls.index(("restore_menu",)) < calls.index(("start", old["Id"]))
    assert not any("volume" in c for c in calls)
    assert not (tmp_path / "active.json").exists()


def test_existing_bot_with_arbitrary_data_mount_rejected():
    m = load()
    old = old_container()
    old["Mounts"][0]["Type"] = "bind"
    with pytest.raises(m.TrialError):
        m.source_volume(old)
