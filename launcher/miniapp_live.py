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
from contextlib import suppress
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
    expected = preview.get("image_config_digest", preview.get("image_id"))
    loaded = updater.inspect_image(preview["image"])
    verified = False
    if loaded and (loaded.get("Os"), loaded.get("Architecture")) == ("linux", "amd64"):
        actual = loaded["Id"]
        if actual != expected:
            actual = updater.exported_config_digest(loaded["Id"])
        verified = actual == expected
    if verified:
        preview["local_image_id"] = loaded["Id"]
    else:
        archive = Path(root) / "versions" / PREVIEW_COMMIT / "image.tar.gz"
        archive.parent.mkdir(parents=True, exist_ok=True)
        updater.download_image(preview, archive)
        preview["local_image_id"] = updater.prepare_image(preview, archive)
    if not updater.inspect_image(panel["cloudflared_image"]):
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
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
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
            ["icacls", str(target), "/grant:r", "*" + sid + ":(F)", "*S-1-5-18:(F)"],
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


MENU_INSIDE_DOCKER = """
import json,os,sys,urllib.request,urllib.error
try:
    operation,expected=sys.argv[1:3]
    token=os.environ.get('TELEGRAM_BOT_TOKEN','')
    if not token or operation not in ('read','set','clear'):
        print('unavailable')
    else:
        def send(method,body):
            req=urllib.request.Request(
                'https://api.telegram.org/bot'+token+'/'+method,
                data=json.dumps(body).encode('utf-8'),
                headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=4) as result:
                response=json.load(result)
            return response.get('result') if response.get('ok') else None
        if operation=='set':
            success=send('setChatMenuButton',{
                'menu_button':{'type':'web_app','text':'Меню',
                               'web_app':{'url':expected}}})
            if success is not True:
                print('api_error')
                sys.exit(0)
        elif operation=='clear':
            success=send('setChatMenuButton',{'menu_button':{'type':'default'}})
            if success is not True:
                print('api_error')
                sys.exit(0)
        menu=send('getChatMenuButton',{})
        if (
            operation == 'clear'
            and isinstance(menu, dict)
            and menu.get('type') in ('default', 'commands')
        ):
            print('cleared')
        elif isinstance(menu,dict) and menu.get('type')=='web_app' and (
            (menu.get('web_app') or {}).get('url')==expected):
            print('match')
        elif isinstance(menu,dict) and menu.get('type') in (
            'web_app','commands','default'):
            print('stale')
        else:
            print('api_error')
except urllib.error.HTTPError as err:
    if err.code in (401,403):
        print('api_auth')
    elif err.code == 429 or err.code >= 500:
        print('unreachable')
    else:
        print('api_error')
except (OSError,ValueError,TypeError,IndexError,KeyError):
    print('unreachable')
"""


def container_menu_probe(url, *, action="read", timeout=7):
    """Проверить меню из контейнера; token не передаётся в Docker CLI."""
    item = owned(LIVE_BOT)
    if not is_running(item):
        return "unavailable"
    expected = url + "/miniapp" if action in {"read", "set"} else ""
    try:
        result = docker(
            "exec",
            item["Id"],
            "python",
            "-c",
            MENU_INSIDE_DOCKER,
            action,
            expected,
            optional=True,
            timeout=timeout,
        ).strip()
    except (OSError, subprocess.TimeoutExpired, updater.UpdateError):
        return "unreachable"
    allowed = {"match", "stale", "cleared", "unavailable", "api_error", "api_auth", "unreachable"}
    return result if result in allowed else "unreachable"


def host_menu_probe(token, url, *, timeout=4):
    try:
        menu = trial.telegram(token, "getChatMenuButton", request_timeout=timeout)
    except trial.TelegramApiError as exc:
        return "rejected" if exc.category == "rejected" else "unreachable"
    except trial.TrialError:
        return "api_error"
    if (
        isinstance(menu, dict)
        and menu.get("type") == "web_app"
        and isinstance(menu.get("web_app"), dict)
        and menu["web_app"].get("url") == url + "/miniapp"
    ):
        return "match"
    if isinstance(menu, dict) and menu.get("type") in ("commands", "default", "web_app"):
        return "stale"
    return "api_error"


