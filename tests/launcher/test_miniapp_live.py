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
        "Config": {"Labels": {
            "com.docker.compose.project": "shluz-owner-check",
            "com.docker.compose.service": "shluz",
        }},
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


def test_error_after_journal_stops_everything_without_automatic_classic(tmp_path, monkeypatch):
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
    monkeypatch.setattr(m.trial, "wait_tunnel", lambda *a, **kw: "https://test.trycloudflare.com")
    monkeypatch.setattr(m, "secure_env", lambda *a: tmp_path / "env")
    monkeypatch.setattr(m, "safe_candidate_args", lambda *a: ("create", "candidate"))
    monkeypatch.setattr(m.trial, "wait_ready", lambda *a: (
        _ for _ in ()).throw(m.trial.TrialError("CI failure"))
    )
    monkeypatch.setattr(
        m, "restore_classic", lambda *a, **kw: pytest.fail("automatic classic rollback")
    )
    monkeypatch.setattr(m, "stop", lambda *a, **kw: events.append(("stop-all",)))
    with pytest.raises(m.trial.TrialError):
        m.start(tmp_path)
    assert events.index(("journal", "preparing")) < events.index(("gateway-ready",))
    assert events.index(("backup-done",)) < events.index((
        "network", "create", "--label", m.LIVE_LABEL + "=1", m.LIVE_NETWORK
    ))
    assert events.index(("journal", "preparing")) < events.index((
        "network", "create", "--label", m.LIVE_LABEL + "=1", m.LIVE_NETWORK
    ))
    assert ("stop-all",) in events
    assert not any(e[:2] == ("volume", "rm") for e in events)



def test_healthy_new_bot_survives_failure_to_open_telegram_browser(tmp_path, monkeypatch):
    m = load()
    events = []
    commands = []
    monkeypatch.setattr(
        m, "docker",
        lambda *a, **kw: commands.append(a) or (
            "linux/amd64" if a[:2] == ("info", "--format") else ""
        ),
    )
    monkeypatch.setattr(m.trial, "root_folder", lambda: tmp_path / "miniapp-trial")
    monkeypatch.setattr(m.trial, "inspect", lambda *a: None)
    monkeypatch.setattr(m, "read_state", lambda *a: None)
    monkeypatch.setattr(m, "gateway_and_old", lambda: (gateway(), old_bot()))
    monkeypatch.setattr(m, "current_settings", lambda *a: settings())
    monkeypatch.setattr(
        m, "prepare_images",
        lambda *a: (
            {"commit": m.PREVIEW_COMMIT, "image": "shluz-preview:" + m.PREVIEW_COMMIT},
            "cloudflare/cloudflared@sha256:" + "a" * 64,
        ),
    )
    monkeypatch.setattr(
        m, "ensure_preflight",
        lambda *a: ("ci-bot-token", {"type": "commands"}, "NoClipTestBot"),
    )
    monkeypatch.setattr(m, "start_gateway", lambda *a: None)
    monkeypatch.setattr(m, "backup", lambda *a: None)
    monkeypatch.setattr(m, "owned", lambda *a: None)
    monkeypatch.setattr(
        m, "save_state", lambda _root, data: events.append(data["phase"])
    )
    monkeypatch.setattr(
        m.trial, "wait_tunnel", lambda *a, **kw: "https://test.trycloudflare.com"
    )
    monkeypatch.setattr(m, "secure_env", lambda *a: tmp_path / "env")
    monkeypatch.setattr(m, "safe_candidate_args", lambda *a: ("create", "candidate"))
    monkeypatch.setattr(m.trial, "wait_ready", lambda *a: None)
    monkeypatch.setattr(m, "restore_classic", lambda *a: pytest.fail("unexpected rollback"))
    monkeypatch.setattr(
        m.webbrowser, "open", lambda *a: (_ for _ in ()).throw(OSError("no browser"))
    )
    m.start(tmp_path)
    assert events == ["preparing", "active"]
    tunnel = next(c for c in commands if c[:2] == ("run", "-d"))
    assert tunnel[tunnel.index("--protocol") + 1] == "auto"
    assert "--no-tls-verify" not in tunnel

