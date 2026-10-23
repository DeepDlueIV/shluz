"""Временная проверка Mini App: только владелец, отдельная история, обратимый запуск.

Stdlib. Не меняет .env, gateway, тарифы, normal preview или исходную историю.
Cloudflare получает только узкий HTTP listener панели, не Docker socket/админку.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from contextlib import suppress
from pathlib import Path

import updater

REPO = "DeepDlueIV/shluz"
LABEL = "io.noclip.owner-trial"
ROOT_NAME = "miniapp-trial"


class TrialError(RuntimeError):
    pass


def docker(*args, **kwargs):
    return updater.docker(*args, **kwargs)


def validate_manifest(data):
    if not isinstance(data, dict):
        raise TrialError("Некорректная сборка проверки.")
    sha = data.get("commit", "")
    if not (
        re.fullmatch(r"[a-f0-9]{40}", str(sha))
        and data.get("schema") == 2
        and data.get("channel") == "miniapp-test"
        and data.get("platform") == "linux/amd64"
        and data.get("image") == "shluz-preview:" + sha
        and data.get("image_url")
        == f"https://github.com/{REPO}/releases/download/miniapp-test-{sha}/image.tar.gz"
        and type(data.get("image_size")) is int
        and 0 < data["image_size"] < 2 * 1024**3
        and re.fullmatch(r"[a-f0-9]{64}", str(data.get("image_sha256", "")))
        and re.fullmatch(r"sha256:[a-f0-9]{64}", str(data.get("image_config_digest", "")))
        and re.fullmatch(r"sha256:[a-f0-9]{64}", str(data.get("image_id", "")))
        and re.fullmatch(
            r"cloudflare/cloudflared@sha256:[a-f0-9]{64}",
            str(data.get("cloudflared_image", "")),
        )
    ):
        raise TrialError("Сборка не прошла проверку. Старый бот не изменён.")
    return data


def env(old):
    return updater.env_values(old)


def inspect(identifier):
    raw = docker("inspect", identifier, optional=True)
    return json.loads(raw)[0] if raw else None


def source_volume(old):
    mounts = [m for m in old["Mounts"] if m["Destination"] == "/app/data"]
    path = env(old).get("TELEGRAM_DATABASE_PATH", "/app/data/telegram.sqlite")
    if (
        len(mounts) != 1
        or mounts[0]["Type"] != "volume"
        or not re.fullmatch(r"/app/data/[a-zA-Z0-9_.-]+", path)
    ):
        raise TrialError("Нестандартная папка истории. Проверка остановлена без изменений.")
    return mounts[0]["Name"]


def trial_environment(old, owner, public_url):
    if type(owner) is not int or not 0 < owner < 2**52:
        raise TrialError("Нужен числовой Telegram ID владельца.")
    if not re.fullmatch(r"https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com", public_url):
        raise TrialError("Туннель вернул неподходящий HTTPS-адрес.")
    values = {k: v for k, v in env(old).items() if k.startswith("TELEGRAM_")}
    if any("\n" in v or "\r" in v for v in values.values()):
        raise TrialError("Неподдерживаемый формат локальных настроек.")
    values.update(
        TELEGRAM_INTERFACE="miniapp",
        TELEGRAM_MINIAPP_URL=public_url + "/miniapp",
        TELEGRAM_MINIAPP_HOST="0.0.0.0",
        TELEGRAM_MINIAPP_PORT="8081",
        TELEGRAM_TRIAL_OWNER_ID=str(owner),
    )
    return values


def candidate_args(old, manifest, state, env_file):
    source_volume(old)
    networks = list(old["NetworkSettings"]["Networks"])
    if len(networks) != 1:
        raise TrialError("У прежнего бота нестандартная сеть. Автопереключение остановлено.")
    args = [
        "create",
        "--name",
        state["bot_name"],
        "--label",
        LABEL + "=1",
        "--restart=no",
        "--init",
        "--cap-drop=ALL",
        "--security-opt",
        "no-new-privileges",
        "--network",
        networks[0],
        "--env-file",
        str(env_file),
        "--mount",
        f"type=volume,source={state['volume']},target=/app/data",
        "--health-cmd",
        "python -m telegram_bot.healthcheck",
        "--health-interval=5s",
        "--health-timeout=5s",
        "--health-retries=12",
    ]
    allowed = {
        "/run/shluz-connector",
        "/app/telegram-connector",
        "/run/telegram-legacy",
        "/run/secrets/telegram-access.json",
    }
    for mount in old["Mounts"]:
        target = mount["Destination"]
        if target == "/app/data":
            continue
        if target not in allowed or mount["Type"] not in {"volume", "bind"}:
            raise TrialError("Неизвестное подключение файлов. Старый бот не изменён.")
        source = mount["Name"] if mount["Type"] == "volume" else mount["Source"]
        if "," in source:
            raise TrialError("Неподдерживаемый путь подключения файлов.")
        args += ["--mount", f"type={mount['Type']},source={source},target={target},readonly"]
    return args + [manifest["image"], "python", "-m", "telegram_bot"]


def telegram(token, method, **body):
    if method not in {"getMe", "getWebhookInfo", "getChatMenuButton", "setChatMenuButton"}:
        raise TrialError("Недопустимая операция Telegram.")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            result = json.load(response)
        if not result.get("ok"):
            raise ValueError()
        return result["result"]
    except (OSError, ValueError, KeyError) as exc:
        # Исключение urlopen содержит URL с секретом — никогда не печатаем его.
        raise TrialError("Telegram не отвечает. Проверьте соединение/VPN и повторите.") from exc


def save_state(root, state):
    updater.write_json(Path(root) / "active.json", state)


def cleanup(root):
    root = Path(root)
    state_file = root / "active.json"
    if not state_file.exists():
        return
    state = json.loads(state_file.read_text(encoding="utf-8"))
    # Управляем только контейнерами нашей проверки, не произвольным именем из файла.
    for key in ("tunnel_name", "bot_name"):
        item = inspect(state[key])
        if item:
            if (item["Config"].get("Labels") or {}).get(LABEL) != "1":
                raise TrialError("Имя тестового контейнера занято другой программой.")
            docker("stop", "--time", "25", state[key], timeout=50)
            docker("rm", state[key])
    old = inspect(state["original_id"])
    if old is None:
        raise TrialError("Прежний контейнер перемещён. Не удаляйте тома; нужна проверка Codex.")
    labels = old["Config"].get("Labels") or {}
    if (labels.get("com.docker.compose.project"), labels.get("com.docker.compose.service")) != (
        updater.PROJECT,
        "telegram_bot",
    ):
        raise TrialError("Прежний контейнер не прошёл проверку принадлежности.")
    menu_error = None
    try:
        telegram(
            env(old)["TELEGRAM_BOT_TOKEN"],
            "setChatMenuButton",
            chat_id=state["owner_id"],
            menu_button=state["old_menu"],
        )
    except TrialError as exc:
        menu_error = exc
    # Новый polling уже остановлен: прежний можно безопасно вернуть даже без Telegram-сети.
    if state["was_running"]:
        docker("start", state["original_id"])
    docker("network", "rm", state["network"], optional=True)
    (root / "trial.env").unlink(missing_ok=True)
    if menu_error:
        raise TrialError(
            "Прежний бот восстановлен, но Telegram не принял возврат кнопки. "
            "После восстановления сети откройте Stop-MiniApp.cmd ещё раз."
        ) from menu_error
    updater.write_json(root / "last-test.json", state)
    state_file.unlink()
    (root / "stop-request").unlink(missing_ok=True)


CLONE_CODE = """import os, shutil, sqlite3
from pathlib import Path
source, target = Path('/source'), Path('/target')
marker = target / '.clone-complete'
if not marker.exists():
    shutil.copytree(source, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('*.lock', '*.heartbeat.json', '.clone-complete'))
    for path in target.rglob('*'):
        if path.is_file():
            with path.open('rb') as stream: header = stream.read(16)
            if header == b'SQLite format 3\\0':
                with sqlite3.connect(path) as db:
                    assert db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
                    # Не удалять старые сообщения Telegram по ID из копии навигации.
                    query = "SELECT name FROM sqlite_master WHERE type='table'"
                    tables = {r[0] for r in db.execute(query)}
                    for name in ('ui_panels', 'ui_activity'):
                        if name in tables: db.execute('DELETE FROM ' + name)
    marker.write_text('Private owner-test snapshot\\n')
