"""Регрессии резервной копии: хост владеет файлом, данные проверяются до миграции."""

import importlib.util
from pathlib import Path

import pytest


def load():
    path = Path(__file__).parents[2] / "launcher" / "updater.py"
    spec = importlib.util.spec_from_file_location("backup_updater", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backup_archive_is_created_by_host_and_readable(tmp_path, monkeypatch):
    """Архив не должен принадлежать root контейнера: владелец должен восстановить его."""
    import os
    import sqlite3
    import subprocess
    import sys
    import tarfile

    u = load()
    source = tmp_path / "volume"
    source.mkdir()
    with sqlite3.connect(source / "shluz.db") as db:
        db.execute("create table sample(value text)")
        db.execute("insert into sample values('preserved')")
    destination = tmp_path / "backup" / "data.tar.gz"
    destination.parent.mkdir()
    real_run = subprocess.run
    calls = []

    def run_container(args, **kwargs):
        calls.append(args)
        assert args[:3] == ["docker", "run", "--rm"]
        assert "none" in args and "readonly" in " ".join(args)
        assert not any("/destination" in str(arg) for arg in args)
        assert kwargs.get("stdout") is not None
        assert kwargs.get("text", False) is False
        code = args[-1].replace("Path('/source')", f"Path({str(source)!r})")
        return real_run([sys.executable, "-c", code], **kwargs)

    monkeypatch.setattr(u.subprocess, "run", run_container)
    u.archive_volume("existing_owner_data", "image:checked", destination)
    assert len(calls) == 1
    assert destination.is_file()
    assert not destination.with_suffix(".part").exists()
    if os.name != "nt":
        assert destination.stat().st_uid == os.getuid()
        assert destination.stat().st_mode & 0o077 == 0
    restored = tmp_path / "restore"
    with tarfile.open(destination, "r:gz") as archive:
        archive.extractall(restored, filter="data")
    with sqlite3.connect(restored / "data/shluz.db") as db:
        assert db.execute("select value from sample").fetchone() == ("preserved",)


def test_failed_backup_stream_is_removed_and_secret_not_exposed(tmp_path, monkeypatch):
    import subprocess

    u = load()
    destination = tmp_path / "data.tar.gz"

    def failing_run(args, **kwargs):
        kwargs["stdout"].write(b"partial archive")
        return subprocess.CompletedProcess(args, 1, stderr=b"private-token-never-print")

    monkeypatch.setattr(u.subprocess, "run", failing_run)
    with pytest.raises(u.UpdateError) as error:
        u.archive_volume("owner_data", "image:checked", destination)
    assert "private-token" not in str(error.value)
    assert not destination.exists()
    assert not destination.with_suffix(".part").exists()


def test_invalid_backup_stream_is_rejected_before_upgrade(tmp_path, monkeypatch):
    import subprocess

    u = load()
    destination = tmp_path / "data.tar.gz"

    def corrupt_run(args, **kwargs):
        kwargs["stdout"].write(b"not an archive")
        return subprocess.CompletedProcess(args, 0, stderr=b"")

    monkeypatch.setattr(u.subprocess, "run", corrupt_run)
    with pytest.raises(u.UpdateError):
        u.archive_volume("owner_data", "image:checked", destination)
    assert not destination.exists()
    assert not destination.with_suffix(".part").exists()


def test_backup_failure_restarts_previous_processes(tmp_path, monkeypatch):
    u = load()
    (tmp_path / "config").mkdir()
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        return "existing volume" if args[0] == "volume" else ""

    def fail(*args):
        raise u.UpdateError("backup is not readable")

    monkeypatch.setattr(u, "docker", docker)
    monkeypatch.setattr(u, "archive_volume", fail)
    with pytest.raises(u.UpdateError):
        u.backup_volumes(tmp_path, {"volumes": {"gateway": "owner_data"}}, "image:checked",
                         [{"Id": "old-gateway", "State": {"Running": True}}])
    assert calls[0] == ("stop", "--time", "25", "old-gateway")
    assert calls[-1] == ("start", "old-gateway")
