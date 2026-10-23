"""Неразрушающие проверки единого portable-меню (без реальных Docker и секретов)."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def load():
    folder = Path(__file__).resolve().parents[2] / "launcher"
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
    spec = importlib.util.spec_from_file_location("owner_control", folder / "owner_control.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def item(service, is_running=False):
    volume = "gw-data" if service == "shluz" else "tg-data"
    return {
        "Id": service + "-id",
        "Image": "sha256:" + "1" * 64,
        "State": {"Running": is_running, "Health": {"Status": "healthy"}},
        "Config": {
            "Labels": {
                "com.docker.compose.project": "shluz-owner-check",
                "com.docker.compose.service": service,
            }
        },
        "Mounts": [{"Type": "volume", "Destination": "/app/data", "Name": volume}],
    }


def settings():
    return {
        "project": "shluz-owner-check",
        "volumes": {"gateway": "gw-data", "bot": "tg-data", "connector": "key-data"},
    }


def test_portable_home_is_opt_in_and_legacy_default_survives(tmp_path, monkeypatch):
    m = load()
    target = tmp_path / "NoClip"
    monkeypatch.setenv("SHLUZ_HOME", str(target))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    assert m.portable_home() == target.resolve()
    assert m.updater.home_root() == target.resolve()
    assert m.legacy_home() == (tmp_path / "AppData" / "Shluz").resolve()
    monkeypatch.delenv("SHLUZ_HOME")
    assert m.updater.home_root() == (tmp_path / "AppData" / "Shluz")


def test_migration_checks_sha_preserves_original_and_repeats_safely(tmp_path, monkeypatch):
    m = load()
    appdata = tmp_path / "AppData"
    old = appdata / "Shluz"
    portable = tmp_path / "Portable"
    (old / "config" / "legacy").mkdir(parents=True)
    (old / "backups" / "001" / "gateway").mkdir(parents=True)
    (old / "versions" / "abcdef").mkdir(parents=True)
    secret = "SHLUZ_ADMIN_PASSWORD=never-print\n"
    (old / "config" / ".env.owner").write_text(secret)
    (old / "config" / "legacy" / "telegram-access.json").write_text("{}")
    (old / "versions" / "abcdef" / "image.tar.gz").write_bytes(b"archive")
    (old / "backups" / "001" / "gateway" / "data.tar.gz").write_bytes(b"snapshot")
    (old / "runtime.json").write_text(json.dumps(settings()))
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    assert m.migrate_legacy(portable) == 5
    assert m.migrate_legacy(portable) == 0
    assert (old / "config" / ".env.owner").read_text() == secret
    assert (portable / "config" / ".env.owner").read_text() == secret
    assert (portable / "migration.json").is_file()
    assert m.sha256(old / "versions" / "abcdef" / "image.tar.gz") == m.sha256(
        portable / "versions" / "abcdef" / "image.tar.gz"
    )


def test_migration_preflight_refuses_conflict_without_overwrite(tmp_path, monkeypatch):
    m = load()
    old = tmp_path / "app" / "Shluz" / "config"
    old.mkdir(parents=True)
    (old / ".env.owner").write_text("password=old")
    (old / ".env.telegram").write_text("bot=source")
    new = tmp_path / "portable"
    (new / "config").mkdir(parents=True)
    (new / "config" / ".env.owner").write_text("password=new")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "app"))
    with pytest.raises(m.ControlError, match="отличаются"):
        m.migrate_legacy(new)
    assert (new / "config" / ".env.owner").read_text() == "password=new"
    assert not (new / "config" / ".env.telegram").exists()
    assert not (new / "migration.json").exists()


def test_legacy_trial_must_be_stopped_before_import(tmp_path, monkeypatch):
    m = load()
    state = tmp_path / "app" / "Shluz" / "miniapp-trial"
    state.mkdir(parents=True)
    (state / "active.json").write_text("{}")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "app"))
    with pytest.raises(m.ControlError, match="Mini App"):
        m.migrate_legacy(tmp_path / "portable")
    assert (state / "active.json").exists()


def test_migration_rejects_source_symlink(tmp_path, monkeypatch):
    m = load()
    old = tmp_path / "app" / "Shluz" / "config"
    old.mkdir(parents=True)
    (tmp_path / "outside").write_text("secret")
    try:
        (old / "linked").symlink_to(tmp_path / "outside")
    except (OSError, NotImplementedError):
        pytest.skip("Недостаточно прав для создания symlink")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "app"))
    with pytest.raises(m.ControlError, match="ссылки"):
        m.migrate_legacy(tmp_path / "portable")


def test_checked_volumes_refuse_wrong_or_missing_database(tmp_path, monkeypatch):
    m = load()
    current = {"shluz": item("shluz", True), "telegram_bot": item("telegram_bot", True)}
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        return ""

    monkeypatch.setattr(m.updater, "docker", docker)
    with pytest.raises(m.ControlError, match="отсутствует"):
        m.check_volumes(settings(), current)
    assert all(c[:2] == ("volume", "inspect") for c in calls)
    wrong = settings()
    wrong["volumes"]["gateway"] = "new-empty-volume"
    with pytest.raises(m.ControlError, match="не совпадает"):
        m.check_volumes(wrong, current)
    assert not any("create" in c or "rm" in c or "down" in c for c in calls)


def test_start_only_existing_ids_after_gateway_health(tmp_path, monkeypatch):
    m = load()
    current = {"shluz": item("shluz"), "telegram_bot": item("telegram_bot")}
    events = []
    monkeypatch.setattr(m, "require_docker", lambda: None)
    monkeypatch.setattr(m, "trial_occupied", lambda root: False)
    monkeypatch.setattr(m, "containers", lambda: current)
    monkeypatch.setattr(m, "settings_for", lambda *args: settings())
    monkeypatch.setattr(m, "check_volumes", lambda *args: {"gateway", "bot"})
    monkeypatch.setattr(
        m, "wait_healthy", lambda identifier: events.append(("healthy", identifier))
    )
    monkeypatch.setattr(m.updater, "docker", lambda *args, **kw: events.append(args) or "")
    m.start_services(tmp_path)
    assert events == [
        ("start", "shluz-id"), ("healthy", "shluz-id"), ("start", "telegram_bot-id")
    ]


def test_stop_never_deletes_containers_or_volumes(tmp_path, monkeypatch):
    m = load()
    current = {"shluz": item("shluz", True), "telegram_bot": item("telegram_bot", True)}
    calls = []
    monkeypatch.setattr(m, "require_docker", lambda: None)
    monkeypatch.setattr(m, "trial_occupied", lambda root: False)
    monkeypatch.setattr(m, "containers", lambda: current)
    monkeypatch.setattr(m, "settings_for", lambda *args: settings())
    monkeypatch.setattr(m, "check_volumes", lambda *args: {"gateway", "bot"})
    monkeypatch.setattr(m.updater, "docker", lambda *args, **kw: calls.append(args) or "")
    m.stop_services(tmp_path)
    assert calls == [
        ("stop", "--time", "25", "telegram_bot-id"),
        ("stop", "--time", "25", "shluz-id"),
    ]


def test_backup_failure_starts_only_previously_running_containers(tmp_path, monkeypatch):
    m = load()
    current = {"shluz": item("shluz", True), "telegram_bot": item("telegram_bot", False)}
    calls = []
    monkeypatch.setattr(m, "require_docker", lambda: None)
    monkeypatch.setattr(m, "trial_occupied", lambda root: False)
    monkeypatch.setattr(m, "containers", lambda: current)
    monkeypatch.setattr(m, "settings_for", lambda *args: settings())
    monkeypatch.setattr(m, "check_volumes", lambda *args: {"gateway"})

    def docker(*args, **kwargs):
        calls.append(args)
        if args[0] == "image":
            return '[{"Id":"fake"}]'
        if args[0] == "inspect":
            return json.dumps([{"State": {"Running": False}}])
        return ""

    monkeypatch.setattr(m.updater, "docker", docker)
    monkeypatch.setattr(
        m.updater, "backup_volumes", lambda *args: (_ for _ in ()).throw(ValueError("failure"))
    )
    with pytest.raises(m.ControlError, match="backup"):
        m.backup_services(tmp_path)
    assert ("start", "shluz-id") in calls
    assert ("start", "telegram_bot-id") not in calls
    assert all("rm" not in call and "down" not in call for call in calls)


def test_new_bundle_has_one_entry_and_no_secrets(tmp_path):
    import zipfile

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "package_owner_control", root / "scripts" / "package_owner_control.py"
    )
    pack = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pack)
    revision = pack.PINNED_MINIAPP_COMMIT
    manifest = {
        "schema": 2,
        "channel": "miniapp-test",
        "commit": revision,
        "image": "shluz-preview:" + revision,
        "image_size": 120,
        "image_sha256": "a" * 64,
        "image_id": "sha256:" + "b" * 64,
        "image_config_digest": "sha256:" + "b" * 64,
        "platform": "linux/amd64",
        "image_url": (
            "https://github.com/DeepDlueIV/shluz/releases/download/miniapp-test-"
            + revision + "/image.tar.gz"
        ),
        "cloudflared_image": "cloudflare/cloudflared@sha256:" + "c" * 64,
    }
    src = tmp_path / "manifest.json"
    src.write_text(json.dumps(manifest))
    dest = tmp_path / "NoClip-SHLUZ-Portable.zip"
    pack.build(dest, src)
    with zipfile.ZipFile(dest) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert "NoClip-SHLUZ/NoClip.cmd" in names
        assert "NoClip-SHLUZ/trial-manifest.json" in names
        assert not any(".env" in n or "backups" in n or "config/" in n for n in names)
        assert b"SHLUZ_HOME=%~dp0" in archive.read("NoClip-SHLUZ/NoClip.cmd")
    assert dest.with_suffix(".sha256").is_file()
