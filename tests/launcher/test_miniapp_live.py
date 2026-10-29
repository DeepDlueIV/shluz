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
        "Config": {
            "Labels": {
                "com.docker.compose.project": "shluz-owner-check",
                "com.docker.compose.service": "shluz",
            }
        },
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
        old_bot(),
        {"image": "shluz-preview:" + "b" * 40},
        settings(),
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
    path = m.secure_env(tmp_path, m.environment_for(old_bot(), "https://test.trycloudflare.com"))
    text = path.read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN=123456:" + "X" * 35 in text
    assert "TELEGRAM_BOT_TOKEN='" not in text
    assert "VENICE_API_KEY" not in text


def test_runtime_uses_existing_gateway_mounts_and_no_blank_volumes(tmp_path, monkeypatch):
    m = load()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / ".env.owner").write_text("KEY=value")
    (tmp_path / "runtime.json").write_text(json.dumps(settings()))
    monkeypatch.setattr(
        m, "docker", lambda *args, **kwargs: "[]" if args[:2] == ("volume", "inspect") else ""
    )
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
    monkeypatch.setattr(
        m, "gateway_and_old", lambda: (gateway(), {**old_bot(), "State": {"Running": False}})
    )
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
    monkeypatch.setattr(
        m.updater,
        "inspect_container",
        lambda s: gateway() if s == "shluz" else {**old_bot(), "State": {"Running": False}},
    )
    monkeypatch.setattr(m, "inspect", lambda *a: {**old_bot(), "State": {"Running": False}})
    monkeypatch.setattr(m, "docker", lambda *a, **kw: calls.append(a) or "")
    monkeypatch.setattr(m, "save_state", lambda *a: calls.append(("state", "stopped")))
    m.stop(tmp_path)
    assert ("state", "stopped") in calls
    assert any(c[:2] == ("stop", "--time") for c in calls)
    assert not any(c and c[0] in {"rm", "down", "start"} for c in calls)
    assert not any(c[:2] == ("volume", "rm") for c in calls)


@pytest.mark.parametrize("failed_stage", ["panel", "menu"])
def test_error_after_journal_stops_everything_without_automatic_classic(
    tmp_path, monkeypatch, failed_stage
):
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
    monkeypatch.setattr(
        m,
        "prepare_images",
        lambda *a: (
            {"commit": m.PREVIEW_COMMIT, "image": "shluz-preview:" + m.PREVIEW_COMMIT},
            "cloudflare/cloudflared@sha256:" + "a" * 64,
        ),
    )
    monkeypatch.setattr(
        m, "ensure_preflight", lambda *a: ("ci-bot-token", {"type": "commands"}, "")
    )
    monkeypatch.setattr(m, "start_gateway", lambda *a: events.append(("gateway-ready",)))
    monkeypatch.setattr(m, "backup", lambda *a: events.append(("backup-done",)))
    monkeypatch.setattr(m, "owned", lambda *a: None)
    monkeypatch.setattr(
        m,
        "save_state",
        lambda root, state: events.append(
            (
                "journal",
                state["phase"],
            )
        ),
    )
    monkeypatch.setattr(m.trial, "wait_tunnel", lambda *a, **kw: "https://test.trycloudflare.com")
    monkeypatch.setattr(m, "secure_env", lambda *a: tmp_path / "env")
    monkeypatch.setattr(m, "safe_candidate_args", lambda *a: ("create", "candidate"))

    def fail_stage(*args):
        raise m.LiveError("CI menu failure")

    monkeypatch.setattr(
        m.trial, "wait_ready", fail_stage if failed_stage == "panel" else lambda *a: None
    )
    monkeypatch.setattr(m, "wait_menu", fail_stage)
    monkeypatch.setattr(
        m, "restore_classic", lambda *a, **kw: pytest.fail("automatic classic rollback")
    )
    monkeypatch.setattr(m, "stop", lambda *a, **kw: events.append(("stop-all",)))
    with pytest.raises(m.LiveError):
        m.start(tmp_path)
    assert events.index(("journal", "preparing")) < events.index(("gateway-ready",))
    assert events.index(("backup-done",)) < events.index(
        ("network", "create", "--label", m.LIVE_LABEL + "=1", m.LIVE_NETWORK)
    )
    assert events.index(("journal", "preparing")) < events.index(
        ("network", "create", "--label", m.LIVE_LABEL + "=1", m.LIVE_NETWORK)
    )
    assert ("stop-all",) in events
    assert not any(e[:2] == ("volume", "rm") for e in events)