def set_host_menu(token, url, *, timeout=4):
    try:
        trial.telegram(
            token,
            "setChatMenuButton",
            request_timeout=timeout,
            menu_button={"type": "web_app", "text": "Меню", "web_app": {"url": url + "/miniapp"}},
        )
    except trial.TelegramApiError as exc:
        return "rejected" if exc.category == "rejected" else "unreachable"
    except trial.TrialError:
        return "api_error"
    return host_menu_probe(token, url, timeout=timeout)


def clear_host_menu_if_matches(token, url, *, timeout=4):
    """Убрать только эту нерабочую managed-ссылку; чужую кнопку не менять."""
    try:
        menu = trial.telegram(token, "getChatMenuButton", request_timeout=timeout)
    except trial.TelegramApiError as exc:
        return "rejected" if exc.category == "rejected" else "unreachable"
    except trial.TrialError:
        return "api_error"
    current = (menu.get("web_app") or {}).get("url") if isinstance(menu, dict) else None
    if not isinstance(current, str) or current != url + "/miniapp":
        return "unchanged"
    try:
        trial.telegram(
            token,
            "setChatMenuButton",
            request_timeout=timeout,
            menu_button={"type": "default"},
        )
        after = trial.telegram(token, "getChatMenuButton", request_timeout=timeout)
    except trial.TelegramApiError as exc:
        return "rejected" if exc.category == "rejected" else "unreachable"
    except trial.TrialError:
        return "api_error"
    return (
        "cleared"
        if isinstance(after, dict) and after.get("type") in ("default", "commands")
        else "stale"
    )


def wait_menu(token, url, *, attempts=8, timeout_seconds=30):
    """Применить точный URL, прочитать Telegram обратно и вернуть безопасный статус."""
    deadline = time.monotonic() + timeout_seconds
    repair_attempted = False
    last_status = "telegram_unavailable"
    for attempt in range(attempts):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        outside = host_menu_probe(token, url, timeout=min(4, remaining))
        if outside == "match":
            return "verified" if time.monotonic() <= deadline else "telegram_unavailable"
        if outside == "stale":
            last_status = "menu_stale"
            if not repair_attempted:
                repair_attempted = True
                fixed = set_host_menu(
                    token, url, timeout=min(4, max(1, deadline - time.monotonic()))
                )
                if fixed == "match":
                    return "verified" if time.monotonic() <= deadline else "telegram_unavailable"
                if fixed == "rejected":
                    last_status = "telegram_rejected"
                    break
                elif fixed == "api_error":
                    last_status = "telegram_error"
                    break
                elif fixed == "unreachable":
                    last_status = "telegram_unavailable"
            elif attempt + 1 == attempts:
                last_status = "menu_stale"
        elif outside == "rejected":
            last_status = "telegram_rejected"
            break
        elif outside == "api_error":
            last_status = "telegram_error"
            break
        else:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                inside = container_menu_probe(url, timeout=min(7, remaining))
                if inside == "match":
                    return "verified" if time.monotonic() <= deadline else "telegram_unavailable"
                if inside == "stale":
                    last_status = "menu_stale"
                    if not repair_attempted:
                        repair_attempted = True
                        fixed = container_menu_probe(
                            url, action="set", timeout=min(7, max(1, deadline - time.monotonic()))
                        )
                        if fixed == "match":
                            return (
                                "verified"
                                if time.monotonic() <= deadline
                                else "telegram_unavailable"
                            )
                        if fixed == "api_auth":
                            last_status = "telegram_rejected"
                            break
                        elif fixed == "api_error":
                            last_status = "telegram_error"
                            break
                        elif fixed == "unreachable":
                            last_status = "telegram_unavailable"
                elif inside == "api_auth":
                    last_status = "telegram_rejected"
                    break
                elif inside == "api_error":
                    last_status = "telegram_error"
                    break
                else:
                    last_status = "telegram_unavailable"
        remaining = deadline - time.monotonic()
        if attempt + 1 < attempts and remaining > 0:
            time.sleep(min(0.5, remaining))

    detail = {
        "menu_stale": "Telegram продолжает возвращать прежнюю кнопку.",
        "telegram_rejected": "Telegram отклонил обновление меню.",
        "telegram_error": "Telegram вернул ошибку при проверке меню.",
        "telegram_unavailable": "Telegram API временно недоступен.",
    }.get(last_status, "Telegram не подтвердил меню.")
    print(
        "ПРЕДУПРЕЖДЕНИЕ: " + detail + " Gateway, бот и HTTPS-панель здоровы; "
        "их работа сохранена, но Mini App не объявляется готовой. Повторный запуск "
        "проверит меню без пересоздания polling-контейнера."
    )
    return last_status


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
    """Остановить собственные live-ресурсы, включая сохранённые предыдущие, без удаления."""
    errors = []
    targets = {}
    for name in (LIVE_BOT, LIVE_TUNNEL):
        try:
            item = owned(name)
            if item:
                targets[item.get("Id", name)] = (name, item)
        except (LiveError, updater.UpdateError) as exc:
            errors.append(type(exc).__name__)
    try:
        for identifier in docker("ps", "-aq", "--filter", "label=" + LIVE_LABEL + "=1").split():
            if identifier in targets:
                continue
            item = inspect(identifier)
            if item and (item["Config"].get("Labels") or {}).get(LIVE_LABEL) == "1":
                targets[identifier] = (identifier, item)
    except (LiveError, updater.UpdateError) as exc:
        errors.append(type(exc).__name__)
    for name, item in targets.values():
        if is_running(item):
            try:
                docker("stop", "--time", "25", name, timeout=60)
            except updater.UpdateError as exc:
                errors.append(type(exc).__name__)
    if errors:
        raise LiveError("Не все собственные Mini App сервисы удалось остановить. Повторите Stop.")


