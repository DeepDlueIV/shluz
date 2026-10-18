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
import tarfile
import tempfile
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
        and data.get("schema") in {1, 2}
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
        and (
            data["schema"] == 1
            or re.fullmatch(r"sha256:[a-f0-9]{64}", str(data.get("image_config_digest", "")))
        )
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


def archive_config_digest(path, image=None):
    """Read the unchanged config blob (including layer diff_ids), never extract/run the image.

    Legacy Docker saves <digest>.json; containerd saves blobs/sha256/<digest>.
    Inspect .Id is NOT portable: graphdriver returns a config digest, containerd a
    manifest/index digest. Archive SHA-256 is checked separately before this call.
    """
    try:
        with tarfile.open(path, "r:*") as archive:

            def read_small(name):
                matches = [m for m in archive.getmembers() if m.name == name]
                if len(matches) != 1 or not matches[0].isfile() or matches[0].size > 4 * 1024**2:
                    raise UpdateError("Некорректные метаданные Docker-архива.")
                with archive.extractfile(matches[0]) as stream:
                    return stream.read(4 * 1024**2 + 1)

            entries = json.loads(read_small("manifest.json"))
            if not isinstance(entries, list) or len(entries) != 1:
                raise UpdateError("Ожидался архив одной проверенной версии образа.")
            entry = entries[0]
            if not isinstance(entry, dict) or (
                image and image not in (entry.get("RepoTags") or [])
            ):
                raise UpdateError("Архив не содержит указанную версию Shluz.")
            name = entry.get("Config", "")
            if not isinstance(name, str) or not re.fullmatch(
                r"(?:[a-f0-9]{64}\.json|blobs/sha256/[a-f0-9]{64})", name
            ):
                raise UpdateError("Неподдерживаемый формат конфигурации Docker-образа.")
            raw = read_small(name)
            digest = hashlib.sha256(raw).hexdigest()
            if name not in {digest + ".json", "blobs/sha256/" + digest}:
                raise UpdateError("Контрольная сумма конфигурации в архиве не совпала.")
            config = json.loads(raw)
            if not isinstance(config, dict) or (config.get("os"), config.get("architecture")) != (
                "linux",
                "amd64",
            ):
                raise UpdateError("Образ не предназначен для Linux/x64.")
            return "sha256:" + digest
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
        raise UpdateError("Не удалось проверить конфигурацию Docker-архива.") from exc


def exported_config_digest(image_id):
    """Docker exports its exact config bytes; no container is started or executable trusted."""
    with tempfile.TemporaryDirectory(prefix="shluz-image-check-") as directory:
        archive = Path(directory) / "loaded.tar"
        docker("image", "save", "--output", str(archive), image_id, timeout=300)
        return archive_config_digest(archive)


def inspect_image(image):
    raw = docker("image", "inspect", image, optional=True)
    if not raw:
        return None
    try:
        items = json.loads(raw)
        info = items[0] if isinstance(items, list) and len(items) == 1 else None
        if not isinstance(info, dict) or not re.fullmatch(
            r"sha256:[a-f0-9]{64}", str(info.get("Id", ""))
        ):
            raise ValueError()
        return info
    except (ValueError, TypeError, KeyError) as exc:
        raise UpdateError("Docker вернул некорректный идентификатор образа.") from exc


def prepare_image(manifest, archive):
    """Verify archive, load, compare config by content across both Docker storage backends."""
    if not valid_file(archive, manifest["image_size"], manifest["image_sha256"]):
        raise UpdateError("Контрольная сумма скачанного архива не совпала; установка остановлена.")
    expected = manifest.get("image_config_digest", manifest["image_id"])
    if archive_config_digest(archive, manifest["image"]) != expected:
        raise UpdateError("Конфигурация образа не совпала с опубликованной версией.")
    info = inspect_image(manifest["image"])
    if not info or info["Id"] != expected:
        # Load only the checksum-verified archive. Its local ID may legitimately change
        # when containerd wraps a legacy export in a manifest/index.
        docker("image", "load", "--input", str(archive), timeout=600)
        info = inspect_image(manifest["image"])
    if not info or (info.get("Os"), info.get("Architecture")) != ("linux", "amd64"):
        raise UpdateError("Загруженный образ не найден или предназначен для другой платформы.")
    local_id = info["Id"]
    actual = local_id if local_id == expected else exported_config_digest(local_id)
    if not secrets.compare_digest(actual, expected):
        raise UpdateError("Контрольная сумма конфигурации загруженного Docker-образа не совпала.")
    return local_id


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
    shared = {
        "image": manifest.get("local_image_id", manifest["image"]),
        "pull_policy": "never",
        "restart": "unless-stopped",
    }
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