def test_healthy_new_bot_survives_failure_to_open_telegram_browser(tmp_path, monkeypatch):
    m = load()
    events = []
    commands = []
    tunnel_created = [False]

    def docker(*args, **kwargs):
        commands.append(args)
        if args[:2] == ("run", "-d"):
            tunnel_created[0] = True
        return "linux/amd64" if args[:2] == ("info", "--format") else ""

    monkeypatch.setattr(m, "docker", docker)
    monkeypatch.setattr(m.trial, "root_folder", lambda: tmp_path / "miniapp-trial")
    monkeypatch.setattr(m.trial, "inspect", lambda *a: None)
    monkeypatch.setattr(m, "read_state", lambda *a: None)
    monkeypatch.setattr(m, "gateway_and_old", lambda: (gateway(), old_bot()))
    monkeypatch.setattr(m, "current_settings", lambda *a: settings())
    monkeypatch.setattr(
        m,
        "prepare_images",
        lambda *a: (
            {"commit": m.PREVIEW_COMMIT, "image": "shluz-preview:" + m.PREVIEW_COMMIT},
            "cloudflare/cloudflared@sha256:" + "a" * 64,
        ),
    )
    monkeypatch.setattr(
        m,
        "ensure_preflight",
        lambda *a: ("ci-bot-token", {"type": "commands"}, "NoClipTestBot"),
    )
    monkeypatch.setattr(m, "start_gateway", lambda *a: None)
    monkeypatch.setattr(m, "backup", lambda *a: None)
    monkeypatch.setattr(
        m,
        "owned",
        lambda name: (
            {
                "Id": "tunnel-id",
                "Config": {"Labels": {m.LIVE_LABEL: "1"}},
                "State": {"Running": True, "StartedAt": "new"},
            }
            if name == m.LIVE_TUNNEL and tunnel_created[0]
            else None
        ),
    )
    monkeypatch.setattr(m, "save_state", lambda _root, data: events.append(data["phase"]))
    monkeypatch.setattr(m.trial, "wait_tunnel", lambda *a, **kw: "https://test.trycloudflare.com")
    monkeypatch.setattr(m, "secure_env", lambda *a: tmp_path / "env")
    monkeypatch.setattr(m, "safe_candidate_args", lambda *a: ("create", "candidate"))
    monkeypatch.setattr(m.trial, "wait_ready", lambda *a: None)
    monkeypatch.setattr(m, "wait_menu", lambda *a: events.append("menu-confirmed") or "verified")
    monkeypatch.setattr(m, "restore_classic", lambda *a: pytest.fail("unexpected rollback"))
    monkeypatch.setattr(
        m.webbrowser, "open", lambda *a: (_ for _ in ()).throw(OSError("no browser"))
    )
    m.start(tmp_path)
    assert events == ["preparing", "menu-confirmed", "active"]
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


def test_telegram_menu_confirmation_waits_for_current_web_app(monkeypatch):
    m = load()
    responses = iter(
        [
            {"type": "web_app", "web_app": {"url": "https://old.trycloudflare.com/miniapp"}},
            {"type": "web_app", "web_app": {"url": "https://current.trycloudflare.com/miniapp"}},
        ]
    )
    calls = []

    def telegram(token, method, **kwargs):
        calls.append((token, method))
        return True if method == "setChatMenuButton" else next(responses)

    monkeypatch.setattr(m.trial, "telegram", telegram)
    monkeypatch.setattr(m.time, "sleep", lambda _: None)
    assert m.wait_menu("synthetic-ci-token", "https://current.trycloudflare.com") == "verified"
    assert calls == [
        ("synthetic-ci-token", "getChatMenuButton"),
        ("synthetic-ci-token", "setChatMenuButton"),
        ("synthetic-ci-token", "getChatMenuButton"),
    ]