def retire_owned():
    """Освободить фиксированные имена, сохранив предыдущие контейнеры и их volumes."""
    stop_owned()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    for name in (LIVE_BOT, LIVE_TUNNEL):
        if owned(name):
            docker("rename", name, name + "-previous-" + stamp)


def ensure_network():
    raw = docker("network", "inspect", LIVE_NETWORK, optional=True)
    if raw:
        network = json.loads(raw)[0]
        if (network.get("Labels") or {}).get(LIVE_LABEL) != "1":
            raise LiveError("Имя сети Mini App занято другим проектом.")
    else:
        docker("network", "create", "--label", LIVE_LABEL + "=1", LIVE_NETWORK)


def compose_owned(service):
    item = updater.inspect_container(service)
    if item:
        labels = item["Config"].get("Labels") or {}
        if (labels.get("com.docker.compose.project"), labels.get("com.docker.compose.service")) != (
            updater.PROJECT,
            service,
        ):
            raise LiveError("Docker-контейнер не прошёл проверку принадлежности SHLUZ.")
    return item


def restore_classic(root, state, *, restart=True):
    # Сначала гарантированно остановить второй polling, потом старый.
    stop_owned()
    old = inspect(state.get("old_id", ""))
    if not old:
        raise LiveError("Исходный Telegram-контейнер не найден. Volumes не удалены.")
    labels = old["Config"].get("Labels") or {}
    if (labels.get("com.docker.compose.project"), labels.get("com.docker.compose.service")) != (
        updater.PROJECT,
        "telegram_bot",
    ):
        raise LiveError("Исходный контейнер не прошёл проверку принадлежности.")
    token = trial.env(old).get("TELEGRAM_BOT_TOKEN", "")
    try:
        trial.telegram(
            token,
            "setChatMenuButton",
            menu_button=state.get("old_menu") or {"type": "commands"},
        )
    except trial.TrialError:
        # Работающий бот важнее временно недоступного Telegram API.
        print("Telegram menu будет восстановлено при появлении сети.")
    if restart and state.get("was_running") and not is_running(old):
        docker("start", old["Id"])
    if not restart and is_running(old):
        docker("stop", "--time", "25", old["Id"], timeout=60)
    state_file(root).unlink(missing_ok=True)