def test_pinned_preview_and_trial_revisions_are_different_but_explicit():
    m = load()
    assert m.PREVIEW_COMMIT == "c47ff5b8acc4a7b24b8f34b19e3a9d024aba0fea"
    assert m.MINIAPP_COMMIT == "ceac07cc88401dd99315a368a52d1c24f4f5d697"
    assert m.PREVIEW_COMMIT != m.MINIAPP_COMMIT


@pytest.mark.parametrize("journal", [None, "broken"])
def test_stop_discovers_all_services_without_a_valid_journal(tmp_path, monkeypatch, journal):
    m = load()
    if journal:
        m.state_file(tmp_path).parent.mkdir(parents=True)
        m.state_file(tmp_path).write_text("{not-json", encoding="utf-8")
    calls = []
    monkeypatch.setattr(m, "owned", lambda name: {"Id": name, "State": {"Running": True}})
    monkeypatch.setattr(
        m.updater,
        "inspect_container",
        lambda service: gateway() if service == "shluz" else old_bot(),
    )
    monkeypatch.setattr(m, "docker", lambda *a, **kw: calls.append(a) or "")
    m.stop(tmp_path)
    stopped = {c[3] for c in calls if c[:2] == ("stop", "--time")}
    assert stopped == {m.LIVE_BOT, m.LIVE_TUNNEL, gateway()["Id"], old_bot()["Id"]}
    assert not any(c[0] in {"rm", "down", "start", "rename"} for c in calls)


def test_stop_continues_other_services_after_one_failure(tmp_path, monkeypatch):
    m = load()
    calls = []
    monkeypatch.setattr(m, "read_state", lambda root: None)
    monkeypatch.setattr(m, "owned", lambda name: {"Id": name, "State": {"Running": True}})
    monkeypatch.setattr(
        m.updater,
        "inspect_container",
        lambda service: gateway() if service == "shluz" else old_bot(),
    )

    def run(*a, **kw):
        calls.append(a)
        if a[:2] == ("stop", "--time") and a[3] == m.LIVE_BOT:
            raise m.updater.UpdateError("CI stop failure")
        return ""

    monkeypatch.setattr(m, "docker", run)
    with pytest.raises(m.LiveError):
        m.stop(tmp_path)
    assert ("stop", "--time", "25", gateway()["Id"]) in calls
    assert ("stop", "--time", "25", old_bot()["Id"]) in calls


def test_stop_does_not_trust_old_id_from_journal(tmp_path, monkeypatch):
    m = load()
    m.save_state(tmp_path, {"phase": "active", "old_id": "foreign"})
    calls = []
    monkeypatch.setattr(m, "owned", lambda name: None)
    monkeypatch.setattr(
        m.updater,
        "inspect_container",
        lambda service: gateway() if service == "shluz" else old_bot(),
    )
    monkeypatch.setattr(
        m,
        "inspect",
        lambda name: {"Id": name, "State": {"Running": True}, "Config": {"Labels": {}}},
    )
    monkeypatch.setattr(m, "docker", lambda *a, **kw: calls.append(a) or "")
    m.stop(tmp_path)
    assert not any("foreign" in c for c in calls)
    assert ("stop", "--time", "25", old_bot()["Id"]) in calls


def test_stop_keeps_owned_containers_and_network(tmp_path, monkeypatch):
    m = load()
    calls = []
    monkeypatch.setattr(m, "owned", lambda name: {"Id": name, "State": {"Running": True}})
    monkeypatch.setattr(m, "docker", lambda *a, **kw: calls.append(a) or "")
    m.stop_owned()
    assert len([c for c in calls if c[0] == "stop"]) == 2
    assert not any(c[0] == "rm" or c[:2] == ("network", "rm") for c in calls)


def test_stopped_owned_containers_are_retained_before_restart(tmp_path, monkeypatch):
    m = load()
    calls = []
    monkeypatch.setattr(m, "owned", lambda name: {"Id": name, "State": {"Running": False}})
    monkeypatch.setattr(m, "docker", lambda *a, **kw: calls.append(a) or "")
    m.retire_owned()
    renamed = [c for c in calls if c[0] == "rename"]
    assert {c[1] for c in renamed} == {m.LIVE_BOT, m.LIVE_TUNNEL}
    assert all(c[2].startswith(c[1] + "-previous-") for c in renamed)
    assert not any(c[0] == "rm" for c in calls)