BACKUP_CODE = """import os,shutil,sqlite3,sys,tarfile,tempfile
from pathlib import Path
root=Path('/source')
try:
 # Источник read-only: SQLite WAL требует writable shm даже при mode=ro.
 # Проверяем полную остановленную копию, включая WAL, а не открываем оригинал.
 with tempfile.TemporaryDirectory(prefix='shluz-backup-') as folder:
  staged=Path(folder)/'data'
  shutil.copytree(root,staged,symlinks=True)
  for path in [staged,*staged.rglob('*')]:
   if path.is_symlink(): raise OSError('unsupported symlink')
   path.chmod(0o700 if path.is_dir() else 0o600)
  for path in list(staged.rglob('*')):
   if not path.is_file(): continue
   with path.open('rb') as f: header=f.read(16)
   if header==b'SQLite format 3\\0':
    db=sqlite3.connect(str(path))
    try:
     if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':
      raise sqlite3.DatabaseError('integrity failure')
    finally: db.close()
  with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as f:
   f.add(root,arcname='data')
except sqlite3.Error:
 print('SHLUZ_BACKUP_SQLITE',file=sys.stderr)
 sys.exit(20)
except OSError:
 print('SHLUZ_BACKUP_STORAGE',file=sys.stderr)
 sys.exit(21)
"""


def archive_volume(volume, image, archive):
    """Поток из read-only тома; файл создаёт хост, не root внутри Docker.

    Не монтируем каталог назначения: UID контейнера не должен лишать владельца
    чтения резервной копии. Бинарные данные не проходят через кодировку Windows.
    """
    archive = Path(archive)
    partial = archive.with_suffix(".part")
    args = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--user",
        "0",
        "--mount",
        f"type=volume,source={volume},target=/source,readonly",
        "--entrypoint",
        "python",
        image,
        "-c",
        BACKUP_CODE,
    ]
    try:
        with partial.open("xb") as output:
            partial.chmod(0o600)
            result = subprocess.run(
                args,
                stdout=output,
                stderr=subprocess.PIPE,
                timeout=300,
                check=False,
            )
            output.flush()
            os.fsync(output.fileno())
        if result.returncode:
            reason = {
                20: "не пройдена проверка целостности копии SQLite",
                21: "не удалось прочитать том или записать временную копию",
                125: "Docker не смог запустить процесс резервного копирования",
            }.get(result.returncode, "процесс резервного копирования завершился с ошибкой")
            # Только код и этап; сырой stderr никогда не содержит публичного отчёта.
            raise UpdateError(
                f"Резервная копия не создана: {reason} (код {result.returncode}). "
                "Обновление не начато; прежние процессы запускаются снова."
            )
        # Прочитать весь архив до EOF: проверить и gzip CRC, и доступность на хосте.
        import gzip

        with gzip.open(partial, "rb") as stream:
            while stream.read(1024 * 1024):
                pass
        with tarfile.open(partial, "r:gz") as saved:
            if "data" not in saved.getnames():
                raise UpdateError("В резервной копии отсутствуют данные; обновление остановлено.")
        partial.replace(archive)
    except (OSError, EOFError, tarfile.TarError, subprocess.TimeoutExpired) as exc:
        # stderr может содержать приватные пути/данные; владельцу только безопасное описание.
        raise UpdateError(
            "Не удалось создать и проверить резервную копию. "
            "Обновление не начато; прежние процессы будут запущены снова."
        ) from exc
    finally:
        partial.unlink(missing_ok=True)


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
            archive_volume(volume, image, dest / "data.tar.gz")
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
            "local_image_id": manifest.get("local_image_id", manifest["image_id"]),
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
                manifest["local_image_id"] = prepare_image(manifest, image_archive)
                # Pin the runtime to the ID just verified; a mutable tag cannot change it.
                compose.write_text(
                    compose_text(manifest, home / "config", settings), encoding="utf-8"
                )
                docker(*args, "config", "--quiet")

            def backup():
                print("Сохраняю настройки и резервные копии баз…")
                location = backup_volumes(
                    home, settings, manifest["local_image_id"], [bot, gateway]
                )
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

            prepare()  # Always verify before any backup, stop or migration.
            installed = home / "installed.json"
            same = (
                installed.exists()
                and json.loads(installed.read_text()).get("commit") == manifest["commit"]
            )
            matching_ids = {
                manifest["local_image_id"],
                manifest.get("image_config_digest", manifest["image_id"]),
            }
            if same and gateway and gateway["Image"] in matching_ids:
                print("Последняя версия уже установлена. Запускаю её.")
                start()
            else:
                perform_update(
                    manifest, home, settings, prepare=lambda: None, backup=backup, start=start
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