def stop(root, *, rollback=False):
    try:
        state = read_state(root)
    except (OSError, ValueError):
        # Journal помогает восстановлению, но не является разрешением на остановку.
        state = None
    if not isinstance(state, dict):
        state = None
    if rollback:
        gateway, old = compose_owned("shluz"), compose_owned("telegram_bot")
        if not gateway or not old:
            raise LiveError("Не найдены исходные сервисы для явного отката.")
        start_gateway(gateway)
        restore_classic(root, {**(state or {}), "old_id": old["Id"], "was_running": True})
        print("Классический бот восстановлен по явному запросу. Данные сохранены.")
        return
    errors = []
    try:
        stop_owned()
    except (LiveError, updater.UpdateError) as exc:
        errors.append(type(exc).__name__)
    for service in ("telegram_bot", "shluz"):
        try:
            item = compose_owned(service)
            if is_running(item):
                docker("stop", "--time", "25", item["Id"], timeout=60)
        except (LiveError, updater.UpdateError) as exc:
            errors.append(type(exc).__name__)
    if state:
        state["phase"] = "stop_failed" if errors else "stopped"
        save_state(root, state)
    if errors:
        raise LiveError(
            "Не все управляемые сервисы удалось остановить. Повторите Stop; данные сохранены."
        )
    print("SHLUZ и новая Mini App остановлены. Контейнеры и Docker volumes сохранены.")


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


def create_tunnel(cloud_image):
    if not re.fullmatch(r"cloudflare/cloudflared@sha256:[a-f0-9]{64}", str(cloud_image)):
        raise LiveError("Нет проверенного закреплённого образа Cloudflare Tunnel.")
    ensure_network()
    docker(
        "run",
        "-d",
        "--name",
        LIVE_TUNNEL,
        "--label",
        LIVE_LABEL + "=1",
        "--restart=no",
        "--network",
        LIVE_NETWORK,
        "--cap-drop=ALL",
        "--security-opt",
        "no-new-privileges",
        cloud_image,
        "tunnel",
        "--no-autoupdate",
        # auto: cloudflared сам выбирает протокол по результатам precheck
        # (в сети с прокси QUIC и TCP-фолбэки работают нестабильно по-разному).
        "--protocol",
        "auto",
        "--url",
        "http://miniapp:8081",
    )
    item = owned(LIVE_TUNNEL)
    if not item:
        raise LiveError("Контейнер Cloudflare Tunnel не создался.")
    return item


def restart_tunnel(tunnel, cloud_image, *, timeout_seconds=90):
    """Перезапускает один managed tunnel-контейнер; polling и volumes не трогает."""
    if tunnel:
        if is_running(tunnel):
            docker("stop", "--time", "25", tunnel["Id"], timeout=60)
        docker("start", tunnel["Id"], timeout=60)
        item = owned(LIVE_TUNNEL)
        if not item or item["Id"] != tunnel["Id"] or not is_running(item):
            raise LiveError("Cloudflare Tunnel не запустился после одного повтора.")
    else:
        item = create_tunnel(cloud_image)
        if not is_running(item):
            raise LiveError("Cloudflare Tunnel не запустился.")
    started_at = item.get("State", {}).get("StartedAt")
    url = trial.wait_tunnel(
        LIVE_TUNNEL,
        require_registered=True,
        since=started_at,
        timeout_seconds=timeout_seconds,
    )
    return url, item


