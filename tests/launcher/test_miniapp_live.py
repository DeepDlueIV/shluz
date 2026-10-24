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


@pytest.mark.parametrize("failed_stage", ["panel", "menu"])
def test_failure_after_transaction_started_rolls_back_owned_runtime(
    tmp_path, monkeypatch, failed_stage
):
    m = load()
    events = []
    state = {"phase": "preparing"}

    def docker(*args, **kwargs):
        events.append(tuple(args))
        if args[:2] == ("info", "--format"):
            return "linux/amd64"
        return ""

    monkeypatch.setattr(m, "docker", docker)
    monkeypatch.setattr(m.trial, "root_folder", lambda: tmp_path / "miniapp-trial")
    monkeypatch.setattr(m.trial, "inspect", lambda *a: None)
    monkeypatch.setattr(m, "read_state", lambda *a: state)
    monkeypatch.setattr(m, "gateway_and_old", lambda: (gateway(), old_bot()))
    monkeypatch.setattr(m, "current_settings", lambda *a: settings())
    monkeypatch.setattr(m, "prepare_images", lambda *a: (
        {"commit": m.PREVIEW_COMMIT, "image": "shluz-preview:" + m.PREVIEW_COMMIT},
        "cloudflare/cloudflared@sha256:" + ("a" * 64),
    ))
    monkeypatch.setattr(m, "ensure_preflight", lambda *a: ("ci-bot-token", {"type": "commands"}, ""))
    monkeypatch.setattr(m, "start_gateway", lambda *a: events.append(("gateway-ready",)))
    monkeypatch.setattr(m, "backup", lambda *a: events.append(("backup-done",)))
    monkeypatch.setattr(m, "owned", lambda *a: None)
    monkeypatch.setattr(m, "save_state", lambda root, value: events.append(("journal", value["phase"])))
    monkeypatch.setattr(m.trial, "wait_tunnel", lambda *a, **kw: "https://test.trycloudflare.com")
    monkeypatch.setattr(m, "secure_env", lambda *a: tmp_path / "env")
    monkeypatch.setattr(m, "safe_candidate_args", lambda *a: ("create", "candidate"))

    def fail_stage(*args):
        raise m.LiveError("synthetic transaction failure")

    monkeypatch.setattr(
        m.trial, "wait_ready",
        fail_stage if failed_stage == "panel" else lambda *a: None,
    )
    monkeypatch.setattr(m, "wait_menu", fail_stage)
    monkeypatch.setattr(
        m, "restore_classic",
        lambda *a, **kw: pytest.fail("automatic classic rollback"),
    )
    monkeypatch.setattr(m, "stop", lambda *a, **kw: events.append(("stop-all",)))
    with pytest.raises(m.LiveError):
        m.start(tmp_path)
    assert ("journal", "preparing") in events
    assert ("stop-all",) in events




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



def test_telegram_menu_confirmation_uses_docker_when_windows_returns_stale(monkeypatch):
    m = load()
    calls = []
    inside = iter(["stale", "match"])
    monkeypatch.setattr(
        m, "container_menu_probe",
        lambda url, **kw: calls.append("docker") or next(inside),
    )
    monkeypatch.setattr(
        m, "host_menu_probe",
        lambda token, url, **kw: calls.append("windows") or "stale",
    )
    monkeypatch.setattr(m.time, "sleep", lambda _: None)
    assert m.wait_menu("ci-token", "https://current.trycloudflare.com") is True
    assert calls == ["docker", "windows", "docker"]


def test_telegram_menu_confirmation_repairs_known_stale_menu_once(monkeypatch):
    m = load()
    actions = []
    reads = iter(["stale", "stale", "match"])

    def probe(url, *, action="read", **kwargs):
        actions.append(action)
        return "match" if action == "set" else next(reads)

    monkeypatch.setattr(m, "container_menu_probe", probe)
    monkeypatch.setattr(m, "host_menu_probe", lambda *a, **kw: "stale")
    monkeypatch.setattr(m.time, "sleep", lambda _: None)
    assert m.wait_menu("ci-token", "https://current.trycloudflare.com") is True
    assert actions.count("set") == 1


def test_telegram_stale_menu_is_unverified_not_a_reason_to_stop_gateway(
    monkeypatch, capsys
):
    m = load()
    operations = []
    monkeypatch.setattr(
        m, "container_menu_probe",
        lambda url, *, action="read", **kw: operations.append(action) or "stale",
    )
    monkeypatch.setattr(m, "host_menu_probe", lambda *a, **kw: "stale")
    monkeypatch.setattr(m.time, "sleep", lambda _: None)
    assert m.wait_menu(
        "ci-token", "https://current.trycloudflare.com", attempts=3
    ) is False
    assert operations.count("set") == 1
    warning = capsys.readouterr().out
    assert "прежнее меню" in warning
    assert "НЕ подтверждена" in warning
    assert "будут остановлены" not in warning