def test_wait_menu_repairs_stale_global_url_and_reads_it_back(monkeypatch):
    m = load()
    reads = iter(
        [
            {"type": "web_app", "web_app": {"url": "https://old.trycloudflare.com/miniapp"}},
            {"type": "web_app", "web_app": {"url": "https://current.trycloudflare.com/miniapp"}},
        ]
    )
    calls = []

    def telegram(token, method, **body):
        calls.append((method, body))
        return next(reads) if method == "getChatMenuButton" else True

    monkeypatch.setattr(m.trial, "telegram", telegram)
    monkeypatch.setattr(m, "container_menu_probe", lambda *a, **kw: "unreachable")
    assert m.wait_menu("synthetic-ci-token", "https://current.trycloudflare.com") == "verified"
    assert [method for method, _ in calls] == [
        "getChatMenuButton",
        "setChatMenuButton",
        "getChatMenuButton",
    ]
    assert calls[1][1]["menu_button"]["web_app"]["url"] == (
        "https://current.trycloudflare.com/miniapp"
    )


def test_wait_menu_distinguishes_telegram_unavailable_and_rejected(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "host_menu_probe", lambda *a, **kw: "unreachable")
    monkeypatch.setattr(m, "container_menu_probe", lambda *a, **kw: "unreachable")
    assert (
        m.wait_menu(
            "synthetic-ci-token",
            "https://current.trycloudflare.com",
            attempts=1,
            timeout_seconds=1,
        )
        == "telegram_unavailable"
    )

    monkeypatch.setattr(m, "host_menu_probe", lambda *a, **kw: "rejected")
    assert (
        m.wait_menu(
            "synthetic-ci-token",
            "https://current.trycloudflare.com",
            attempts=8,
            timeout_seconds=10,
        )
        == "telegram_rejected"
    )


def test_active_start_revalidates_https_without_recreating_pollers(tmp_path, monkeypatch):
    m = load()
    state = {
        "phase": "active",
        "origin": "https://same.trycloudflare.com",
        "tunnel_id": "tunnel-id",
        "tunnel_started_at": "2026-10-24T10:00:00.000000000Z",
        "cloudflared_image": "cloudflare/cloudflared@sha256:" + "a" * 64,
    }
    live = {"Id": "bot-id", "State": {"Running": True, "Health": {"Status": "healthy"}}}
    tunnel = {
        "Id": "tunnel-id",
        "State": {"Running": True, "StartedAt": state["tunnel_started_at"]},
    }
    probes = []
    public_checks = []
    commands = []
    monkeypatch.setattr(m, "docker", lambda *a, **kw: commands.append(a) or "linux/amd64")
    monkeypatch.setattr(m.trial, "root_folder", lambda: tmp_path / "miniapp-trial")
    monkeypatch.setattr(m.trial, "inspect", lambda *a: None)
    monkeypatch.setattr(m, "read_state", lambda root: state)
    monkeypatch.setattr(m, "owned", lambda name: live if name == m.LIVE_BOT else tunnel)
    monkeypatch.setattr(
        m, "gateway_and_old", lambda: (gateway(), {**old_bot(), "State": {"Running": False}})
    )
    monkeypatch.setattr(m.trial, "wait_tunnel", lambda *a, **kw: state["origin"])
    monkeypatch.setattr(
        m,
        "public_status",
        lambda *a, **kw: public_checks.append((a, kw)) or "ready",
    )
    monkeypatch.setattr(m.trial, "wait_ready", lambda *a, **kw: probes.append((a, kw)))
    monkeypatch.setattr(m, "wait_menu", lambda *a, **kw: "verified")
    monkeypatch.setattr(m, "save_state", lambda *a: None)
    monkeypatch.setattr(
        m, "prepare_images", lambda *a: pytest.fail("healthy retry must not rebuild")
    )
    m.start(tmp_path)
    assert public_checks and public_checks[0][0][1] == state["origin"]
    assert probes == []
    assert not any(args and args[0] in {"run", "create", "rm"} for args in commands)