def public_status(name, url, *, timeout=5):
    """Проверяет локальную панель и публичный health именно у ожидаемой Mini App."""
    try:
        if not trial.probe_local_panel(name, timeout=timeout):
            return "panel_unavailable"
    except (OSError, subprocess.TimeoutExpired, updater.UpdateError):
        return "panel_unavailable"
    diagnostics = {}
    if trial.probe_windows_https(url, timeout=timeout, diagnostics=diagnostics):
        return "ready"
    diagnostics = {}
    if trial.probe_container_https(name, url, timeout=timeout + 2, diagnostics=diagnostics):
        return "ready"
    categories = set(diagnostics.values())
    for category in ("dns", "tls", "timeout", "network"):
        if category in categories:
            return category
    if any(value.startswith("http:") for value in categories):
        return "http_error"
    if "response" in categories:
        return "wrong_service"
    return "unavailable"


def clear_managed_menu(token, url, *, timeout=4):
    """Удаляет только Mini App Quick Tunnel URL, не затрагивая другую кнопку."""
    try:
        menu = trial.telegram(token, "getChatMenuButton", request_timeout=timeout)
    except trial.TelegramApiError as exc:
        return "rejected" if exc.category == "rejected" else "unavailable"
    except trial.TrialError:
        return "error"
    current = (menu.get("web_app") or {}).get("url") if isinstance(menu, dict) else None
    managed_url = bool(
        isinstance(current, str)
        and re.fullmatch(
            r"https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com/miniapp",
            current,
        )
    )
    if current != url + "/miniapp" and not managed_url:
        return "unchanged"
    try:
        trial.telegram(
            token,
            "setChatMenuButton",
            request_timeout=timeout,
            menu_button={"type": "default"},
        )
        after = trial.telegram(token, "getChatMenuButton", request_timeout=timeout)
    except trial.TelegramApiError as exc:
        return "rejected" if exc.category == "rejected" else "unavailable"
    except trial.TrialError:
        return "error"
    return (
        "cleared"
        if isinstance(after, dict) and after.get("type") in ("default", "commands")
        else "stale"
    )


def record_tunnel_failure(root, state, token, reason, *urls):
    menu_status = "unchanged"
    for url in dict.fromkeys(u for u in urls if isinstance(u, str) and u.startswith("https://")):
        result = clear_managed_menu(token, url) if token else "unavailable"
        if result == "cleared":
            menu_status = "cleared"
            break
        if result in {"unavailable", "rejected", "error", "stale"}:
            menu_status = result
    # Не оставлять процесс cloudflared в бесконечном retry-цикле после
    # неподтверждённого публичного адреса. Gateway и bot-poller не останавливаются.
    with suppress(Exception):
        tunnel = owned(LIVE_TUNNEL)
        if is_running(tunnel):
            docker("stop", "--time", "25", tunnel["Id"], timeout=60)
    state["phase"] = "panel_unavailable" if reason == "panel_unavailable" else "tunnel_unavailable"
    state["origin"] = None
    state["tunnel_status"] = reason
    state["menu_status"] = menu_status
    save_state(root, state)
    print(
        "Mini App не готова: "
        + {
            "panel_unavailable": "локальная HTTP-панель не отвечает.",
            "dns": "публичный адрес не разрешается через DNS.",
            "tls": "HTTPS-проверка Cloudflare не прошла.",
            "timeout": "публичная HTTPS-проверка истекла по времени.",
            "network": "публичный HTTPS-маршрут недоступен.",
            "http_error": "публичный адрес ответил HTTP-ошибкой.",
            "wrong_service": "публичный адрес не ведёт к ожидаемой Mini App.",
            "tunnel_unregistered": "Cloudflare edge не подтвердил соединение.",
            "unavailable": "публичный health-check не подтвердил Mini App.",
        }.get(reason, "проверка туннеля не завершилась.")
    )
    if menu_status == "cleared":
        print(
            "Старая кнопка Mini App очищена; Gateway и бот сохранены. "
            "Повторите запуск после восстановления сети."
        )
    elif menu_status != "unchanged":
        print(
            "Telegram не подтвердил очистку старой кнопки; Mini App не считается "
            "готовой. Повторите запуск после проверки сети."
        )
    else:
        print(
            "Gateway и бот сохранены. Старая ссылка не показывается лаунчером; "
            "повторите запуск для восстановления."
        )