def test_telegram_menu_confirmation_network_outage_preserves_healthy_bot(monkeypatch):
    m = load()
    attempts = []
    monkeypatch.setattr(
        m, "container_menu_probe",
        lambda *a, **kw: attempts.append("docker") or "unreachable",
    )
    monkeypatch.setattr(
        m, "host_menu_probe",
        lambda *a, **kw: attempts.append("windows") or "unreachable",
    )
    monkeypatch.setattr(m.time, "sleep", lambda _: None)
    assert m.wait_menu("ci-token", "https://current.trycloudflare.com", attempts=2) is False
    assert attempts == ["docker", "windows", "docker", "windows"]



def test_menu_ultimately_becomes_current_after_initial_stale_readback(monkeypatch):
    m = load()
    calls = []
    events = iter(["stale", "stale", "match"])

    def inside(url, *, action="read", **kwargs):
        calls.append(action)
        return "stale" if action == "set" else next(events)

    monkeypatch.setattr(m, "container_menu_probe", inside)
    monkeypatch.setattr(m, "host_menu_probe", lambda *a, **kw: "stale")
    monkeypatch.setattr(m.time, "sleep", lambda _: None)
    assert m.wait_menu("synthetic-ci-token", "https://current.trycloudflare.com") is True
    assert "set" in calls
    assert calls.index("set") < len(calls) - 1


def test_telegram_menu_confirmation_deadline_is_bounded(monkeypatch):
    m = load()
    clock = iter([0, 0, 31])
    calls = []
    monkeypatch.setattr(m.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(
        m, "container_menu_probe",
        lambda *a, **kw: calls.append("docker") or "unreachable",
    )
    monkeypatch.setattr(
        m, "host_menu_probe", lambda *a, **kw: pytest.fail("deadline expired"),
    )
    assert m.wait_menu("ci-token", "https://current.trycloudflare.com") is False
    assert calls == ["docker"]


@pytest.mark.parametrize("failed_stage", ["current_settings", "prepare_images", "ensure_preflight"])
@pytest.mark.parametrize("corrupt", [False, True])
def test_read_only_preflight_failure_preserves_owned_runtime(
    tmp_path, monkeypatch, failed_stage, corrupt
):
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
        m.updater, "inspect_container",
        lambda role: gateway() if role == "shluz" else old_bot(),
    )
    monkeypatch.setattr(m, "save_state", lambda *a: pytest.fail("failure precedes journal"))

    def docker(*args, **kwargs):
        calls.append(args)
        return "linux/amd64" if args[0] == "info" else ""

    monkeypatch.setattr(m, "docker", docker)

    def fail(*args):
        raise m.LiveError("synthetic preflight failure")

    monkeypatch.setattr(m, failed_stage, fail)
    with pytest.raises(m.LiveError, match="preflight"):
        m.start(tmp_path)
    assert not any(c[0] in {"stop", "start", "rm", "rename"} for c in calls)


