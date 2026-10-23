"""NoClip: постоянная Mini App вместо устаревшего classic polling.

Запуск в один клик, прежний bot/container/volumes остаются для rollback.
Mini App использует ТУ ЖЕ историю и текущие права шлюза; не owner-only trial.
Реальный Telegram/Venice и данные пользователя никогда не используются в CI.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import webbrowser
from datetime import UTC, datetime
from pathlib import Path

import miniapp_trial as trial
import updater

PREVIEW_COMMIT = "c47ff5b8acc4a7b24b8f34b19e3a9d024aba0fea"
MINIAPP_COMMIT = "ceac07cc88401dd99315a368a52d1c24f4f5d697"
LIVE_LABEL = "io.noclip.permanent-miniapp"
LIVE_BOT = "noclip-miniapp"
LIVE_TUNNEL = "noclip-miniapp-https"
LIVE_NETWORK = "noclip-miniapp-https-net"


class LiveError(RuntimeError):
    pass


def docker(*args, **kwargs):
    return updater.docker(*args, **kwargs)


def home():
    return updater.home_root()


def state_file(root):
    return Path(root) / "live-miniapp" / "state.json"


def read_state(root):
    file = state_file(root)
    return json.loads(file.read_text(encoding="utf-8")) if file.exists() else None


def save_state(root, value):
    updater.write_json(state_file(root), value)


def inspect(name):
    raw = docker("inspect", name, optional=True)
    return json.loads(raw)[0] if raw else None


def owned(name):
    item = inspect(name)
    if item and (item["Config"].get("Labels") or {}).get(LIVE_LABEL) != "1":
        raise LiveError("Имя контейнера занято другой системой; ничего не изменено.")
    return item


def is_running(item):
    return bool(item and item.get("State", {}).get("Running"))


def gateway_and_old():
    gateway = updater.inspect_container("shluz")
    old = updater.inspect_container("telegram_bot")
    if not gateway or not old:
        raise LiveError(
            "Не найдены существующие Gateway и Telegram. "
            "Сначала нужна штатная установка SHLUZ, не пустая база."
        )
    if gateway["Id"] == old["Id"]:
        raise LiveError("Некорректные идентификаторы Docker.")
    return gateway, old


def current_settings(root, gateway, old):
    root = Path(root)
    state = root / "runtime.json"
    if state.exists():
        settings = json.loads(state.read_text(encoding="utf-8"))
    else:
        # Никаких больших архивов и обязательного копирования AppData.
        # Реквизиты и имена volumes извлекаются из РЕАЛЬНЫХ контейнеров.
        settings = updater.prepare_settings(root, gateway, old)
    if settings.get("project") != updater.PROJECT:
        raise LiveError("Настройки принадлежат другому Docker-проекту.")
    bot_volume = trial.source_volume(old)
    mounts = [m for m in gateway.get("Mounts", []) if m.get("Destination") == "/app/data"]
    if len(mounts) != 1 or mounts[0].get("Type") != "volume":
        raise LiveError("Нестандартный том базы шлюза; автоматический запуск запрещён.")
    volumes = settings.get("volumes", {})
    if volumes.get("bot") != bot_volume or volumes.get("gateway") != mounts[0]["Name"]:
        raise LiveError("Docker volumes не совпадают с сохранёнными настройками.")
    for name in (volumes.get("gateway"), volumes.get("bot"), volumes.get("connector")):
        if not name or not docker("volume", "inspect", name, optional=True):
            raise LiveError("Ожидаемый Docker volume отсутствует. Новый не создаю.")
    if not (root / "config" / ".env.owner").is_file():
        raise LiveError("Нет настроек Gateway. Сначала проверьте старую установку.")
    return settings


def manifest_from_release(url, validate, revision):
    try:
        with updater.response(url) as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise LiveError("Слишком большой manifest Docker-образа.")
        data = validate(json.loads(raw))
        if data["commit"] != revision:
            raise LiveError("Ревизия проверенной сборки изменилась.")
        return data
    except (OSError, ValueError, KeyError) as exc:
        raise LiveError("Не удалось проверить закреплённый GitHub Release.") from exc


def prepare_images(root):
    preview = manifest_from_release(
        updater.RELEASE_ROOT + "preview-" + PREVIEW_COMMIT + "/manifest.json",
        updater.validate_manifest,
        PREVIEW_COMMIT,
    )
    panel = manifest_from_release(
        updater.RELEASE_ROOT + "miniapp-test-" + MINIAPP_COMMIT + "/manifest.json",
        trial.validate_manifest,
        MINIAPP_COMMIT,
    )
    # Preview уже содержит ТОЧНО ТЕ ЖЕ байты Mini App, но более свежий Gateway.
    # Проверено через сравнение 8 Git blob SHA; не загружаем устаревший image trial.
    archive = Path(root) / "versions" / PREVIEW_COMMIT / "image.tar.gz"
    archive.parent.mkdir(parents=True, exist_ok=True)
    updater.download_image(preview, archive)
    preview["local_image_id"] = updater.prepare_image(preview, archive)
    docker("pull", panel["cloudflared_image"], timeout=300)
    return preview, panel["cloudflared_image"]


def environment_for(old, url):
    if not re.fullmatch(r"https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com", url):
        raise LiveError("Неподдерживаемый HTTPS-адрес Mini App.")
    values = {k: v for k, v in trial.env(old).items() if k.startswith("TELEGRAM_")}
    if not values.get("TELEGRAM_BOT_TOKEN"):
        raise LiveError("Нет токена существующего Telegram-бота.")
    if any("\r" in v or "\n" in v for v in values.values()):
        raise LiveError("Нестандартные переносы строк в настройках Telegram.")
    values.update(
        TELEGRAM_INTERFACE="miniapp",
        TELEGRAM_TRIAL_OWNER_ID="0",  # Полноценный режим для всех разрешённых пользователей.
        TELEGRAM_MINIAPP_URL=url + "/miniapp",
        TELEGRAM_MINIAPP_HOST="0.0.0.0",
        TELEGRAM_MINIAPP_PORT="8081",
        TELEGRAM_GATEWAY_URL="http://shluz:8000",
    )
    return values


def secure_env(root, values):
    target = Path(root) / "live-miniapp" / "live.env"
    target.parent.mkdir(parents=True, exist_ok=True)
    if any(not k.startswith("TELEGRAM_") for k in values):
        raise LiveError("Недопустимый ключ среды Telegram.")
    # docker --env-file НЕ снимает кавычки dotenv: пишем строго KEY=VALUE.
    content = "\n".join(k + "=" + v for k, v in values.items()) + "\n"
    temporary = target.with_name(".live.env.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(target)
    target.chmod(0o600)
    if os.name == "nt":
        # Windows chmod сам по себе не изменяет inherited ACL на D:.
        user = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        import csv
        import io
        try:
            sid = next(csv.reader(io.StringIO(user.stdout)))[1]
        except (ValueError, IndexError, StopIteration) as exc:
            raise LiveError("Не удалось получить Windows SID для защиты ключей.") from exc
        # Убираем inherited ACL с конкретного secret-файла, разрешаем owner+SYSTEM.
        for command in (
            ["icacls", str(target), "/inheritance:r"],
            ["icacls", str(target), "/grant:r", "*"+sid+":(F)", "*S-1-5-18:(F)"],
        ):
            result = subprocess.run(command, capture_output=True, timeout=15, check=False)
            if result.returncode:
                raise LiveError("Не удалось ограничить доступ к секретам Windows.")
    return target


def safe_candidate_args(old, manifest, settings, config):
    candidate = {
        "bot_name": LIVE_BOT,
        "volume": settings["volumes"]["bot"],
    }
    args = trial.candidate_args(old, manifest, candidate, config)
    try:
        pos = args.index(trial.LABEL + "=1")
    except ValueError as exc:
        raise LiveError("Не совпала схема безопасного запуска Telegram.") from exc
    args[pos] = LIVE_LABEL + "=1"
    return args


def ensure_preflight(old):
    token = trial.env(old).get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise LiveError("Нет существующего Telegram Bot Token.")
    if trial.telegram(token, "getWebhookInfo").get("url"):
        raise LiveError("У бота webhook. Запуск второго polling запрещён.")
    me = trial.telegram(token, "getMe")
    if not me.get("id"):
        raise LiveError("Telegram не подтвердил исходного бота.")
    old_menu = trial.telegram(token, "getChatMenuButton")
    return token, old_menu, me.get("username", "")


def start_gateway(gateway):
    if not is_running(gateway):
        docker("start", gateway["Id"])
    for _ in range(120):
        item = inspect(gateway["Id"])
        if not is_running(item):
            raise LiveError("Gateway завершился. Telegram не переключаю.")
        status = item["State"].get("Health", {}).get("Status")
        if status == "healthy":
            return
        if status == "unhealthy":
            raise LiveError("Gateway unhealthy. Telegram не переключаю.")
        time.sleep(1)
    raise LiveError("Gateway не стал healthy. Telegram не переключаю.")


def stop_owned():
    for name in (LIVE_BOT, LIVE_TUNNEL):
        item = owned(name)
        if item:
            if is_running(item):
                docker("stop", "--time", "25", name, timeout=60)
            docker("rm", name)
    raw = docker("network", "inspect", LIVE_NETWORK, optional=True)
    if raw:
        network = json.loads(raw)[0]
        if (network.get("Labels") or {}).get(LIVE_LABEL) != "1":
            raise LiveError("Имя сети Mini App занято другим проектом.")
        docker("network", "rm", LIVE_NETWORK)


def restore_classic(root, state, *, restart=True):
    # Сначала гарантированно остановить второй polling, потом старый.
    stop_owned()
    old = inspect(state.get("old_id", ""))
    if not old:
        raise LiveError("Исходный Telegram-контейнер не найден. Volumes не удалены.")
    labels = old["Config"].get("Labels") or {}
    if (labels.get("com.docker.compose.project"), labels.get("com.docker.compose.service")) != (
        updater.PROJECT, "telegram_bot"
    ):
        raise LiveError("Исходный контейнер не прошёл проверку принадлежности.")
    token = trial.env(old).get("TELEGRAM_BOT_TOKEN", "")
    try:
        trial.telegram(token, "setChatMenuButton", menu_button=state.get("old_menu") or {"type": "commands"})
    except trial.TrialError:
        # Работающий бот важнее временно недоступного Telegram API.
        print("Telegram menu будет восстановлено при появлении сети.")
    if restart and state.get("was_running") and not is_running(old):
        docker("start", old["Id"])
    if not restart and is_running(old):
        docker("stop", "--time", "25", old["Id"], timeout=60)
    state_file(root).unlink(missing_ok=True)


def stop(root, *, rollback=False):
    state = read_state(root)
    if not state:
        print("Новая Mini App ещё не установлена.")
        return
    if rollback:
        restore_classic(root, state)
        print("Классический бот восстановлен. Данные сохранены.")
        return
    for name in (LIVE_BOT, LIVE_TUNNEL):
        item = owned(name)
        if is_running(item):
            docker("stop", "--time", "25", name, timeout=60)
    gateway = updater.inspect_container("shluz")
    if gateway and is_running(gateway):
        docker("stop", "--time", "25", gateway["Id"], timeout=60)
    old = inspect(state.get("old_id", ""))
    if old and is_running(old):
        docker("stop", "--time", "25", old["Id"], timeout=60)
    # Не возвращаем прежний бот автоматически: новая версия остаётся основной.
    state["phase"] = "stopped"
    save_state(root, state)
    print("SHLUZ и новая Mini App остановлены. Все Docker volumes сохранены.")


def backup(root):
    state = read_state(root)
    gateway, old = gateway_and_old()
    settings = current_settings(root, gateway, old)
    live = owned(LIVE_BOT)
    if state and state.get("phase") == "active" and not live:
        raise LiveError("Нет ожидаемого live-контейнера. Копирование остановлено.")
    running = [c for c in (live, old, gateway) if is_running(c)]
    helper_image = gateway["Image"]
    error = None
    destination = None
    try:
        destination = updater.backup_volumes(root, settings, helper_image, running)
        required = {"gateway", "bot", "connector"}
        if any(not (destination / k / "data.tar.gz").is_file() for k in required):
            raise LiveError("Неполный backup. Данные не изменены.")
    except Exception as exc:
        error = exc
    finally:
        # Работавшие контейнеры запускать в правильном порядке.
        for item in [gateway, live, old]:
            if item and any(c["Id"] == item["Id"] for c in running):
                actual = inspect(item["Id"])
                if actual and not is_running(actual):
                    docker("start", item["Id"])
    if error:
        raise LiveError("Не удалось закончить backup; исходные данные не удалены.") from error
    print("Backup проверен:", destination)
    return destination


def start(root):
    docker_platform = docker("info", "--format", "{{.OSType}}/{{.Architecture}}").strip()
    if docker_platform not in {"linux/amd64", "linux/x86_64"}:
        raise LiveError("Нужен Docker Desktop Linux/x64.")
    old_trial_root = trial.root_folder()
    legacy_trial_root = Path(
        os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share"))
    ) / "Shluz" / "miniapp-trial"
    for candidate in (old_trial_root, legacy_trial_root):
        if (candidate / "active.json").exists():
            with updater.update_lock(candidate):
                trial.cleanup(candidate)  # Восстановить старый polling без удаления history.
    leftover = trial.inspect("noclip-miniapp-trial-bot")
    if leftover and is_running(leftover):
        raise LiveError("Старая Mini App всё ещё работает; второй polling запрещён.")
    state = read_state(root)
    if state and state.get("phase") == "active":
        live, tunnel = owned(LIVE_BOT), owned(LIVE_TUNNEL)
        if (
            is_running(live) and is_running(tunnel)
            and live["State"].get("Health", {}).get("Status") == "healthy"
        ):
            gateway, old = gateway_and_old()
            if is_running(gateway) and not is_running(old):
                print("NoClip уже запущен: Gateway + новая Telegram Mini App.")
                return
    gateway, old = gateway_and_old()
    if state:
        restore_classic(root, state, restart=False)
        gateway, old = gateway_and_old()
    settings = current_settings(root, gateway, old)
    image_manifest, cloud_image = prepare_images(root)
    token, previous_menu, username = ensure_preflight(old)
    start_gateway(gateway)
    backup(root)  # До единого переключения всегда сохраняем SQLite и настройки.
    # Никаких удалений старых контейнеров и томов. Только свой HTTPS и bot.
    if owned(LIVE_BOT) or owned(LIVE_TUNNEL):
        raise LiveError("Остались live-контейнеры без state; нужна безопасная диагностика.")
    if docker("network", "inspect", LIVE_NETWORK, optional=True):
        raise LiveError("Сеть Mini App занята. Не запускаю два polling-процесса.")
    initial = {
        "schema": 1,
        "phase": "preparing",
        "old_id": old["Id"],
        "was_running": is_running(old),
        "old_menu": previous_menu,
        "image": image_manifest["commit"],
        "created_at": datetime.now(UTC).isoformat(),
    }
    save_state(root, initial)  # ДО первой внешней модификации, для crash recovery.
    try:
        docker("network", "create", "--label", LIVE_LABEL + "=1", LIVE_NETWORK)
        docker(
            "run", "-d", "--name", LIVE_TUNNEL, "--label", LIVE_LABEL + "=1",
            "--restart=no", "--network", LIVE_NETWORK, "--cap-drop=ALL",
            "--security-opt", "no-new-privileges", cloud_image,
            "tunnel", "--no-autoupdate", "--protocol", "http2",
            "--url", "http://miniapp:8081",
        )
        url = trial.wait_tunnel(LIVE_TUNNEL)
        config = secure_env(root, environment_for(old, url))
        args = safe_candidate_args(old, image_manifest, settings, config)
        if is_running(old):
            docker("stop", "--time", "25", old["Id"], timeout=60)
        docker(*args)
        docker("network", "connect", "--alias", "miniapp", LIVE_NETWORK, LIVE_BOT)
        docker("start", LIVE_BOT)
        trial.wait_ready(LIVE_BOT, url)
        # Снять оставшийся personal MenuButtonCommands от owner-only теста.
        owner_file = trial.root_folder() / "owner.json"
        if not owner_file.is_file():
            owner_file = legacy_trial_root / "owner.json"
        if owner_file.is_file():
            owner_id = json.loads(owner_file.read_text(encoding="utf-8")).get("telegram_id")
            if type(owner_id) is int and 0 < owner_id < 2**52:
                trial.telegram(
                    token, "setChatMenuButton",
                    chat_id=owner_id, menu_button={"type": "default"},
                )
        initial["phase"] = "active"
        initial["origin"] = url  # HTTPS адрес не секрет; необходим для диагностики.
        save_state(root, initial)
        print("Готово: Gateway + новый Telegram с Mini App.")
        print("Откройте Telegram и кнопку «Меню». Старый бот сохранён для отката.")
        if re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
            webbrowser.open("https://t.me/" + username)
    except Exception:
        try:
            restore_classic(root, initial)
        except Exception:
            # Не скрывать исходную ошибку; state оставляем для следующего восстановления.
            pass
        raise


def status(root):
    state = read_state(root)
    print("NoClip (portable):", root)
    gateway = updater.inspect_container("shluz")
    old = updater.inspect_container("telegram_bot")
    print("Gateway:", "работает" if is_running(gateway) else "остановлен")
    print("Новый Telegram:", "работает" if is_running(owned(LIVE_BOT)) else "остановлен")
    print("Mini App HTTPS:", "работает" if is_running(owned(LIVE_TUNNEL)) else "остановлен")
    print("Старый бот (резерв):", "работает" if is_running(old) else "остановлен")
    print("Состояние:", state.get("phase") if state else "не переключён")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    root = home()
    root.mkdir(parents=True, exist_ok=True)
    args = list(sys.argv[1:] if argv is None else argv)
    operation = args[0] if args else "--start"
    try:
        with updater.update_lock(root):
            if operation in {"--start", "start"}:
                start(root)
            elif operation in {"--stop", "stop"}:
                stop(root)
            elif operation in {"--rollback", "rollback"}:
                stop(root, rollback=True)
            elif operation in {"--backup", "backup"}:
                backup(root)
            elif operation in {"--status", "status"}:
                status(root)
            else:
                raise LiveError("Доступные команды: start, stop, status, backup, rollback.")
        return 0
    except (LiveError, trial.TrialError, updater.UpdateError) as exc:
        print("SHLUZ:", str(exc))
        print("Старые Docker volumes и база не удалялись.")
        return 1
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print("SHLUZ: безопасный запуск не завершён:", type(exc).__name__)
        print("Повторите запуск после проверки Docker Desktop. Данные не удалялись.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