def test_active_start_uses_url_from_restarted_tunnel_process(tmp_path, monkeypatch):
    m = load()
    state = {
        "phase": "active",
        "origin": "https://old.trycloudflare.com",
        "tunnel_id": "tunnel-id",
        "tunnel_started_at": "2026-10-24T09:00:00.000000000Z",
        "cloudflared_image": "cloudflare/cloudflared@sha256:" + "a" * 64,
    }
    new_url = "https://new.trycloudflare.com"
    live = {"Id": "bot-id", "State": {"Running": True, "Health": {"Status": "healthy"}}}
    tunnel = {
        "Id": "tunnel-id",
        "State": {"Running": True, "StartedAt": "2026-10-24T10:00:00.000000000Z"},
    }
    menu_urls = []
    saved = []
    monkeypatch.setattr(m, "docker", lambda *a, **kw: "linux/amd64")
    monkeypatch.setattr(m.trial, "root_folder", lambda: tmp_path / "miniapp-trial")
    monkeypatch.setattr(m.trial, "inspect", lambda *a: None)
    monkeypatch.setattr(m, "read_state", lambda root: state)
    monkeypatch.setattr(m, "owned", lambda name: live if name == m.LIVE_BOT else tunnel)
    monkeypatch.setattr(
        m, "gateway_and_old", lambda: (gateway(), {**old_bot(), "State": {"Running": False}})
    )
    monkeypatch.setattr(m.trial, "wait_tunnel", lambda *a, **kw: new_url)
    monkeypatch.setattr(m, "public_status", lambda *a, **kw: "ready")
    monkeypatch.setattr(m, "wait_menu", lambda token, url: menu_urls.append(url) or "verified")
    monkeypatch.setattr(m, "save_state", lambda _root, value: saved.append(dict(value)))
    monkeypatch.setattr(m, "prepare_images", lambda *a: pytest.fail("must reuse current services"))
    m.start(tmp_path)
    assert menu_urls == [new_url]
    assert saved[-1]["origin"] == new_url
    assert saved[-1]["tunnel_started_at"] == tunnel["State"]["StartedAt"]


def test_restart_tunnel_reuses_the_same_managed_container(monkeypatch):
    m = load()
    old = {
        "Id": "d" * 64,
        "Config": {"Labels": {m.LIVE_LABEL: "1"}},
        "State": {"Running": True, "StartedAt": "old"},
    }
    restarted = {**old, "State": {"Running": True, "StartedAt": "new"}}
    calls = []
    seen = []
    monkeypatch.setattr(m, "docker", lambda *a, **kw: calls.append(a) or "")
    monkeypatch.setattr(m, "owned", lambda _name: restarted)
    monkeypatch.setattr(
        m.trial,
        "wait_tunnel",
        lambda name, **kw: seen.append((name, kw)) or "https://rotated.trycloudflare.com",
    )
    url, item = m.restart_tunnel(old, "cloudflare/cloudflared@sha256:" + "a" * 64)
    assert url == "https://rotated.trycloudflare.com"
    assert item["Id"] == old["Id"]
    assert calls == [("stop", "--time", "25", old["Id"]), ("start", old["Id"])]
    assert seen[0][1]["since"] == "new"
    assert not any(call[0] == "run" for call in calls)