def test_menu_confirmation_does_not_accept_late_readback(monkeypatch):
    m = load()
    clock = iter([0, 0, 16, 16])
    calls = []
    monkeypatch.setattr(m.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(
        m, "container_menu_probe",
        lambda *a, **kw: calls.append("docker") or "match",
    )
    monkeypatch.setattr(
        m, "host_menu_probe", lambda *a, **kw: pytest.fail("expired budget"),
    )
    assert m.wait_menu(
        "ci-token", "https://current.trycloudflare.com", timeout_seconds=15
    ) is False
    assert calls == ["docker"]


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


def test_docker_menu_probe_never_passes_bot_secret_to_docker_cli(monkeypatch):
    m = load()
    calls = []
    token = "123456:" + "X" * 35
    monkeypatch.setattr(
        m, "owned", lambda *a: {
            "Id": "verified-owned-id", "State": {"Running": True},
        },
    )
    monkeypatch.setattr(
        m, "docker", lambda *a, **kw: calls.append((a, kw)) or "match",
    )
    url = "https://current.trycloudflare.com"
    assert m.container_menu_probe(url) == "match"
    assert m.container_menu_probe(url, action="set") == "match"
    assert all(args[:2] == ("exec", "verified-owned-id") for args, _ in calls)
    assert all(args[-1] == url + "/miniapp" for args, _ in calls)
    assert all(token not in repr(args) for args, _ in calls)
    assert all("--env" not in args for args, _ in calls)


def test_host_menu_probe_distinguishes_transport_error_from_stale_menu(monkeypatch):
    m = load()
    url = "https://current.trycloudflare.com"
    monkeypatch.setattr(
        m.trial, "telegram",
        lambda *a, **kw: (_ for _ in ()).throw(m.trial.TrialError("offline")),
    )
    assert m.host_menu_probe("ci-token", url) == "unreachable"
    monkeypatch.setattr(
        m.trial, "telegram", lambda *a, **kw: {"type": "commands"},
    )
    assert m.host_menu_probe("ci-token", url) == "stale"
    monkeypatch.setattr(
        m.trial, "telegram",
        lambda *a, **kw: {
            "type": "web_app", "web_app": {"url": url + "/miniapp"}
        },
    )
    assert m.host_menu_probe("ci-token", url) == "match"


@pytest.mark.parametrize("menu_state", ["unreachable", "stale", "api_error", "api_auth"])
def test_start_does_not_stop_healthy_bot_just_for_unverified_menu(
    tmp_path, monkeypatch, menu_state
):
    m = load()
    phases = []
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
        m, "ensure_preflight", lambda *a: ("ci-token", {"type": "commands"}, "")
    )
    monkeypatch.setattr(m, "start_gateway", lambda *a: None)
    monkeypatch.setattr(m, "backup", lambda *a: None)
    monkeypatch.setattr(m, "owned", lambda *a: None)
    monkeypatch.setattr(
        m, "save_state", lambda _root, state: phases.append(state["phase"])
    )
    monkeypatch.setattr(
        m.trial, "wait_tunnel", lambda *a, **kw: "https://test.trycloudflare.com"
    )
    monkeypatch.setattr(m, "secure_env", lambda *a: tmp_path / "env")
    monkeypatch.setattr(m, "safe_candidate_args", lambda *a: ("create", "candidate"))
    monkeypatch.setattr(m.trial, "wait_ready", lambda *a: None)
    monkeypatch.setattr(
        m, "container_menu_probe",
        lambda *a, **kw: menu_state,
    )
    monkeypatch.setattr(
        m, "host_menu_probe",
        lambda *a, **kw: menu_state,
    )
    monkeypatch.setattr(m.time, "sleep", lambda _: None)
    monkeypatch.setattr(m, "stop", lambda *a: pytest.fail("healthy bot stopped"))
    m.start(tmp_path)
    assert phases == ["preparing", "menu_unverified"]
    assert not any(a and a[0] == "rm" for a in commands)
    assert ("start", m.LIVE_BOT) in commands


def test_pending_menu_state_rechecks_without_docker_recreate(tmp_path, monkeypatch):
    m = load()
    state = {
        "phase": "menu_unverified",
        "origin": "https://current.trycloudflare.com",
        "old_id": old_bot()["Id"],
    }
    active = {"State": {"Running": True, "Health": {"Status": "healthy"}}}
    tunnel = {"State": {"Running": True}}
    events = []
    monkeypatch.setattr(m, "docker", lambda *a, **kw: (
        events.append(a) or "linux/amd64"
    ))
    monkeypatch.setattr(m.trial, "root_folder", lambda: tmp_path / "miniapp-trial")
    monkeypatch.setattr(m.trial, "inspect", lambda *a: None)
    monkeypatch.setattr(m, "read_state", lambda *a: state)
    monkeypatch.setattr(m, "owned", lambda n: active if n == m.LIVE_BOT else tunnel)
    monkeypatch.setattr(m, "gateway_and_old", lambda: (
        gateway(), {**old_bot(), "State": {"Running": False}}
    ))
    monkeypatch.setattr(m, "wait_menu", lambda *a: True)
    monkeypatch.setattr(
        m, "save_state", lambda _root, value: events.append(("saved", value["phase"]))
    )
    monkeypatch.setattr(m, "prepare_images", lambda *a: pytest.fail("image replaced"))
    monkeypatch.setattr(m, "backup", lambda *a: pytest.fail("duplicated backup"))
    m.start(tmp_path)
    assert state["phase"] == "active"
    assert ("saved", "active") in events
    assert not any(a[0] in {"stop", "start", "run", "rm"} for a in events)


def test_docker_telegram_script_is_syntactically_valid_and_bounded():
    m = load()
    compile(m.MENU_INSIDE_DOCKER, "<docker-telegram-check>", "exec")
    assert "getChatMenuButton" in m.MENU_INSIDE_DOCKER
    assert "setChatMenuButton" in m.MENU_INSIDE_DOCKER
    assert "err.code == 429" in m.MENU_INSIDE_DOCKER
    assert "token=os.environ.get('TELEGRAM_BOT_TOKEN'" in m.MENU_INSIDE_DOCKER
    assert "123456:" not in m.MENU_INSIDE_DOCKER


