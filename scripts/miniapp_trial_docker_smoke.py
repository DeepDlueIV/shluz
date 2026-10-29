"""Real Docker snapshot/rollback + isolated HTTP listener. No external API or credentials."""

import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "launcher"))
import miniapp_trial as trial  # noqa: E402


def run(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def main():
    image = sys.argv[1]
    suffix = uuid.uuid4().hex[:8]
    source, target = "trial-source-" + suffix, "trial-copy-" + suffix
    server, network = "trial-http-" + suffix, "trial-net-" + suffix
    try:
        run("volume", "create", source)
        run("volume", "create", target)
        run("network", "create", "--internal", network)
        setup = """import sqlite3
from pathlib import Path
p=Path('/data/telegram.sqlite')
with sqlite3.connect(p) as db:
 db.execute('CREATE TABLE sample(value TEXT)')
 db.execute("INSERT INTO sample VALUES('preserved')")
 db.execute('CREATE TABLE ui_panels(message_id INTEGER)')
 db.execute('INSERT INTO ui_panels VALUES(123)')
"""
        run(
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0",
            "--entrypoint",
            "python",
            "--mount",
            f"type=volume,source={source},target=/data",
            image,
            "-c",
            setup,
        )
        run(
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0",
            "--entrypoint",
            "python",
            "--mount",
            f"type=volume,source={source},target=/source,readonly",
            "--mount",
            f"type=volume,source={target},target=/target",
            image,
            "-c",
            trial.CLONE_CODE,
        )
        check = """import sqlite3
from pathlib import Path
assert Path('/target/.clone-complete').exists()
with sqlite3.connect('/target/telegram.sqlite') as db:
 assert db.execute('SELECT value FROM sample').fetchone()==('preserved',)
 assert db.execute('SELECT COUNT(*) FROM ui_panels').fetchone()==(0,)
 db.execute("UPDATE sample SET value='test only'")
"""
        run(
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "python",
            "--mount",
            f"type=volume,source={target},target=/target",
            image,
            "-c",
            check,
        )
        run(
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "python",
            "--mount",
            f"type=volume,source={source},target=/data,readonly",
            image,
            "-c",
            "import sqlite3; c=sqlite3.connect('file:/data/telegram.sqlite?mode=ro',uri=True);"
            "assert c.execute('SELECT value FROM sample').fetchone()==('preserved',);",
        )
        http_server = """import uvicorn
from pathlib import Path
from telegram_bot.config import AccessRegistry, BotSettings
from telegram_bot.controller import Controller
from telegram_bot.storage import Store
from telegram_bot.gateway import GatewayClient
from telegram_bot.miniapp.server import create_miniapp
s=BotSettings(interface='miniapp',miniapp_url='https://trial.example/miniapp',trial_owner_id=100)
c=Controller(s,AccessRegistry(Path('/tmp/no-file'),optional=True),
             Store(Path('/tmp/trial.sqlite')),GatewayClient('http://127.0.0.1:9'))
uvicorn.run(create_miniapp(c),host='0.0.0.0',port=8081,access_log=False)
"""
        run(
            "run",
            "-d",
            "--name",
            server,
            "--network",
            network,
            "--entrypoint",
            "python",
            image,
            "-c",
            http_server,
        )
        probes = """import urllib.request,urllib.error
base='http://127.0.0.1:8081'
assert urllib.request.urlopen(base+'/miniapp/health').status==200
for path,expected in [('/admin',404),('/v1/models',404),('/docs',404),('/miniapp/api/state',401)]:
 try: urllib.request.urlopen(base+path); raise AssertionError(path)
 except urllib.error.HTTPError as e: assert e.code==expected,(path,e.code)
css=urllib.request.urlopen(base+'/miniapp/assets/app.css').read()
assert b'--bg:#0c0d0f' in css and b'--accent:#f4f4f4' in css
"""
        for _ in range(30):
            result = subprocess.run(
                ["docker", "exec", server, "python", "-c", probes], capture_output=True, text=True
            )
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Mini App HTTP container did not pass probes")
        ports = json.loads(run("inspect", server))[0]["HostConfig"]["PortBindings"]
        assert not ports, ports
        print("PASS: Docker private history clone; old data preserved; stale UI IDs cleared;")
        print("HTTP ready, no admin/model API, authentication required, no published host ports.")
    finally:
        subprocess.run(["docker", "rm", "-f", server], capture_output=True)
        subprocess.run(["docker", "volume", "rm", source, target], capture_output=True)
        subprocess.run(["docker", "network", "rm", network], capture_output=True)


if __name__ == "__main__":
    main()