def refresh_active(root, state, gateway, old, live, tunnel):
    """Повторно проверяет сохранённый runtime без создания второго Telegram poller."""
    if not is_running(gateway) or not is_running(live):
        return False
    if live.get("State", {}).get("Health", {}).get("Status") != "healthy":
        return False
    if is_running(old):
        raise LiveError("Обнаружены два polling-бота; безопасный повторный запуск остановлен.")
    token = trial.env(old).get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        record_tunnel_failure(root, state, "", "tunnel_unregistered", state.get("origin"))
        return True
    old_url = state.get("origin") if isinstance(state.get("origin"), str) else ""
    cloud_image = state.get("cloudflared_image") or (
        (tunnel.get("Config", {}).get("Image") if tunnel else "") or ""
    )
    if not re.fullmatch(r"cloudflare/cloudflared@sha256:[a-f0-9]{64}", cloud_image):
        record_tunnel_failure(root, state, token, "tunnel_unregistered", old_url)
        return True
    restarted = False
    url = old_url
    try:
        if not tunnel or not is_running(tunnel):
            url, tunnel = restart_tunnel(tunnel, cloud_image)
            restarted = True
        else:
            started_at = tunnel.get("State", {}).get("StartedAt")
            same_process = (
                state.get("tunnel_id") == tunnel.get("Id")
                and state.get("tunnel_started_at") == started_at
            )
            if not same_process or not url:
                url = trial.wait_tunnel(
                    LIVE_TUNNEL,
                    require_registered=True,
                    since=started_at,
                    timeout_seconds=30,
                )
    except trial.TrialError:
        try:
            url, tunnel = restart_tunnel(tunnel, cloud_image)
            restarted = True
        except (LiveError, trial.TrialError, updater.UpdateError, subprocess.TimeoutExpired):
            with suppress(Exception):
                if tunnel and is_running(tunnel):
                    docker("stop", "--time", "25", tunnel["Id"], timeout=60)
            record_tunnel_failure(root, state, token, "tunnel_unregistered", old_url, url)
            return True
    live_env = dict(
        item.split("=", 1) for item in ((live.get("Config") or {}).get("Env") or []) if "=" in item
    )
    declared_url = live_env.get("TELEGRAM_MINIAPP_URL")
    if declared_url is not None and declared_url != url + "/miniapp":
        # Туннель переехал на новый адрес: пересоздать контейнер с новым origin,
        # иначе панель отклоняет запросы («Недопустимый источник запроса»).
        settings = current_settings(root, gateway, old)
        config = secure_env(root, environment_for(old, url))
        args = safe_candidate_args(old, {"image": live["Config"]["Image"]}, settings, config)
        docker("remove", "--force", LIVE_BOT)
        docker(*args)
        docker("network", "connect", "--alias", "miniapp", LIVE_NETWORK, LIVE_BOT)
        docker("start", LIVE_BOT)
        live = owned(LIVE_BOT)
        restarted = True
    local = public_status(LIVE_BOT, url, timeout=4)
    if local == "panel_unavailable":
        record_tunnel_failure(root, state, token, local, old_url, url)
        return True
    if local != "ready":
        if not restarted:
            try:
                url, tunnel = restart_tunnel(tunnel, cloud_image)
                restarted = True
            except (LiveError, trial.TrialError, updater.UpdateError, subprocess.TimeoutExpired):
                with suppress(Exception):
                    if tunnel and is_running(tunnel):
                        docker("stop", "--time", "25", tunnel["Id"], timeout=60)
                record_tunnel_failure(root, state, token, local, old_url, url)
                return True
        try:
            trial.wait_ready(LIVE_BOT, url, attempts=12, timeout_seconds=30)
        except trial.TrialError:
            local = public_status(LIVE_BOT, url, timeout=4)
            if local != "ready":
                with suppress(Exception):
                    actual = owned(LIVE_TUNNEL)
                    if is_running(actual):
                        docker("stop", "--time", "25", actual["Id"], timeout=60)
                record_tunnel_failure(root, state, token, local, old_url, url)
                return True
    menu_status = wait_menu(token, url)
    current_tunnel = owned(LIVE_TUNNEL)
    state.update(
        {
            "phase": "active" if menu_status == "verified" else "menu_unverified",
            "origin": url,
            "menu_status": menu_status,
            "tunnel_id": current_tunnel.get("Id") if current_tunnel else None,
            "tunnel_started_at": current_tunnel.get("State", {}).get("StartedAt")
            if current_tunnel
            else None,
            "cloudflared_image": cloud_image,
        }
    )
    save_state(root, state)
    if menu_status == "verified":
        print("Готово: Gateway, Telegram Mini App, HTTPS и кнопка Telegram подтверждены.")
    else:
        print(
            "Gateway и Mini App доступны; Telegram menu_unverified. Повторите запуск для read-back."
        )
    return True