def test_restart_reuses_only_owned_network(tmp_path, monkeypatch):
    m = load()
    calls = []
    monkeypatch.setattr(
        m,
        "docker",
        lambda *a, **kw: calls.append(a) or json.dumps([{"Labels": {m.LIVE_LABEL: "1"}}]),
    )
    m.ensure_network()
    assert not any(c[:2] == ("network", "create") for c in calls)
    monkeypatch.setattr(m, "docker", lambda *a, **kw: json.dumps([{"Labels": {}}]))
    with pytest.raises(m.LiveError):
        m.ensure_network()


@pytest.mark.parametrize("wrapped", [False, True])
def test_prepare_reuses_verified_loaded_image_without_download(tmp_path, monkeypatch, wrapped):
    m = load()
    expected = "sha256:" + "e" * 64
    local = "sha256:" + "f" * 64 if wrapped else expected
    preview = {
        "commit": m.PREVIEW_COMMIT,
        "image": "shluz-preview:" + m.PREVIEW_COMMIT,
        "image_config_digest": expected,
    }
    panel = {"cloudflared_image": "cloudflare/cloudflared@sha256:" + "a" * 64}
    monkeypatch.setattr(
        m,
        "manifest_from_release",
        lambda url, validate, revision: dict(preview) if revision == m.PREVIEW_COMMIT else panel,
    )
    monkeypatch.setattr(
        m.updater,
        "inspect_image",
        lambda image: {"Id": local, "Os": "linux", "Architecture": "amd64"},
    )
    monkeypatch.setattr(m.updater, "exported_config_digest", lambda image: expected)
    monkeypatch.setattr(
        m.updater,
        "download_image",
        lambda *a: pytest.fail("verified image must not download archive"),
    )
    monkeypatch.setattr(m.updater, "prepare_image", lambda *a: pytest.fail("already verified"))
    monkeypatch.setattr(m, "docker", lambda *a, **kw: pytest.fail("images are already loaded"))
    manifest, _ = m.prepare_images(tmp_path)
    assert manifest["local_image_id"] == local
    assert not (tmp_path / "versions").exists()


def test_stop_refuses_foreign_compose_but_stops_other_services(tmp_path, monkeypatch):
    m = load()
    calls = []
    foreign = {
        **old_bot(),
        "Config": {
            "Labels": {
                "com.docker.compose.project": "another-project",
                "com.docker.compose.service": "telegram_bot",
            }
        },
    }
    monkeypatch.setattr(m, "owned", lambda name: None)
    monkeypatch.setattr(
        m.updater, "inspect_container", lambda service: gateway() if service == "shluz" else foreign
    )
    monkeypatch.setattr(m, "docker", lambda *a, **kw: calls.append(a) or "")
    with pytest.raises(m.LiveError):
        m.stop(tmp_path)
    assert ("stop", "--time", "25", gateway()["Id"]) in calls
    assert not any(foreign["Id"] in c for c in calls)


def test_explicit_rollback_can_restore_classic_without_journal(tmp_path, monkeypatch):
    m = load()
    events = []
    monkeypatch.setattr(
        m.updater,
        "inspect_container",
        lambda service: gateway() if service == "shluz" else old_bot(),
    )
    monkeypatch.setattr(m, "start_gateway", lambda item: events.append(("gateway", item["Id"])))
    monkeypatch.setattr(
        m,
        "restore_classic",
        lambda root, state: events.append(("classic", state["old_id"], state["was_running"])),
    )
    m.stop(tmp_path, rollback=True)
    assert events == [("gateway", gateway()["Id"]), ("classic", old_bot()["Id"], True)]


def test_stop_catches_archived_labelled_polling_without_removing_it(tmp_path, monkeypatch):
    m = load()
    calls = []
    identifier = "c" * 64
    monkeypatch.setattr(m, "owned", lambda name: None)
    monkeypatch.setattr(
        m,
        "inspect",
        lambda name: {
            "Id": name,
            "State": {"Running": True},
            "Config": {"Labels": {m.LIVE_LABEL: "1"}},
        },
    )

    def run(*a, **kw):
        calls.append(a)
        return identifier if a[:2] == ("ps", "-aq") else ""

    monkeypatch.setattr(m, "docker", run)
    m.stop_owned()
    assert ("stop", "--time", "25", identifier) in calls
    assert not any(c[0] in {"rm", "rename"} for c in calls)