def test_active_dns_failure_keeps_gateway_bot_and_volumes(tmp_path, monkeypatch, capsys):
    m = load()
    old_url = "https://old.trycloudflare.com"
    state = {
        "phase": "active",
        "origin": old_url,
        "tunnel_id": "tunnel-id",
        "tunnel_started_at": "same",
        "cloudflared_image": "cloudflare/cloudflared@sha256:" + "a" * 64,
    }
    live = {"Id": "bot-id", "State": {"Running": True, "Health": {"Status": "healthy"}}}
    tunnel = {"Id": "tunnel-id", "State": {"Running": True, "StartedAt": "same"}}
    calls = []
    saved = []
    checks = iter(["dns", "dns"])
    monkeypatch.setattr(m, "docker", lambda *a, **kw: calls.append(a) or "linux/amd64")
    monkeypatch.setattr(m.trial, "root_folder", lambda: tmp_path / "miniapp-trial")
    monkeypatch.setattr(m.trial, "inspect", lambda *a: None)
    monkeypatch.setattr(m, "read_state", lambda root: state)
    monkeypatch.setattr(m, "owned", lambda name: live if name == m.LIVE_BOT else tunnel)
    monkeypatch.setattr(
        m, "gateway_and_old", lambda: (gateway(), {**old_bot(), "State": {"Running": False}})
    )
    monkeypatch.setattr(m.trial, "wait_tunnel", lambda *a, **kw: old_url)
    monkeypatch.setattr(m, "public_status", lambda *a, **kw: next(checks))
    monkeypatch.setattr(
        m, "restart_tunnel", lambda *a, **kw: ("https://new.trycloudflare.com", tunnel)
    )
    monkeypatch.setattr(
        m.trial, "wait_ready", lambda *a, **kw: (_ for _ in ()).throw(m.trial.TrialError("dns"))
    )
    monkeypatch.setattr(m, "clear_managed_menu", lambda *a, **kw: "cleared")
    monkeypatch.setattr(m, "save_state", lambda _root, value: saved.append(dict(value)))
    monkeypatch.setattr(
        m, "prepare_images", lambda *a: pytest.fail("must not rebuild healthy Gateway/bot")
    )
    m.start(tmp_path)
    assert saved[-1]["phase"] == "tunnel_unavailable"
    assert saved[-1]["origin"] is None
    assert saved[-1]["tunnel_status"] == "dns"
    assert saved[-1]["menu_status"] == "cleared"
    assert any(call[:2] == ("stop", "--time") and call[-1] == tunnel["Id"] for call in calls)
    assert not any(call and call[0] in {"run", "rm", "down", "start"} for call in calls)
    assert not any(call and call[0] in {"volume", "compose"} for call in calls)
    output = capsys.readouterr().out
    assert (
        "synthetic-ci-token" not in output
        and old_bot()["Config"]["Env"][0].split("=", 1)[1] not in output
    )


def test_telegram_menu_confirmation_refuses_stale_or_missing_url(monkeypatch):
    m = load()
    calls = []

    def telegram(token, method, **kwargs):
        calls.append(method)
        return True if method == "setChatMenuButton" else {"type": "commands"}

    monkeypatch.setattr(m.trial, "telegram", telegram)
    monkeypatch.setattr(m.time, "sleep", lambda _: None)
    assert (
        m.wait_menu("synthetic-ci-token", "https://current.trycloudflare.com", attempts=3)
        == "menu_stale"
    )
    assert calls == ["getChatMenuButton", "setChatMenuButton"] + ["getChatMenuButton"] * 3


def test_telegram_menu_confirmation_deadline_is_bounded(monkeypatch):
    m = load()
    clock = [0.0]
    calls = []
    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        m.trial,
        "telegram",
        lambda token, method, **kw: (
            calls.append(method)
            or (True if method == "setChatMenuButton" else {"type": "commands"})
        ),
    )
    monkeypatch.setattr(m.time, "sleep", lambda value: clock.__setitem__(0, clock[0] + value))
    assert (
        m.wait_menu(
            "synthetic-ci-token",
            "https://current.trycloudflare.com",
            attempts=20,
            timeout_seconds=1,
        )
        == "menu_stale"
    )
    assert clock[0] <= 1
    assert calls.count("setChatMenuButton") == 1