@pytest.mark.parametrize("response_url,expected", [
    ("https://current.trycloudflare.com/miniapp", "match"),
    ("https://old.trycloudflare.com/miniapp", "stale"),
])
def test_docker_readback_script_uses_internal_token_only(
    monkeypatch, capsys, response_url, expected
):
    import io
    import urllib.request

    m = load()
    secret = "bot-secret-protected-by-container-env"
    menu_url = "https://current.trycloudflare.com/miniapp"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", secret)
    monkeypatch.setattr(sys, "argv", ["python", "read", menu_url])
    seen = []

    def fake_urlopen(request, timeout):
        seen.append((request.full_url, json.loads(request.data), timeout))
        return io.BytesIO(json.dumps({
            "ok": True, "result": {
                "type": "web_app",
                "web_app": {"url": response_url},
            },
        }).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    exec(m.MENU_INSIDE_DOCKER, {"__name__": "__main__"})
    captured = capsys.readouterr()
    assert captured.out.strip() == expected
    assert secret not in captured.out and secret not in captured.err
    assert len(seen) == 1
    assert seen[0][0].endswith("/getChatMenuButton")
    assert seen[0][1] == {}
    assert seen[0][2] <= 4


def test_docker_readback_rate_limit_is_transient(monkeypatch, capsys):
    import urllib.error
    import urllib.request

    m = load()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-only")
    monkeypatch.setattr(
        sys, "argv", ["python", "read", "https://current.trycloudflare.com/miniapp"]
    )

    def rate_limited(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", rate_limited)
    exec(m.MENU_INSIDE_DOCKER, {"__name__": "__main__"})
    assert capsys.readouterr().out.strip() == "unreachable"


def test_preflight_exception_does_not_stop_existing_healthy_services(tmp_path, monkeypatch):
    m = load()
    events = []
    current = {
        "phase": "active",
        "origin": "https://previous.trycloudflare.com",
        "old_id": "classic-id",
    }
    monkeypatch.setattr(
        m, "docker",
        lambda *a, **kw: "linux/amd64" if a[:2] == ("info", "--format") else "",
    )
    monkeypatch.setattr(m.trial, "root_folder", lambda: tmp_path / "trial")
    monkeypatch.setattr(m.trial, "inspect", lambda *a: None)
    monkeypatch.setattr(m, "read_state", lambda *a: current)
    monkeypatch.setattr(m, "save_state", lambda *a: None)
    monkeypatch.setattr(m, "gateway_and_old", lambda: (gateway(), old_bot()))
    monkeypatch.setattr(m, "owned", lambda name: {
        m.LIVE_BOT: {
            "Id": "live-id", "State": {
                "Running": True, "Health": {"Status": "healthy"}
            },
        },
        m.LIVE_TUNNEL: {
            "Id": "tunnel-id", "State": {"Running": True}
        },
    }.get(name))
    def fail_before_mutation(*args, **kwargs):
        raise m.LiveError("safe synthetic read-only preflight failure")
    monkeypatch.setattr(m, "refresh_active_tunnel", fail_before_mutation)
    monkeypatch.setattr(m, "stop", lambda *a: events.append("stop"))
    with pytest.raises(m.LiveError, match="safe synthetic"):
        m.start(tmp_path)
    assert "stop" not in events


def test_stateful_preparing_failure_uses_existing_rollback(tmp_path, monkeypatch):
    m = load()
    events = []
    states = {"phase": "preparing"}
    monkeypatch.setattr(
        m, "docker",
        lambda *a, **kw: "linux/amd64" if a[:2] == ("info", "--format") else "",
    )
    monkeypatch.setattr(m.trial, "root_folder", lambda: tmp_path / "trial")
    monkeypatch.setattr(m.trial, "inspect", lambda *a: None)
    monkeypatch.setattr(m, "read_state", lambda *a: states)
    monkeypatch.setattr(m, "stop", lambda *a: events.append("rollback"))
    monkeypatch.setattr(m, "gateway_and_old", lambda: (None, None))
    monkeypatch.setattr(m, "current_settings", lambda *a: settings())
    monkeypatch.setattr(m, "prepare_images", lambda *a: (
        {"commit": m.PREVIEW_COMMIT, "image": "shluz-preview:" + m.PREVIEW_COMMIT},
        "cloudflare/cloudflared@sha256:" + "a" * 64,
    ))
    monkeypatch.setattr(m, "ensure_preflight", lambda *a: ("ci-token", {}, ""))
    monkeypatch.setattr(m, "save_state", lambda _root, value: states.update(value))
    monkeypatch.setattr(m, "backup", lambda *a: None)
    monkeypatch.setattr(m, "retire_owned", lambda *a: None)
    monkeypatch.setattr(m, "start_gateway", lambda *a: None)
    monkeypatch.setattr(m, "create_tunnel", lambda *a: (_ for _ in ()).throw(
        m.LiveError("stateful synthetic")
    ))
    with pytest.raises(m.LiveError, match="stateful synthetic"):
        m.start(tmp_path)
    assert events == ["rollback"]