def start(root):
    try:
        docker_platform = docker("info", "--format", "{{.OSType}}/{{.Architecture}}").strip()
        if docker_platform not in {"linux/amd64", "linux/x86_64"}:
            raise LiveError("Нужен Docker Desktop Linux/x64.")
        old_trial_root = trial.root_folder()
        legacy_trial_root = (
            Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share")))
            / "Shluz"
            / "miniapp-trial"
        )
        for candidate in (old_trial_root, legacy_trial_root):
            if (candidate / "active.json").exists():
                with updater.update_lock(candidate):
                    # Старую тестовую копию лишь останавливаем; историю и контейнеры сохраняем.
                    for name in ("noclip-miniapp-trial-bot", "noclip-miniapp-trial-tunnel"):
                        item = trial.inspect(name)
                        if item and (item["Config"].get("Labels") or {}).get(trial.LABEL) != "1":
                            raise LiveError("Имя прежней Mini App занято чужим контейнером.")
                        if is_running(item):
                            docker("stop", "--time", "25", name, timeout=60)
        leftover = trial.inspect("noclip-miniapp-trial-bot")
        if leftover and is_running(leftover):
            raise LiveError("Старая Mini App всё ещё работает; второй polling запрещён.")
        try:
            state = read_state(root)
        except (OSError, ValueError):
            state = None
        if not isinstance(state, dict):
            state = None
        if state and state.get("phase") in {
            "active",
            "menu_unverified",
            "tunnel_unavailable",
            "panel_unavailable",
        }:
            gateway, old = gateway_and_old()
            live, tunnel = owned(LIVE_BOT), owned(LIVE_TUNNEL)
            if (
                is_running(gateway)
                and is_running(live)
                and live["State"].get("Health", {}).get("Status") == "healthy"
                and not is_running(old)
            ):
                try:
                    refresh_active(root, state, gateway, old, live, tunnel)
                except (
                    LiveError,
                    trial.TrialError,
                    updater.UpdateError,
                    OSError,
                    subprocess.TimeoutExpired,
                    ValueError,
                    KeyError,
                ):
                    state["phase"] = "tunnel_unavailable"
                    state["origin"] = None
                    state["tunnel_status"] = "recheck_failed"
                    save_state(root, state)
                    print(
                        "Повторная проверка не завершилась. Gateway и бот сохранены; "
                        "туннель не объявлен рабочим. Повторите запуск после проверки сети."
                    )
                return
        gateway, old = gateway_and_old()
        if state:
            stop(root)
            gateway, old = gateway_and_old()
        settings = current_settings(root, gateway, old)
        image_manifest, cloud_image = prepare_images(root)
        token, previous_menu, username = ensure_preflight(old)
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
        stop_owned()
        if is_running(old):
            docker("stop", "--time", "25", old["Id"], timeout=60)
        start_gateway(gateway)
        backup(root)
        retire_owned()
        tunnel = create_tunnel(cloud_image)
        url = trial.wait_tunnel(
            LIVE_TUNNEL,
            require_registered=True,
            since=tunnel.get("State", {}).get("StartedAt"),
        )
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
            with suppress(OSError, ValueError, TypeError, trial.TrialError):
                owner_id = json.loads(owner_file.read_text(encoding="utf-8")).get("telegram_id")
                if type(owner_id) is int and 0 < owner_id < 2**52:
                    trial.telegram(
                        token,
                        "setChatMenuButton",
                        chat_id=owner_id,
                        menu_button={"type": "default"},
                    )
        menu_status = wait_menu(token, url)
        initial["phase"] = "active" if menu_status == "verified" else "menu_unverified"
        initial["menu_status"] = menu_status
        initial["origin"] = url  # HTTPS адрес не секрет; необходим для диагностики.
        current_tunnel = owned(LIVE_TUNNEL)
        initial["tunnel_id"] = current_tunnel.get("Id") if current_tunnel else None
        initial["tunnel_started_at"] = (
            current_tunnel.get("State", {}).get("StartedAt") if current_tunnel else None
        )
        initial["cloudflared_image"] = cloud_image
        save_state(root, initial)
        if menu_status == "verified":
            print("Готово: Gateway, Telegram Mini App, HTTPS и меню подтверждены.")
        else:
            print("SHLUZ работает, но подтверждение меню Telegram ожидается.")
            print("Повторный запуск проверит меню без пересоздания контейнеров.")
        print("Откройте Telegram и кнопку «Меню». Старый бот сохранён для отката.")
        if re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
            with suppress(Exception):
                webbrowser.open("https://t.me/" + username)
    except Exception:
        with suppress(Exception):
            # Ошибка не включает classic: он доступен только через явный rollback.
            stop(root)
        raise