@pytest.mark.parametrize("failed_stage", ["current_settings", "prepare_images", "ensure_preflight"])
@pytest.mark.parametrize("corrupt", [False, True])
def test_prejournal_failure_stops_owned_runtime(tmp_path, monkeypatch, failed_stage, corrupt):
    m = load()
    calls = []
    if corrupt:
        m.state_file(tmp_path).parent.mkdir(parents=True)
        m.state_file(tmp_path).write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(m.trial, "root_folder", lambda: tmp_path / "unused-trial")
    monkeypatch.setattr(m.trial, "inspect", lambda name: None)
    monkeypatch.setattr(m, "gateway_and_old", lambda: (gateway(), old_bot()))
    monkeypatch.setattr(m, "current_settings", lambda *a: settings())
    monkeypatch.setattr(m, "prepare_images", lambda *a: ({"commit": m.PREVIEW_COMMIT}, "cloud"))
    monkeypatch.setattr(m, "ensure_preflight", lambda *a: ("ci-token", {}, ""))
    monkeypatch.setattr(m, "owned", lambda name: {"Id": name, "State": {"Running": True}})
    monkeypatch.setattr(
        m.updater, "inspect_container", lambda role: gateway() if role == "shluz" else old_bot()
    )
    monkeypatch.setattr(m, "save_state", lambda *a: pytest.fail("failure precedes journal"))
    monkeypatch.setattr(m, "restore_classic", lambda *a, **kw: pytest.fail("no automatic classic"))

    def docker(*args, **kwargs):
        calls.append(args)
        return "linux/amd64" if args[0] == "info" else ""

    monkeypatch.setattr(m, "docker", docker)

    def fail(*args):
        raise m.LiveError("synthetic preflight failure")

    monkeypatch.setattr(m, failed_stage, fail)
    with pytest.raises(m.LiveError, match="preflight"):
        m.start(tmp_path)
    stopped = {c[3] for c in calls if c[:2] == ("stop", "--time")}
    assert stopped == {m.LIVE_BOT, m.LIVE_TUNNEL, old_bot()["Id"], gateway()["Id"]}
    assert not any(c[0] in {"start", "rm", "rename"} for c in calls)


