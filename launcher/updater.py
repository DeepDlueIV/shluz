"""Shluz preview: готовый Docker-образ, сохранение локальных данных, один запуск.

Python stdlib, без Git, pip, PowerShell policy changes и ручного выбора веток.
Только локальная SQLite owner-установка. Никаких платежей или сбросов базы.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

REPOSITORY = "DeepDlueIV/shluz"
RELEASE_ROOT = f"https://github.com/{REPOSITORY}/releases/download/"
MANIFEST_URL = RELEASE_ROOT + "preview/manifest.json"
PROJECT = "shluz-owner-check"
MAX_IMAGE = 2 * 1024**3 - 1


class UpdateError(RuntimeError):
    pass


def validate_manifest(data):
    if not isinstance(data, dict):
        raise UpdateError("Сервер обновлений вернул неправильный ответ.")
    commit = data.get("commit", "")
    expected = RELEASE_ROOT + f"preview-{commit}/image.tar.gz"
    if not (
        type(data.get("schema")) is int
        and data.get("schema") == 1
        and data.get("channel") == "preview"
        and isinstance(commit, str)
        and re.fullmatch("[a-f0-9]{40}", commit)
        and data.get("image") == "shluz-preview:" + commit
        and re.fullmatch(r"sha256:[a-f0-9]{64}", str(data.get("image_id", "")))
        and re.fullmatch("[a-f0-9]{64}", str(data.get("image_sha256", "")))
        and type(data.get("image_size")) is int
        and 0 < data["image_size"] <= MAX_IMAGE
        and data.get("image_url") == expected
        and data.get("platform") == "linux/amd64"
    ):
        raise UpdateError("Обновление не прошло проверку. Текущая версия не изменена.")
    return data


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


@contextmanager
def update_lock(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    handle = (root / ".update.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            if handle.seek(0, 2) == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise UpdateError("Обновление уже запущено. Дождитесь первого окна.") from exc
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise UpdateError("Обновление уже запущено.") from exc
        yield
    finally:
        handle.close()


def valid_file(path, size, digest):
    path = Path(path)
    if not path.is_file() or path.stat().st_size != size:
        return False
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return secrets.compare_digest(h.hexdigest(), digest)


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        from urllib.parse import urlsplit

        url = urlsplit(newurl)
        # Release asset CDN; checksum remains mandatory after redirects.
        if url.scheme != "https" or url.hostname not in {
            "github.com",
            "release-assets.githubusercontent.com",
            "objects.githubusercontent.com",
            "github-releases.githubusercontent.com",
        }:
            raise UpdateError("Небезопасное перенаправление при скачивании.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def response(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Shluz-Preview-Updater/1"})
    return urllib.request.build_opener(SafeRedirect()).open(request, timeout=60)


def get_manifest():
    try:
        with response(MANIFEST_URL) as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise UpdateError("Слишком большой манифест обновления.")
        return validate_manifest(json.loads(raw))
    except (OSError, ValueError) as exc:
        raise UpdateError(
            "Не удалось получить проверенную версию с GitHub. "
            "Проверьте соединение; текущие данные не изменены."
        ) from exc


def download_image(manifest, target):
    if valid_file(target, manifest["image_size"], manifest["image_sha256"]):
        return
    partial = target.with_suffix(".part")
    try:
        size = 0
        deadline = time.monotonic() + 1800
        with response(manifest["image_url"]) as source, partial.open("wb") as dest:
            while block := source.read(1024 * 1024):
                size += len(block)
                if size > manifest["image_size"] or time.monotonic() > deadline:
                    raise UpdateError("Скачивание превысило безопасный размер или время.")
                dest.write(block)
        if not valid_file(partial, manifest["image_size"], manifest["image_sha256"]):
            raise UpdateError("Архив повреждён. Старый шлюз не изменён; повторите обновление.")
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


def docker(*args, timeout=180, optional=False):
    try:
        result = subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(
            "Docker не отвечает. Откройте Docker Desktop и повторите запуск."
        ) from exc
    if result.returncode and not optional:
        # Не выводим сырой docker inspect/config: там могут быть секреты.
        raise UpdateError(
            f"Не удалось выполнить Docker «{args[0]}» (код {result.returncode}). "
            "Данные и резервные копии сохранены. Передайте это окно Codex."
        )
    return result.stdout if result.returncode == 0 else ""


def inspect_container(service):
    raw = docker(
        "ps",
        "-aq",
        "--filter",
        f"label=com.docker.compose.project={PROJECT}",
        "--filter",
        f"label=com.docker.compose.service={service}",
    )
    ids = raw.split()
    if not ids:
        return None
    if len(ids) > 1:
        raise UpdateError("Найдено несколько экземпляров Shluz. Codex должен выбрать установку.")
    return json.loads(docker("inspect", ids[0]))[0]


def import_local_files(source, destination):
    source, destination = Path(source), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name in (".env.owner", ".env.telegram"):
        src, dst = source / name, destination / name
        if src.is_file() and not dst.exists():
            shutil.copyfile(src, dst)
            dst.chmod(0o600)


def env_values(container):
    return dict(item.split("=", 1) for item in container["Config"].get("Env", []) if "=" in item)


def write_env(path, values):
    # Одинарные кавычки dotenv предотвращают подстановку $ в секретах.
    def quoted(value):
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"

    text = "\n".join(f"{key}={quoted(value)}" for key, value in values.items()) + "\n"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def prepare_settings(root, gateway, bot):
    config = root / "config"
    config.mkdir(exist_ok=True)
    legacy = config / "legacy"
    legacy.mkdir(exist_ok=True)
    state_file = root / "runtime.json"
    if state_file.exists():
        settings = json.loads(state_file.read_text(encoding="utf-8"))
        if settings.get("project") != PROJECT:
            raise UpdateError("Неподдерживаемая установка; рабочую базу не меняем.")
    else:
        settings = {
            "project": PROJECT,
            "volumes": {
                "gateway": PROJECT + "_owner_data",
                "bot": PROJECT + "_owner_telegram_data",
                "connector": PROJECT + "_owner_telegram_connector",
            },
        }
    for service, container in [("gateway", gateway), ("bot", bot)]:
        if not container:
            continue
        labels = container["Config"].get("Labels") or {}
        working = labels.get("com.docker.compose.project.working_dir")
        if working:
            import_local_files(working, config)
        for path in labels.get("com.docker.compose.project.config_files", "").split(","):
            if path:
                import_local_files(Path(path).parent, config)
        values = env_values(container)
        if service == "gateway":
            # Не пытаться делать tar-backup внешней PostgreSQL или нестандартного пути.
            db_url = values.get("SHLUZ_DATABASE_URL", "sqlite+pysqlite:///./data/shluz.db")
            if not (
                db_url.startswith("sqlite")
                and ("/app/data/" in db_url or "///./data/" in db_url or "///data/" in db_url)
            ):
                raise UpdateError(
                    "Запускатель рассчитан на локальную SQLite. "
                    "Codex должен настроить обновление этой базы отдельно."
                )
            if not (config / ".env.owner").exists():
                write_env(
                    config / ".env.owner",
                    {
                        k: v
                        for k, v in values.items()
                        if k.startswith("SHLUZ_") or k == "VENICE_API_KEY"
                    },
                )
        elif not (config / ".env.telegram").exists():
            write_env(
                config / ".env.telegram",
                {k: v for k, v in values.items() if k.startswith("TELEGRAM_")},
            )
        for mount in container.get("Mounts", []):
            if mount["Destination"] == "/app/data":
                if mount["Type"] != "volume":
                    raise UpdateError("Нестандартное хранилище Shluz: обновление остановлено.")
                settings["volumes"][service] = mount["Name"]
            if mount["Destination"] in {"/app/telegram-connector", "/run/shluz-connector"}:
                if mount["Type"] != "volume":
                    raise UpdateError("Нестандартное хранилище служебного ключа.")
                settings["volumes"]["connector"] = mount["Name"]
            if (
                service == "bot"
                and mount["Type"] == "bind"
                and mount["Destination"]
                in {"/run/telegram-legacy", "/run/secrets/telegram-access.json"}
            ):
                source = Path(mount["Source"])
                if source.is_dir():
                    source /= "telegram-access.json"
                target = legacy / "telegram-access.json"
                if source.is_file() and not target.exists():
                    shutil.copyfile(source, target)
                    target.chmod(0o600)
    if not (config / ".env.owner").exists():
        write_env(
            config / ".env.owner",
            {
                "SHLUZ_ADMIN_USERNAME": "owner",
                "SHLUZ_ADMIN_PASSWORD": secrets.token_urlsafe(20),
                "SHLUZ_BOOTSTRAP_API_TOKEN": secrets.token_urlsafe(32),
                "SHLUZ_ACTIVE_PROVIDER": "mock",
                "SHLUZ_DATABASE_URL": "sqlite+pysqlite:////app/data/shluz.db",
            },
        )
        print("Создана новая тестовая установка. Логин owner; пароль — в config/.env.owner.")
    settings["telegram"] = (config / ".env.telegram").is_file()
    for name in settings["volumes"].values():
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]+", name):
            raise UpdateError("Неподдерживаемое имя тома.")
    write_json(state_file, settings)
    return settings


def compose_text(manifest, config, settings):
    # JSON — валидный YAML. Не интерполируем секреты в сгенерированный Compose.
    config = Path(config).resolve()
    shared = {"image": manifest["image"], "pull_policy": "never", "restart": "unless-stopped"}
    service = {
        **shared,
        "env_file": [str(config / ".env.owner")],
        "ports": ["127.0.0.1:18000:8000"],
        "environment": {
            "SHLUZ_TELEGRAM_CONNECTOR_FILE": "/app/telegram-connector/key",
            "SHLUZ_BUILD_SHA": manifest["commit"],
        },
        "volumes": ["gateway:/app/data", "connector:/app/telegram-connector"],
    }
    services = {"shluz": service}
    if settings.get("telegram"):
        services["telegram_bot"] = {
            **shared,
            "env_file": [str(config / ".env.telegram")],
            "environment": {
                "TELEGRAM_GATEWAY_URL": "http://shluz:8000",
                "TELEGRAM_DATABASE_PATH": "/app/data/telegram.sqlite",
                "TELEGRAM_CONNECTOR_FILE": "/run/shluz-connector/key",
                "TELEGRAM_ACCESS_FILE": "/run/telegram-legacy",
            },
            "command": ["python", "-m", "telegram_bot"],
            "depends_on": {"shluz": {"condition": "service_healthy"}},
            "volumes": [
                "bot:/app/data",
                "connector:/run/shluz-connector:ro",
                {
                    "type": "bind",
                    "source": str(config / "legacy"),
                    "target": "/run/telegram-legacy",
                    "read_only": True,
                },
            ],
            "healthcheck": {
                "test": ["CMD", "python", "-m", "telegram_bot.healthcheck"],
                "interval": "10s",
                "timeout": "5s",
                "start_period": "30s",
                "retries": 12,
            },
            "init": True,
            "stop_grace_period": "20s",
        }
    return json.dumps(
        {
            "name": settings["project"],
            "services": services,
            "volumes": {k: {"name": v} for k, v in settings["volumes"].items()},
        },
        indent=2,
    )


BACKUP_CODE = """import os,sqlite3,tarfile
from pathlib import Path
root=Path('/source')
for path in root.rglob('*'):
 if path.is_file():
  with path.open('rb') as f: header=f.read(16)
  if header==b'SQLite format 3\\0':
   db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
   assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok','SQLite integrity failure'
   db.close()