for path in [target, *target.rglob('*')]:
    os.chown(path, 10001, 10001)
"""


def root_folder():
    root = updater.home_root() / ROOT_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def owner_id(root):
    saved = root / "owner.json"
    bundled = Path(__file__).with_name("owner.json")
    path = saved if saved.exists() else bundled
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8")).get("telegram_id")
    else:
        value = int(input("Ваш числовой Telegram ID (из раздела Пользователи): ").strip())
    if type(value) is not int or not 0 < value < 2**52:
        raise TrialError("Некорректный Telegram ID владельца.")
    updater.write_json(saved, {"telegram_id": value})
    return value


def wait_tunnel(name):
    # docker logs пишет cloudflared в stderr; не показываем сырой вывод.
    for _ in range(90):
        result = subprocess.run(
            ["docker", "logs", "--tail=60", name], capture_output=True, timeout=10, check=False
        )
        logs = (result.stdout + result.stderr).decode("utf-8", "replace")
        match = re.search(r"https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com", logs)
        if match:
            return match.group()
        status = inspect(name)
        if not status or not status["State"]["Running"]:
            break
        time.sleep(1)
    raise TrialError("Не удалось открыть временный HTTPS. Старый бот будет восстановлен.")


def probe_local_panel(name):
    """Проверить localhost HTTP внутри кандидата; наружу не публиковать порт."""
    script = (
        "import json,urllib.request; "
        "r=urllib.request.urlopen('http://127.0.0.1:8081/miniapp/health',timeout=3); "
        "assert json.load(r)=={'status':'ok'}; print('ready')"
    )
    response = docker("exec", name, "python", "-c", script, optional=True, timeout=12)
    return response.strip() == "ready"


def probe_windows_https(url):
    """Проверка с хоста может не пройти из-за отдельного VPN/proxy Windows."""
    try:
        with urllib.request.urlopen(url + "/miniapp/health", timeout=5) as response:
            return json.load(response) == {"status": "ok"}
    except (OSError, ValueError, TypeError):
        return False


def probe_container_https(name, url):
    """Независимый TLS/Cloudflare probe из контейнера, без вывода URL или ошибок."""
    # URL уже проверен trial_environment: host trycloudflare.com без query/fragment.
    script = (
        "import json,urllib.request; "
        f"r=urllib.request.urlopen({(url + '/miniapp/health')!r},timeout=5); "
        "assert json.load(r)=={'status':'ok'}; print('ready')"
    )
    response = docker("exec", name, "python", "-c", script, optional=True, timeout=12)
    return response.strip() == "ready"


def wait_ready(name, public_url, *, attempts=90):
    """Нужны: живой polling, локальная панель и доказанный публичный HTTPS.

    Внешняя проверка может быть выполнена внутри контейнера, если Windows/VPN
    блокирует trycloudflare.com локально. При сбое возвращаем категорию причины.
    """
    seen_healthy = False
    seen_local_panel = False
    for _ in range(attempts):
        item = inspect(name)
        if not item or not item["State"]["Running"]:
            raise TrialError("Тестовый бот остановился. Старый бот будет восстановлен.")
        if item["State"].get("Health", {}).get("Status") == "healthy":
            seen_healthy = True
            if probe_local_panel(name):
                seen_local_panel = True
                if probe_windows_https(public_url) or probe_container_https(name, public_url):
                    return
        time.sleep(1)
    if not seen_healthy:
        raise TrialError(
            "Не запустился Telegram-бот в тестовом контейнере (проверка heartbeat). "
            "Это не ошибка HTTPS. Старый бот будет восстановлен."
        )
    if not seen_local_panel:
        raise TrialError(
            "Бот запущен, но его локальная HTTP-панель не ответила. "
            "Старый бот будет восстановлен."
        )
    raise TrialError(
        "Бот и HTTP-панель работают, но HTTPS-туннель Cloudflare недоступен "
        "и из Windows, и из тестового контейнера. Старый бот будет восстановлен."
    )


def run_trial(root):
    # Старый остаток незавершённой сессии сначала безопасно восстановить.
    cleanup(root)
    (root / "stop-request").unlink(missing_ok=True)
    manifest = validate_manifest(
        json.loads(Path(__file__).with_name("trial-manifest.json").read_text(encoding="utf-8"))
    )
    if docker("info", "--format", "{{.OSType}}/{{.Architecture}}").strip() not in {
        "linux/x86_64",
        "linux/amd64",
    }:
        raise TrialError("Откройте Docker Desktop в режиме Linux containers / x64.")
    old = updater.inspect_container("telegram_bot")
    gateway = updater.inspect_container("shluz")
    if old is None or gateway is None or not gateway["State"].get("Running"):
        raise TrialError("Сначала запустите прежний шлюз и бот обычным Shluz.cmd.")
    source = source_volume(old)
    owner = owner_id(root)
    values = env(old)
    token = values.get("TELEGRAM_BOT_TOKEN", "")
    me = telegram(token, "getMe")
    if telegram(token, "getWebhookInfo").get("url"):
        raise TrialError("У бота настроен webhook. Проверка не будет его изменять.")
    previous = telegram(token, "getChatMenuButton", chat_id=owner)
    # Все downloads и проверка конфигурации до остановки рабочего бота.
    state = dict(
        original_id=old["Id"],
        was_running=old["State"].get("Running", False),
        owner_id=owner,
        old_menu=previous,
        bot_name="noclip-miniapp-trial-bot",
        tunnel_name="noclip-miniapp-trial-tunnel",
        network="noclip-miniapp-trial-net",
        volume=f"noclip-miniapp-trial-{me['id']}-{owner}",
        commit=manifest["commit"],
    )
    candidate_args(old, manifest, state, root / "trial.env")
    for name in (state["bot_name"], state["tunnel_name"]):
        if inspect(name):
            raise TrialError(
                "Тестовый контейнер уже существует. Сначала откройте Stop-MiniApp.cmd."
            )
    network = docker("network", "inspect", state["network"], optional=True)
    if network:
        raise TrialError("Тестовая сеть уже занята. Сначала завершите прежнюю проверку.")
    volume = docker("volume", "inspect", state["volume"], optional=True)
    if volume and (json.loads(volume)[0].get("Labels") or {}).get(LABEL) != "1":
        raise TrialError("Имя тестового тома занято. Исходную историю не меняю.")
    print("Загружаю проверенную тестовую сборку. Старый бот пока работает.")
    archive = root / (manifest["commit"] + ".tar.gz")
    updater.download_image(manifest, archive)
    updater.prepare_image(manifest, archive)
    docker("pull", manifest["cloudflared_image"], timeout=300)
    if state["was_running"]:
        # Не обрывать текущую платную генерацию ради смены интерфейса.
        check = (
            "import os,sqlite3; p=os.environ.get('TELEGRAM_DATABASE_PATH',"
            "'/app/data/telegram.sqlite'); c=sqlite3.connect('file:'+p+'?mode=ro',uri=True);"
            "print(c.execute(\"SELECT COUNT(*) FROM turns WHERE status='pending'\").fetchone()[0])"
        )
        if int(docker("exec", old["Id"], "python", "-c", check).strip()):
            raise TrialError(
                "Сначала дождитесь текущего ответа в боте, затем откройте Start ещё раз."
            )
    print(f"Закрытая проверка для Telegram ID {owner}. Старую историю не меняю.")
    save_state(root, state)  # До первого внешнего изменения, для crash recovery.
    try:
        if state["was_running"]:
            docker("stop", "--time", "25", old["Id"], timeout=50)
        docker("volume", "create", "--label", LABEL + "=1", state["volume"])
        docker(
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
            f"type=volume,source={state['volume']},target=/target",
            manifest["image"],
            "-c",
            CLONE_CODE,
            timeout=180,
        )
        docker("network", "create", "--label", LABEL + "=1", state["network"])
        docker(
            "run",
            "-d",
            "--name",
            state["tunnel_name"],
            "--label",
            LABEL + "=1",
            "--restart=no",
            "--network",
            state["network"],
            "--cap-drop=ALL",
            "--security-opt",
            "no-new-privileges",
            manifest["cloudflared_image"],
            "tunnel",
            "--no-autoupdate",
            "--protocol",
            "http2",
            "--url",
            "http://miniapp:8081",
        )
        url = wait_tunnel(state["tunnel_name"])
        trial = trial_environment(old, owner, url)
        # docker --env-file не является dotenv: значения пишутся без кавычек/интерполяции.
        secret_file = root / "trial.env"
        secret_file.write_text(
            "\n".join(k + "=" + v for k, v in trial.items()) + "\n", encoding="utf-8"
        )
        secret_file.chmod(0o600)
        docker(*candidate_args(old, manifest, state, secret_file))
        docker("network", "connect", "--alias", "miniapp", state["network"], state["bot_name"])
        docker("start", state["bot_name"])
        wait_ready(state["bot_name"], url)
        print("\nГотово. В Telegram откройте вашего бота и кнопку МЕНЮ у строки ввода.")
        print("Меню и настройки бесплатны. Отправленный вами промпт использует прежний тариф.")
        print("Проверка работает, пока открыто это окно. ENTER — закончить и вернуть старого бота.")
        print("После аварийного закрытия окна используйте Stop-MiniApp.cmd.")
        if re.fullmatch(r"[A-Za-z0-9_]{5,32}", me.get("username", "")):
            webbrowser.open("https://t.me/" + me["username"])
        stop = threading.Event()

        def enter():
            with suppress(EOFError):
                input()
            stop.set()

        threading.Thread(target=enter, daemon=True).start()
        while not stop.wait(1):
            if (root / "stop-request").exists():
                break
            item = inspect(state["bot_name"])
            if not item or not item["State"]["Running"]:
                raise TrialError("Тестовый бот остановился. Возвращаю прежнюю версию.")
    finally:
        cleanup(root)
    print("Прежний бот и меню восстановлены. Тестовая копия истории сохранена отдельно.")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    root = root_folder()
    try:
        if "--stop" in sys.argv:
            (root / "stop-request").touch()
            try:
                with updater.update_lock(root):
                    cleanup(root)
                print("Проверка остановлена; прежний бот восстановлен.")
            except updater.UpdateError:
                print("Команда остановки передана окну проверки. Оно вернёт прежнего бота.")
            return 0
        with updater.update_lock(root):
            run_trial(root)
        return 0
    except KeyboardInterrupt:
        print("Проверка завершена. Для восстановления после сбоя используйте Stop-MiniApp.cmd.")
        return 1
    except (
        TrialError,
        updater.UpdateError,
        OSError,
        ValueError,
        KeyError,
        subprocess.SubprocessError,
    ) as exc:
        text = (
            str(exc)
            if isinstance(exc, (TrialError, updater.UpdateError))
            else ("Не удалось закончить проверку. Старые файлы не перезаписывались.")
        )
        print("\n" + text)
        print("При незавершённом переключении: Stop-MiniApp.cmd. Не удаляйте Docker Volumes.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
