"""Real Docker smoke for permanent Mini App volume preservation. CI runner ONLY.

No live Telegram, no Venice, no user data. Ephemeral runner owns all resources.
"""

import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Linux":
    raise RuntimeError("Only an isolated Linux GitHub Actions runner is permitted")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "launcher"))
import miniapp_live as live  # noqa: E402
import updater  # noqa: E402

IMAGE = "noclip-portable-smoke:local"
PROJECT = updater.PROJECT


def docker(*args, ok=True):
    result = subprocess.run(["docker", *args], text=True, capture_output=True, timeout=180)
    if ok and result.returncode:
        # Do not print raw Docker output (may include private env).
        raise RuntimeError("CI Docker stage failed: " + args[0])
    return result.stdout.strip()


def healthy(id_):
    return json.loads(docker("inspect", id_))[0]["State"]


def main():
    if updater.inspect_container("shluz") or updater.inspect_container("telegram_bot"):
        raise RuntimeError("CI runner has unexpected owner containers")
    network = PROJECT + "_default"
    gateway_name = "noclip-smoke-gateway"
    bot_name = "noclip-smoke-classic"
    image = IMAGE
    volumes = {
        "gateway": PROJECT + "_owner_data",
        "bot": PROJECT + "_owner_telegram_data",
        "connector": PROJECT + "_owner_telegram_connector",
    }
    with tempfile.TemporaryDirectory(prefix="noclip-portable-ci-") as folder:
        root = Path(folder)
        docker("network", "create", network)
        for v in volumes.values():
            docker("volume", "create", v)
        try:
            docker(
                "run", "-d", "--name", gateway_name,
                "--label", "com.docker.compose.project=" + PROJECT,
                "--label", "com.docker.compose.service=shluz",
                "--network", network, "--network-alias", "shluz",
                "--env", "SHLUZ_ACTIVE_PROVIDER=mock",
                "--env", "SHLUZ_DATABASE_URL=sqlite+pysqlite:////app/data/shluz.db",
                "--env", "SHLUZ_ADMIN_PASSWORD=ci-only",
                "--env", "SHLUZ_BOOTSTRAP_API_TOKEN=ci-only",
                "--env", "SHLUZ_TELEGRAM_CONNECTOR_FILE=/app/telegram-connector/key",
                "--mount", "type=volume,source=" + volumes["gateway"] + ",target=/app/data",
                "--mount", "type=volume,source=" + volumes["connector"] + ",target=/app/telegram-connector",
                image,
            )
            for _ in range(100):
                if healthy(gateway_name).get("Health", {}).get("Status") == "healthy":
                    break
                import time
                time.sleep(1)
            else:
                raise RuntimeError("Gateway did not become healthy in CI")
            docker(
                "run", "-d", "--name", bot_name,
                "--label", "com.docker.compose.project=" + PROJECT,
                "--label", "com.docker.compose.service=telegram_bot",
                "--network", network,
                "--env", "TELEGRAM_BOT_TOKEN=123456:" + "X" * 35,
                "--env", "TELEGRAM_DATABASE_PATH=/app/data/telegram.sqlite",
                "--env", "TELEGRAM_GATEWAY_URL=http://shluz:8000",
                "--env", "TELEGRAM_CONNECTOR_FILE=/run/shluz-connector/key",
                "--mount", "type=volume,source=" + volumes["bot"] + ",target=/app/data",
                "--mount", "type=volume,source=" + volumes["connector"] +
                           ",target=/run/shluz-connector,readonly",
                image, "python", "-c", "import time;time.sleep(3600)",
            )
            gateway, old = live.gateway_and_old()
            state = live.current_settings(root, gateway, old)
            assert state["volumes"] == volumes
            # A real SQLite record persists through old bot stop and backup.
            seed = (
                "import sqlite3; c=sqlite3.connect('/app/data/telegram.sqlite');"
                "c.execute('create table preserved(value text)');"
                "c.execute(\"insert into preserved values('no loss')\"); c.commit()"
            )
            docker("exec", bot_name, "python", "-c", seed)
            snapshot = live.backup(root)
            assert all((snapshot / k / "data.tar.gz").is_file() for k in volumes)
            with tarfile.open(snapshot / "bot" / "data.tar.gz", "r:gz") as archive:
                import sqlite3
                member = archive.extractfile("data/telegram.sqlite")
                assert member
                file = root / "db.copy.sqlite"
                file.write_bytes(member.read())
                with sqlite3.connect(file) as database:
                    assert database.execute("select value from preserved").fetchone() == (
                        "no loss",
                    )
            # Candidate is created, but no real Telegram polling runs in CI.
            mock_manifest = {"image": image}
            env = live.environment_for(old, "https://ci-test.trycloudflare.com")
            env_file = live.secure_env(root, env)
            cmd = live.safe_candidate_args(old, mock_manifest, state, env_file)
            cmd[-3:] = ["python", "-c", "import time;time.sleep(3600)"]
            docker("stop", "--time", "5", bot_name)
            docker(*cmd)
            newer = live.owned(live.LIVE_BOT)
            assert newer
            mounts = {
                m["Destination"]: m.get("Name")
                for m in newer["Mounts"] if m["Type"] == "volume"
            }
            assert mounts["/app/data"] == volumes["bot"]
            assert mounts["/run/shluz-connector"] == volumes["connector"]
            assert newer["Config"]["Env"] and any(
                value == "TELEGRAM_INTERFACE=miniapp" for value in newer["Config"]["Env"]
            )
            assert not any(
                value.startswith("VENICE_API_KEY=") for value in newer["Config"]["Env"]
            )
            docker("start", live.LIVE_BOT)
            assert healthy(live.LIVE_BOT)["Running"]
            live.save_state(root, {
                "phase": "active", "old_id": old["Id"], "was_running": True,
                "old_menu": {"type": "commands"},
            })
            live.stop(root)
            assert not healthy(live.LIVE_BOT)["Running"]
            assert not healthy(bot_name)["Running"]
            assert not healthy(gateway_name)["Running"]
            assert docker("volume", "inspect", volumes["bot"])
            print("PASS: real Docker volume identity, SQLite backup, Mini App env, no parallel polling, stop")
        finally:
            # The entire environment is an ephemeral CI VM; do not delete Docker volumes.
            for name in (live.LIVE_BOT, bot_name, gateway_name):
                docker("stop", "--time", "3", name, ok=False)


if __name__ == "__main__":
    main()
