"""Бэкап остановленной WAL-базы не должен писать в исходный read-only том."""

import importlib.util
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest


def updater():
    spec = importlib.util.spec_from_file_location(
        "wal_updater", Path(__file__).parents[2] / "launcher" / "updater.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(os.name == "nt", reason="Read-only POSIX reproduction; Docker runs in Linux")
def test_readonly_wal_without_shm_is_preserved():
    u = updater()
    with tempfile.TemporaryDirectory(prefix="shluz-wal-") as folder:
        root = Path(folder)
        root.chmod(0o755)
        source = root / "volume"
        source.mkdir()
        dbpath = source / "telegram.sqlite"
        code = (
            "import sqlite3,os; "
            f"db=sqlite3.connect({str(dbpath)!r}); "
            "db.execute('PRAGMA journal_mode=WAL'); "
            "db.execute('CREATE TABLE sample(value TEXT)'); "
            "db.execute(\"INSERT INTO sample VALUES('WAL-must-survive')\"); "
            "db.commit(); os._exit(0)"
        )
        subprocess.run([sys.executable, "-c", code], check=True)
        (source / "telegram.sqlite-shm").unlink(missing_ok=True)
        before = {p.name: p.read_bytes() for p in source.iterdir()}
        assert "telegram.sqlite-wal" in before
        for path in source.iterdir():
            path.chmod(0o444)
        source.chmod(0o555)
        try:
            runner = {"user": 65534, "group": 65534} if os.getuid() == 0 else {}
            code = u.BACKUP_CODE.replace("Path('/source')", f"Path({str(source)!r})")
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                timeout=15,
                **runner,
            )
            assert result.returncode == 0, result.stderr.decode()
            archive = root / "data.tar.gz"
            archive.write_bytes(result.stdout)
            with tarfile.open(archive, "r:gz") as saved:
                saved.extractall(root / "restored", filter="data")
            with sqlite3.connect(root / "restored/data/telegram.sqlite") as db:
                assert db.execute("select value from sample").fetchone() == ("WAL-must-survive",)
                assert db.execute("PRAGMA quick_check").fetchone() == ("ok",)
            assert before == {p.name: p.read_bytes() for p in source.iterdir()}
        finally:
            source.chmod(0o755)
            for path in source.iterdir():
                path.chmod(0o644)