out=Path('/destination/data.tar.gz')
with tarfile.open(out,'w:gz') as f: f.add(root,arcname='data')
with tarfile.open(out,'r:gz') as f: f.getmembers()
out.chmod(0o600)
"""


def backup_volumes(root, settings, image, containers):
    target = root / "backups" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target.mkdir(parents=True, exist_ok=False)
    running = [c["Id"] for c in containers if c and c["State"].get("Running")]
    if running:
        docker("stop", "--time", "25", *running, timeout=90)
    try:
        for kind, volume in settings["volumes"].items():
            if not docker("volume", "inspect", volume, optional=True):
                continue
            dest = target / kind
            dest.mkdir()
            docker(
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "0",
                "--mount",
                f"type=volume,source={volume},target=/source,readonly",
                "--mount",
                f"type=bind,source={dest.resolve()},target=/destination",
                "--entrypoint",
                "python",
                image,
                "-c",
                BACKUP_CODE,
                timeout=300,
            )
            archive = dest / "data.tar.gz"
            if not archive.is_file() or not archive.stat().st_size:
                raise UpdateError("Резервная копия не создана; обновление не начато.")
        shutil.copytree(root / "config", target / "config")
        write_json(
            target / "backup.json",
            {
                "project": settings["project"],
                "volumes": settings["volumes"],
                "old_images": [c["Image"] for c in containers if c],
            },
        )
    except Exception:
        # Миграции ещё не запускались: безопасно вернуть прежние процессы.
        if running:
            docker("start", *running, optional=True)
        raise
    return target


def perform_update(manifest, root, settings, *, prepare, backup, start):
    prepare()  # Скачать/проверить/загрузить ДО остановки приложения.
    backup()
    start()  # Не отмечать версию установленной, пока healthcheck не прошёл.
    write_json(
        root / "installed.json",
        {
            "commit": manifest["commit"],
            "image_id": manifest["image_id"],
            "updated_at": datetime.now(UTC).isoformat(),
        },
    )


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    home = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share"))) / "Shluz"
    home.mkdir(parents=True, exist_ok=True)
    try:
        with update_lock(home):
            print("Shluz — обновить и открыть\n")
            platform = docker("info", "--format", "{{.OSType}}/{{.Architecture}}").strip()
            if platform not in {"linux/x86_64", "linux/amd64"}:
                raise UpdateError("Нужен Docker Desktop в режиме Linux containers (x64).")
            docker("compose", "version")
            manifest = get_manifest()
            print("Проверенная версия: " + manifest["commit"][:8])
            gateway, bot = inspect_container("shluz"), inspect_container("telegram_bot")
            settings = prepare_settings(home, gateway, bot)
            version = home / "versions" / manifest["commit"]
            version.mkdir(parents=True, exist_ok=True)
            image_archive = version / "image.tar.gz"
            compose = version / "compose.json"
            compose.write_text(compose_text(manifest, home / "config", settings), encoding="utf-8")
            if shutil.disk_usage(home).free < manifest["image_size"] * 3 + 256 * 1024**2:
                raise UpdateError("Недостаточно свободного места для обновления и резервной копии.")
            args = (
                "compose",
                "-p",
                settings["project"],
                "--env-file",
                str(home / "config" / ".env.owner"),
                "-f",
                str(compose),
            )
            docker(*args, "config", "--quiet")

            def prepare():
                print("Скачиваю и проверяю готовый образ…")
                download_image(manifest, image_archive)
                current = docker(
                    "image", "inspect", manifest["image"], "--format", "{{.Id}}", optional=True
                ).strip()
                if current != manifest["image_id"]:
                    docker("image", "load", "--input", str(image_archive), timeout=600)
                actual = docker(
                    "image", "inspect", manifest["image"], "--format", "{{.Id}}"
                ).strip()
                if actual != manifest["image_id"]:
                    raise UpdateError("Контрольная сумма Docker-образа не совпала.")

            def backup():
                print("Сохраняю настройки и резервные копии баз…")
                location = backup_volumes(home, settings, manifest["image"], [bot, gateway])
                print("Копия сохранена: " + str(location))

            def start():
                print("Запускаю обновлённые шлюз и бот…")
                docker(
                    *args,
                    "up",
                    "-d",
                    "--no-build",
                    "--pull",
                    "never",
                    "--wait",
                    "--wait-timeout",
                    "180",
                    timeout=240,
                )

            installed = home / "installed.json"
            same = (
                installed.exists()
                and json.loads(installed.read_text()).get("commit") == manifest["commit"]
            )
            if same and gateway and gateway["Image"] == manifest["image_id"]:
                print("Последняя версия уже установлена. Запускаю её.")
                start()
            else:
                perform_update(
                    manifest, home, settings, prepare=prepare, backup=backup, start=start
                )
            print("Готово. Открываю панель. Данные сохранены.")
            webbrowser.open("http://127.0.0.1:18000/admin")
        return 0
    except (UpdateError, OSError, ValueError, KeyError) as exc:
        message = (
            str(exc)
            if isinstance(exc, UpdateError)
            else "Локальная настройка требует проверки Codex."
        )
        print("\nНе удалось закончить обновление: " + message)
        print("Не удаляйте Docker Volumes. Резервные копии: " + str(home / "backups"))
        print("Откат после миграции выполняет Codex из согласованной резервной копии.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