def test_menu_confirmation_rejects_correct_but_late_response(monkeypatch):
    m = load()
    clock = iter([0, 0, 16, 16])
    received = []
    monkeypatch.setattr(m.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(m.time, "sleep", lambda _: pytest.fail("expired budget"))

    def telegram(token, method, **kwargs):
        received.append((method, kwargs))
        if method == "getChatMenuButton":
            return {
                "type": "web_app",
                "web_app": {"url": "https://current.trycloudflare.com/miniapp"},
            }
        return True

    monkeypatch.setattr(m.trial, "telegram", telegram)
    assert (
        m.wait_menu("synthetic-ci-token", "https://current.trycloudflare.com", timeout_seconds=15)
        == "telegram_unavailable"
    )
    assert received[0][1]["request_timeout"] <= 4


def test_telegram_request_timeout_never_enters_api_payload(monkeypatch):
    import io

    m = load()
    observed = []

    def urlopen(request, timeout):
        observed.append((json.loads(request.data), timeout))
        return io.BytesIO(b'{"ok":true,"result":{"type":"commands"}}')

    monkeypatch.setattr(m.trial.urllib.request, "urlopen", urlopen)
    assert m.trial.telegram("synthetic-ci-token", "getChatMenuButton", request_timeout=1.25) == {
        "type": "commands"
    }
    assert observed == [({}, 1.25)]


def test_one_click_start_and_stop_include_website_bridge(tmp_path, monkeypatch):
    from contextlib import nullcontext

    m = load()
    import site_bridge

    monkeypatch.setattr(m, "home", lambda: tmp_path)
    monkeypatch.setattr(m.updater, "update_lock", lambda *_: nullcontext())
    monkeypatch.setattr(m, "start", lambda *_: None)
    monkeypatch.setattr(m, "stop", lambda *_args, **_kwargs: None)
    calls = []
    monkeypatch.setattr(site_bridge, "main", lambda args: calls.append(args) or 0)

    assert m.main(["start"]) == 0
    assert m.main(["stop"]) == 0
    assert calls == [[], ["stop"]]


def _refresh_fixtures(new_url, live_env_url):
    state = {
        "schema": 1,
        "phase": "menu_unverified",
        "old_id": old_bot()["Id"],
        "was_running": False,
        "old_menu": {"type": "commands"},
        "image": "c47ff5b",
        "origin": new_url,
        "tunnel_id": "t" * 64,
        "tunnel_started_at": "2026-10-30T00:00:00Z",
        "cloudflared_image": "cloudflare/cloudflared@sha256:" + "0" * 64,
        "menu_status": "menu_stale",
    }
    live = {
        "Id": "c" * 64,
        "Config": {
            "Image": "shluz-preview:c47ff5b",
            "Labels": {"io.noclip.permanent-miniapp": "1"},
            "Env": ["TELEGRAM_MINIAPP_URL=" + live_env_url],
        },
        "State": {"Running": True, "Health": {"Status": "healthy"}},
    }
    tunnel = {
        "Id": "t" * 64,
        "Config": {"Image": "cloudflare/cloudflared@sha256:" + "0" * 64},
        "State": {"Running": True, "StartedAt": "2026-10-30T00:00:00Z"},
    }
    return state, live, tunnel


def test_refresh_recreates_live_container_when_tunnel_url_changes(tmp_path, monkeypatch):
    m = load()
    new_url = "https://fresh-tunnel.trycloudflare.com"
    state, live, tunnel = _refresh_fixtures(
        new_url, "https://stale-old.trycloudflare.com/miniapp"
    )
    old = {**old_bot(), "State": {"Running": False}}
    calls = []
    saved = {}
    monkeypatch.setattr(m, "docker", lambda *a, **kw: calls.append(a) or "")
    monkeypatch.setattr(m, "owned", lambda name: None)
    monkeypatch.setattr(m, "current_settings", lambda root, gw, ob: settings())
    monkeypatch.setattr(m, "secure_env", lambda root, values: tmp_path / "live.env")
    monkeypatch.setattr(m, "public_status", lambda *a, **kw: "ready")
    monkeypatch.setattr(m, "wait_menu", lambda *a, **kw: "verified")
    monkeypatch.setattr(m, "save_state", lambda root, value: saved.update(value))

    assert m.refresh_active(tmp_path, state, gateway(), old, live, tunnel) is True

    flat = [" ".join(a) for a in calls if a]
    assert any(c.startswith("remove --force noclip-miniapp") for c in flat)
    create = next(" ".join(a) for a in calls if a and a[0] == "create")
    assert "--name noclip-miniapp" in create
    assert "shluz-preview:c47ff5b" in create
    assert "--env-file" in create
    assert any(c.startswith("network connect --alias miniapp") for c in flat)
    assert any(c.startswith("start noclip-miniapp") for c in flat)
    assert saved["phase"] == "active"
    assert saved["origin"] == new_url


def test_refresh_keeps_live_container_when_env_url_matches(tmp_path, monkeypatch):
    m = load()
    new_url = "https://fresh-tunnel.trycloudflare.com"
    state, live, tunnel = _refresh_fixtures(new_url, new_url + "/miniapp")
    old = {**old_bot(), "State": {"Running": False}}
    calls = []
    monkeypatch.setattr(m, "docker", lambda *a, **kw: calls.append(a) or "")
    monkeypatch.setattr(m, "owned", lambda name: None)
    monkeypatch.setattr(m, "public_status", lambda *a, **kw: "ready")
    monkeypatch.setattr(m, "wait_menu", lambda *a, **kw: "verified")
    monkeypatch.setattr(m, "save_state", lambda root, value: None)

    assert m.refresh_active(tmp_path, state, gateway(), old, live, tunnel) is True

    flat = [" ".join(a) for a in calls if a]
    assert not any(c.startswith("remove") for c in flat)
    assert not any(c and c[0] == "create" for c in calls)
