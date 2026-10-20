"""CI-only Docker upgrade/backup/restart check; never run against the owner's machine."""

import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

BASELINE = "dc0dca68652cf36f3acf3f4a4f6ae6cd049adf2d"
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("updater", ROOT / "launcher/updater.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


def command(*args, stdin=None):
    proc = subprocess.run(args, input=stdin, capture_output=True, text=True, check=False)
    if proc.returncode:
        raise RuntimeError(f"CI command failed: {args[0]}\n{proc.stderr[-3000:]}")
    return proc.stdout


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Linux":
        raise RuntimeError("This destructive test is permitted only on an isolated GitHub runner")
    if u.inspect_container("shluz") or u.inspect_container("telegram_bot"):
        raise RuntimeError("Test must start with an empty owner project")
    sha, image = sys.argv[1:3]
    with tempfile.TemporaryDirectory(prefix="shluz preview ") as directory:
        root = Path(directory)
        old = root / "old"
        old.mkdir()
        archive = subprocess.check_output(["git", "archive", BASELINE])
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(old, filter="data")
        # Unique credentials for this runner; never retrieve or log owner secrets.
        (old / ".env.owner").write_text(
            "SHLUZ_DATABASE_URL=sqlite+pysqlite:////app/data/shluz.db\n"
            "SHLUZ_ADMIN_USERNAME=owner\nSHLUZ_ADMIN_PASSWORD=ci-only-password\n"
            "SHLUZ_BOOTSTRAP_API_TOKEN=ci-only-bootstrap\nSHLUZ_ACTIVE_PROVIDER=mock\n"
        )
        command("docker", "build", "-q", "-t", "shluz-upgrade-baseline", str(old))
        old_compose = root / "old-compose.json"
        # Use original schema/code but an explicit prebuilt test image.
        old_compose.write_text(
            json.dumps(
                {
                    "name": u.PROJECT,
                    "services": {
                        "shluz": {
                            "image": "shluz-upgrade-baseline",
                            "env_file": [str(old / ".env.owner")],
                            "ports": ["127.0.0.1:18000:8000"],
                            "volumes": ["owner_data:/app/data"],
                        }
                    },
                    "volumes": {"owner_data": {}},
                }
            )
        )
        new_compose = None
        try:
            command(
                "docker",
                "compose",
                "-p",
                u.PROJECT,
                "-f",
                str(old_compose),
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "120",
            )
            container = u.inspect_container("shluz")
            # Seed an existing user, tariff, key and two successful API requests.
            seed = """from app.config import Settings
from app.db.database import Database
from app.db.models import Account,Plan,Subscription
from app.db.repositories import create_api_token
db=Database(Settings())
with db.session() as s:
 p=Plan(code='before-update',name='Preserved plan',monthly_request_limit=5)
 a=Account(display_name='Preserved owner')
 s.add_all([p,a]);s.flush()
 s.add(Subscription(account_id=a.id,plan_id=p.id))
 create_api_token(s,account_id=a.id,source='harness',raw_token='shluz_'+ 'z'*43,name='CI')
"""
            command("docker", "exec", "-i", container["Id"], "python", "-", stdin=seed)

            def api(path, body=None):
                request = Request(
                    "http://127.0.0.1:18000" + path,
                    data=json.dumps(body).encode() if body else None,
                    headers={
                        "Authorization": "Bearer shluz_" + "z" * 43,
                        "Content-Type": "application/json",
                    },
                )
                with urlopen(request, timeout=10) as res:
                    return json.load(res)

            for _ in range(2):
                api(
                    "/v1/chat/completions",
                    {
                        "model": "mock-chat",
                        "messages": [{"role": "user", "content": "Preserve this count"}],
                    },
                )
            assert api("/v1/me")["allowance"]["requests"]["used"] == 2
            # Legacy bot DB/connector volumes are backed up even when no live Telegram is used.
            botvol = u.PROJECT + "_owner_telegram_data"
            command("docker", "volume", "create", botvol)
            command(
                "docker",
                "run",
                "--rm",
                "-v",
                botvol + ":/app/data",
                "--entrypoint",
                "python",
                image,
                "-c",
                "import sqlite3,os; db=sqlite3.connect('/app/data/telegram.sqlite');"
                "db.execute('PRAGMA journal_mode=WAL');"
                "db.execute('create table preserved(value text)');"
                "db.execute(\"insert into preserved values('history-before-upgrade')\");"
                "db.commit(); os.remove('/app/data/telegram.sqlite-shm'); os._exit(0)",
            )
            managed = root / "managed installation"
            managed.mkdir()
            settings = u.prepare_settings(managed, container, None)
            assert "ci-only-password" in (managed / "config/.env.owner").read_text()
            assert settings["volumes"]["gateway"] == u.PROJECT + "_owner_data"
            manifest = {
                "commit": sha,
                "image": image,
                "image_id": command(
                    "docker", "image", "inspect", image, "--format", "{{.Id}}"
                ).strip(),
            }
            new_compose = managed / "compose.json"
            new_compose.write_text(u.compose_text(manifest, managed / "config", settings))
            backups = []
            u.perform_update(
                manifest,
                managed,
                settings,
                prepare=lambda: None,
                backup=lambda: backups.append(
                    u.backup_volumes(managed, settings, image, [container])
                ),
                start=lambda: u.docker(
                    "compose",
                    "-p",
                    u.PROJECT,
                    "-f",
                    str(new_compose),
                    "up",
                    "-d",
                    "--no-build",
                    "--pull",
                    "never",
                    "--wait",
                    "--wait-timeout",
                    "120",
                ),
            )
            assert api("/health")["build"] == sha
            assert api("/v1/me")["allowance"]["requests"]["used"] == 2
            assert api("/v1/me")["account"]["display_name"] == "Preserved owner"
            assert api("/v1/me/usage")["summary"]["requests"] == 2
            # Check actual backup contents separately; never restore on top of a live DB.
            import sqlite3

            unpack = root / "restored backup"
            unpack.mkdir()
            with tarfile.open(backups[0] / "gateway/data.tar.gz", "r:gz") as tar:
                tar.extractall(unpack, filter="data")
            db = sqlite3.connect(unpack / "data/shluz.db")
            assert db.execute("pragma quick_check").fetchone()[0] == "ok"
            assert (
                db.execute("select version_num from alembic_version").fetchone()[0] == "20261018_06"
            )
            assert (
                db.execute(
                    "select count(*) from accounts where display_name=?", ("Preserved owner",)
                ).fetchone()[0]
                == 1
            )
            db.close()
            assert (backups[0] / "bot/data.tar.gz").is_file()
            bot_restore = root / "bot restored"
            with tarfile.open(backups[0] / "bot/data.tar.gz", "r:gz") as tar:
                tar.extractall(bot_restore, filter="data")
            with sqlite3.connect(bot_restore / "data/telegram.sqlite") as restored:
                assert restored.execute("select value from preserved").fetchone() == (
                    "history-before-upgrade",
                )
                assert restored.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            current = u.inspect_container("shluz")
            command("docker", "restart", current["Id"])
            command(
                "docker",
                "compose",
                "-p",
                u.PROJECT,
                "-f",
                str(new_compose),
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "120",
            )
            assert api("/v1/me")["allowance"]["requests"]["used"] == 2
            assert json.loads((managed / "installed.json").read_text())["commit"] == sha
            print(
                "PASS: launcher settings discovery, exact image, backup including committed WAL, "
                "schema6→8, "
                "existing user/key/plan/usage, bot DB backup, restart persistence"
            )
        finally:
            for compose in [new_compose, old_compose]:
                if compose and compose.exists():
                    u.docker("compose", "-p", u.PROJECT, "-f", str(compose), "down", optional=True)
            # This whole script is restricted to ephemeral GitHub runners above.
            for suffix in ("owner_data", "owner_telegram_data", "owner_telegram_connector"):
                u.docker("volume", "rm", u.PROJECT + "_" + suffix, optional=True)


if __name__ == "__main__":
    main()
