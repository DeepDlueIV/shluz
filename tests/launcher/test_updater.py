import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

MODULE = Path(__file__).parents[2] / "launcher" / "updater.py"


def load():
    spec = importlib.util.spec_from_file_location("updater", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest():
    sha = "a" * 40
    return {
        "schema": 1,
        "channel": "preview",
        "commit": sha,
        "image": "shluz-preview:" + sha,
        "image_id": "sha256:" + "b" * 64,
        "platform": "linux/amd64",
        "image_size": 100,
        "image_sha256": "c" * 64,
        "image_url": f"https://github.com/DeepDlueIV/shluz/releases/download/preview-{sha}/image.tar.gz",
    }


def test_launcher_is_implemented():
    assert MODULE.exists(), "Нужен настоящий запускатель, а не только план"


def test_manifest_is_pinned_and_rejects_remote_code_and_wrong_channel():
    u = load()
    assert u.validate_manifest(manifest())["commit"] == "a" * 40
    for field, value in [
        ("image_url", "https://evil.test/image"),
        ("channel", "random-branch"),
        ("commit", "../escape"),
        ("image_id", "latest"),
        ("image_size", -1),
    ]:
        data = manifest()
        data[field] = value
        with pytest.raises(u.UpdateError):
            u.validate_manifest(data)


def test_checksum_failure_never_replaces_existing_download(tmp_path):
    u = load()
    source = tmp_path / "image"
    source.write_bytes(b"old")
    assert not u.valid_file(source, 3, "0" * 64)
    assert u.valid_file(source, 3, hashlib.sha256(b"old").hexdigest())
    assert source.read_bytes() == b"old"


def test_compose_preserves_named_volumes_and_no_build(tmp_path):
    import yaml

    u = load()
    settings = {
        "project": "shluz-owner-check",
        "volumes": {
            "gateway": "shluz-owner-check_owner_data",
            "bot": "shluz-owner-check_owner_telegram_data",
            "connector": "shluz-owner-check_owner_telegram_connector",
        },
        "telegram": True,
    }
    text = u.compose_text(manifest(), tmp_path, settings)
    data = yaml.safe_load(text)
    assert data["services"]["shluz"]["image"] == manifest()["image"]
    assert "build" not in data["services"]["shluz"]
    assert data["volumes"]["gateway"]["name"] == settings["volumes"]["gateway"]
    assert data["services"]["telegram_bot"]["depends_on"]["shluz"]["condition"] == "service_healthy"
    assert (
        data["services"]["telegram_bot"]["environment"]["TELEGRAM_GATEWAY_URL"]
        == "http://shluz:8000"
    )
    assert data["services"]["shluz"]["pull_policy"] == "never"


def test_secret_import_is_idempotent_and_does_not_overwrite(tmp_path):
    u = load()
    src = tmp_path / "old"
    dest = tmp_path / "new"
    src.mkdir()
    dest.mkdir()
    (src / ".env.owner").write_text("SECRET=old")
    (dest / ".env.owner").write_text("SECRET=current")
    u.import_local_files(src, dest)
    assert (dest / ".env.owner").read_text() == "SECRET=current"
    (src / ".env.telegram").write_text("TELEGRAM_BOT_TOKEN=not-for-logs")
    u.import_local_files(src, dest)
    assert (dest / ".env.telegram").read_text() == "TELEGRAM_BOT_TOKEN=not-for-logs"


def test_state_atomic_lock_and_schema_mismatch(tmp_path):
    u = load()
    with u.update_lock(tmp_path), pytest.raises(u.UpdateError), u.update_lock(tmp_path):
        pass
    with u.update_lock(tmp_path):
        u.write_json(tmp_path / "state.json", {"commit": "a" * 40})
    assert json.loads((tmp_path / "state.json").read_text())["commit"] == "a" * 40
    data = manifest()
    data["schema"] = 999
    with pytest.raises(u.UpdateError):
        u.validate_manifest(data)


def test_backup_before_mutation_and_failure_does_not_mark_installed(tmp_path):
    u = load()
    events = []
    u.perform_update(
        manifest(),
        tmp_path,
        {},
        prepare=lambda: events.append("download"),
        backup=lambda: events.append("backup"),
        start=lambda: events.append("start"),
    )
    assert events == ["download", "backup", "start"]
    before = (tmp_path / "installed.json").read_text()

    def fail():
        raise u.UpdateError("startup failed")

    with pytest.raises(u.UpdateError):
        u.perform_update(
            manifest(), tmp_path, {}, prepare=lambda: None, backup=lambda: None, start=fail
        )
    assert (tmp_path / "installed.json").read_text() == before


def test_manifest_rejects_boolean_schema_and_oversize():
    u = load()
    for key, value in [
        ("schema", True),
        ("image_size", 2 * 1024**3),
        ("platform", "windows/amd64"),
    ]:
        data = manifest()
        data[key] = value
        with pytest.raises(u.UpdateError):
            u.validate_manifest(data)


def test_failed_backup_does_not_start_or_mark_installed(tmp_path):
    u = load()
    called = []

    def fail():
        raise u.UpdateError("backup failed")

    with pytest.raises(u.UpdateError):
        u.perform_update(
            manifest(),
            tmp_path,
            {},
            prepare=lambda: called.append("download"),
            backup=fail,
            start=lambda: called.append("start"),
        )
    assert called == ["download"]
    assert not (tmp_path / "installed.json").exists()


def test_automatic_settings_discovery_preserves_secret_and_volume(tmp_path):
    u = load()
    old = tmp_path / "old project"
    old.mkdir()
    (old / ".env.owner").write_text("SHLUZ_ADMIN_PASSWORD=only-local")
    root = tmp_path / "managed"
    root.mkdir()
    container = {
        "Config": {
            "Env": ["SHLUZ_DATABASE_URL=sqlite+pysqlite:////app/data/shluz.db"],
            "Labels": {"com.docker.compose.project.working_dir": str(old)},
        },
        "Mounts": [{"Type": "volume", "Destination": "/app/data", "Name": "existing_database"}],
    }
    settings = u.prepare_settings(root, container, None)
    assert settings["volumes"]["gateway"] == "existing_database"
    assert (root / "config" / ".env.owner").read_text() == "SHLUZ_ADMIN_PASSWORD=only-local"
    assert not settings["telegram"]