def status(root):
    state = read_state(root)
    print("NoClip (portable):", root)
    gateway = updater.inspect_container("shluz")
    old = updater.inspect_container("telegram_bot")
    print("Gateway:", "работает" if is_running(gateway) else "остановлен")
    print("Новый Telegram:", "работает" if is_running(owned(LIVE_BOT)) else "остановлен")
    tunnel = owned(LIVE_TUNNEL)
    if state and state.get("origin") and is_running(tunnel):
        url_status = public_status(LIVE_BOT, state["origin"], timeout=4)
        label = "доступен" if url_status == "ready" else "не подтверждён (" + url_status + ")"
    else:
        label = "не запущен"
    print("Mini App HTTPS:", label)
    print("Старый бот (резерв):", "работает" if is_running(old) else "остановлен")
    print("Состояние:", state.get("phase") if state else "не переключён")
    if state and state.get("phase") == "menu_unverified":
        print("Меню Telegram: не подтверждено")


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
                import site_bridge

                if site_bridge.main([]) != 0:
                    print("Telegram запущен, но сайт/его HTTPS-туннель не поднялся.")
                    return 1
            elif operation in {"--stop", "stop"}:
                import site_bridge

                website_result = site_bridge.main(["stop"])
                stop(root)
                if website_result != 0:
                    print("Telegram остановлен; проверьте состояние туннеля сайта.")
                    return 1
            elif operation in {"--rollback", "rollback"}:
                stop(root, rollback=True)
                import site_bridge

                site_bridge.main(["stop"])
            elif operation in {"--backup", "backup"}:
                backup(root)
            elif operation in {"--status", "status"}:
                status(root)
                import site_bridge

                item = site_bridge.inspect_gateway()
                print("Gateway сайта:", "работает" if is_running(item) else "остановлен")
                try:
                    site_state = json.loads(
                        (root / "config" / "site-bridge" / "state.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    tunnel_up = (
                        type(site_state.get("pid")) is int
                        and site_state.get("phase") != "stopped"
                    )
                except (OSError, ValueError, TypeError):
                    tunnel_up = False
                print(
                    "Туннель сайта:",
                    "запущен" if tunnel_up else "остановлен/не подтверждён",
                )
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
