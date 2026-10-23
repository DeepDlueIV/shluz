"""Единое portable-управление локальным SHLUZ (Windows, Docker Desktop).

Никаких docker down, rm, volume rm и автоматического восстановления поверх данных.
Старый Shluz.cmd остаётся отдельным и использует прежний AppData-путь.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import updater


class ControlError(RuntimeError):
    pass


def portable_home():
    requested = Path(os.environ.get("SHLUZ_HOME", str(Path(__file__).resolve().parent)))
    if not requested.is_absolute():
        raise ControlError("Папка SHLUZ_HOME должна быть абсолютным путём.")
    return requested.resolve()


def legacy_home():
    return (
        Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share")))
        / "Shluz"
    ).resolve()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def migration_files(source):
    # Переносим пользовательские настройки, исходные backup и загрузки,
    # но не устаревшие исполняемые файлы и не временные lock/part.
    files = []
    for name in ("config", "versions", "backups", "miniapp-trial"):
        folder = source / name
        if not folder.exists():
            continue
        if folder.is_symlink() or not folder.is_dir():
            raise ControlError("В старой установке обнаружен нестандартный каталог.")
        for item in folder.rglob("*"):
            if item.is_symlink():
                raise ControlError("Перенос через ссылки запрещён; старые данные не изменены.")
            if item.is_file() and not item.name.endswith((".part", ".tmp")):
                files.append(item.relative_to(source))
            elif not item.is_file() and not item.is_dir():
                raise ControlError("Нестандартный файл в старой установке.")
    for name in ("installed.json", "runtime.json"):
        file = source / name
        if file.is_symlink():
            raise ControlError("Не разрешён перенос ссылки на файл состояния.")
        if file.is_file():
            files.append(Path(name))
    return sorted(files)


def migrate_legacy(root):
    """Только copy+SHA256: не переименовываем и не удаляем AppData."""
    root = Path(root).resolve()
    source = legacy_home()
    if not source.is_dir() or root == source:
        return 0
    if source in root.parents or root in source.parents:
        raise ControlError("Portable и старая папка не могут находиться одна внутри другой.")
    if (source / "miniapp-trial" / "active.json").exists():
        raise ControlError(
            "В старой папке найдена незавершённая Mini App. "
            "Сначала остановите прежнюю проверку; ничего не перенесено."
        )
    if (root / "miniapp-trial" / "active.json").exists():
        raise ControlError("Mini App работает: перенос конфигурации сейчас запрещён.")
    marker = root / "migration.json"
    if marker.exists():
        data = json.loads(marker.read_text(encoding="utf-8"))
        if data.get("schema") != 1 or data.get("source") != str(source):
            raise ControlError("История переноса не совпадает; файлы не перезаписаны.")
        return 0

    files = migration_files(source)
    pending = []
    # Сначала проверяем ВСЕ конфликты, затем начинаем копирование.
    for relative in files:
        src, dst = source / relative, root / relative
        if dst.is_symlink() or not dst.parent.resolve().is_relative_to(root):
            raise ControlError("Небезопасный путь назначения; перенос не выполнен.")
        if dst.exists():
            if not dst.is_file() or dst.stat().st_size != src.stat().st_size:
                raise ControlError(
                    "Файлы AppData и portable отличаются. Ничего не перезаписано."
                )
            if sha256(src) != sha256(dst):
                raise ControlError(
                    "Файлы AppData и portable отличаются. Ничего не перезаписано."
                )
        else:
            pending.append((src, dst))
    root.mkdir(parents=True, exist_ok=True)
    required = sum(src.stat().st_size for src, _ in pending)
    if required and shutil.disk_usage(root).free < required + 64 * 1024**2:
        raise ControlError("Не хватает места для проверенного переноса данных.")

    for src, dst in pending:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.parent.resolve().is_relative_to(root):
            raise ControlError("Путь назначения неожиданно изменился.")
        # Временный файл находится на том же диске; rename атомарный.
        with tempfile.NamedTemporaryFile(
            prefix=".noclip-copy-", dir=dst.parent, delete=False
        ) as stream:
            tmp = Path(stream.name)
        try:
            shutil.copyfile(src, tmp)
            if src.stat().st_size != tmp.stat().st_size or sha256(src) != sha256(tmp):
                raise ControlError("Проверка копии SHA-256 не прошла.")
            # Данные назначения никогда не перезаписываются.
            if dst.exists() or dst.is_symlink():
                raise ControlError("Файл назначения изменился во время переноса.")
            tmp.replace(dst)
            dst.chmod(0o600)
        finally:
            tmp.unlink(missing_ok=True)
    # Повторная проверка всех файлов до записи маркера успеха.
    if any(sha256(source / rel) != sha256(root / rel) for rel in files):
        raise ControlError("Проверка перенесённых данных не прошла.")
    updater.write_json(
        marker,
        {
            "schema": 1,
            "source": str(source),
            "files": len(files),
            "completed_at": datetime.now(UTC).isoformat(),
        },
    )
    return len(pending)


def require_docker():
    platform = updater.docker("info", "--format", "{{.OSType}}/{{.Architecture}}").strip()
    if platform not in {"linux/amd64", "linux/x86_64"}:
        raise ControlError("Нужен Docker Desktop: Linux containers / x64.")


def containers():
    result = {}
    for service in ("shluz", "telegram_bot"):
        item = updater.inspect_container(service)
        if item:
            labels = item.get("Config", {}).get("Labels") or {}
            if (labels.get("com.docker.compose.project"),
                    labels.get("com.docker.compose.service")) != (updater.PROJECT, service):
                raise ControlError("Контейнер не принадлежит ожидаемому проекту.")
        result[service] = item
    return result


def settings_for(root, existing):
    state = root / "runtime.json"
    if state.is_file():
        values = json.loads(state.read_text(encoding="utf-8"))
    else:
        if not existing["shluz"]:
            raise ControlError("Шлюз ещё не установлен. Выберите установку/обновление.")
        values = updater.prepare_settings(root, existing["shluz"], existing["telegram_bot"])
    if values.get("project") != updater.PROJECT:
        raise ControlError("Неверный проект в portable runtime.json.")
    volumes = values.get("volumes")
    if not isinstance(volumes, dict) or any(
        not isinstance(volumes.get(k), str) for k in ("gateway", "bot", "connector")
    ):
        raise ControlError("Неизвестная конфигурация Docker-томов.")
    if not (root / "config" / ".env.owner").is_file():
        raise ControlError("Нет перенесённых настроек .env.owner. Новые не создаю.")
    return values


def check_volumes(settings, existing):
    """Запрещает незаметное подключение нового пустого тома."""
    volumes = settings["volumes"]
    required = set()
    for service, kind in (("shluz", "gateway"), ("telegram_bot", "bot")):
        item = existing[service]
        if not item:
            continue
        mounts = [m for m in item.get("Mounts", []) if m.get("Destination") == "/app/data"]
        if len(mounts) != 1 or mounts[0].get("Type") != "volume":
            raise ControlError("Путь базы отличается от ожидаемого Docker volume.")
        if mounts[0].get("Name") != volumes[kind]:
            raise ControlError("Docker volume не совпадает с сохранённой конфигурацией.")
        required.add(kind)
        for mount in item.get("Mounts", []):
            if mount.get("Destination") in {"/app/telegram-connector", "/run/shluz-connector"}:
                if mount.get("Type") != "volume" or mount.get("Name") != volumes["connector"]:
                    raise ControlError("Служебный Docker volume не совпадает.")
                required.add("connector")
    for kind in required:
        if not updater.docker("volume", "inspect", volumes[kind], optional=True):
            raise ControlError("Ожидаемый Docker volume отсутствует. Запуск запрещён.")
    return required


def running(item):
    return bool(item and item.get("State", {}).get("Running"))


def wait_healthy(identifier, seconds=120):
    for _ in range(seconds):
        raw = updater.docker("inspect", identifier)
        current = json.loads(raw)[0]
        state = current["State"]
        if not state.get("Running"):
            raise ControlError("Шлюз завершился после запуска. Бот не запущен.")
        health = state.get("Health", {}).get("Status")
        if health == "healthy":
            return
        if health == "unhealthy":
            raise ControlError("Шлюз сообщает unhealthy. Бот не запущен.")
        if health is None:
            raise ControlError("У шлюза нет Docker healthcheck. Бот не запущен.")
        time.sleep(1)
    raise ControlError("Шлюз не стал healthy. Бот не запущен.")


def trial_state(root):
    return root / "miniapp-trial" / "active.json"


def trial_occupied(root):
    if trial_state(root).exists():
        return True
    # Даже без файла восстановления не допускаем второго polling рядом с тестовым.
    raw = updater.docker("inspect", "noclip-miniapp-trial-bot", optional=True)
    if raw:
        item = json.loads(raw)[0]
        labels = item.get("Config", {}).get("Labels") or {}
        if labels.get("io.noclip.owner-trial") == "1" and running(item):
            return True
    return False


def start_services(root):
    require_docker()
    if trial_occupied(root):
        raise ControlError("Mini App активна или требует Stop. Старый polling не запускаю.")
    current = containers()
    if not current["shluz"]:
        raise ControlError("Шлюз не найден. Выберите безопасную установку/обновление.")
    settings = settings_for(root, current)
    check_volumes(settings, current)
    gateway = current["shluz"]
    if not running(gateway):
        updater.docker("start", gateway["Id"])
    wait_healthy(gateway["Id"])
    bot = current["telegram_bot"]
    if bot:
        if not running(bot):
            updater.docker("start", bot["Id"])
        print("Gateway и Telegram-бот запущены.")
    else:
        print("Gateway запущен. Telegram-бот ещё не настроен.")


def stop_miniapp(root):
    if not trial_state(root).exists():
        if trial_occupied(root):
            raise ControlError("Нет файла восстановления Mini App. Нужна диагностика.")
        print("Mini App не активна.")
        return
    script = Path(__file__).with_name("miniapp_trial.py")
    environment = {**os.environ, "SHLUZ_HOME": str(root)}
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--stop"],
            cwd=script.parent,
            env=environment,
            capture_output=True,
            timeout=100,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ControlError("Не удалось завершить Mini App. Старый бот не трогаю.") from exc
    if result.returncode:
        raise ControlError("Остановка Mini App завершилась ошибкой. Не трогайте Docker volumes.")
    for _ in range(70):
        if not trial_state(root).exists():
            print("Mini App остановлена; исходный бот восстановлен.")
            return
        time.sleep(1)
    raise ControlError("Mini App ещё восстанавливается. Шлюз и бот не изменены.")


def stop_services(root):
    require_docker()
    if trial_occupied(root):
        stop_miniapp(root)
    current = containers()
    if current["shluz"]:
        settings = settings_for(root, current)
        check_volumes(settings, current)
    for service in ("telegram_bot", "shluz"):
        item = current[service]
        if running(item):
            updater.docker("stop", "--time", "25", item["Id"], timeout=55)
    print("Gateway и Telegram остановлены. Контейнеры и тома сохранены.")


def backup_services(root):
    require_docker()
    if trial_occupied(root):
        raise ControlError("Для согласованного backup сначала остановите Mini App.")
    current = containers()
    if not current["shluz"]:
        raise ControlError("Не найден действующий шлюз для резервной копии.")
    settings = settings_for(root, current)
    required = check_volumes(settings, current)
    image = current["shluz"]["Image"]
    if not updater.docker("image", "inspect", image, optional=True):
        raise ControlError("Нет Docker-образа для безопасного создания backup.")
    previous = [(service, current[service]) for service in ("shluz", "telegram_bot")
                if running(current[service])]
    location = None
    error = None
    try:
        location = updater.backup_volumes(
            root, settings, image, [current["telegram_bot"], current["shluz"]]
        )
        for kind in required:
            if not (location / kind / "data.tar.gz").is_file():
                raise ControlError("Копия необходимого Docker volume не создана.")
        if not (location / "backup.json").is_file() or not (location / "config").is_dir():
            raise ControlError("Резервная копия неполная.")
    except Exception as exc:
        error = exc
    finally:
        # Возвращаем только то, что работало до backup. Никогда не пересоздаём.
        for _service, item in previous:
            inspected = json.loads(updater.docker("inspect", item["Id"]))[0]
            if not running(inspected):
                updater.docker("start", item["Id"])
    if error:
        if isinstance(error, (ControlError, updater.UpdateError)):
            raise error
        raise ControlError("Не удалось завершить backup. Исходные файлы сохранены.") from error
    print("Проверенная резервная копия:", location)
    return location


def start_miniapp(root):
    require_docker()
    if trial_occupied(root):
        raise ControlError("Mini App уже активна. Используйте статус или Stop.")
    if not (root / "trial-manifest.json").is_file():
        raise ControlError("Нет manifest проверенного пакета Mini App.")
    current = containers()
    if not running(current["shluz"]) or not running(current["telegram_bot"]):
        raise ControlError("Сначала запустите шлюз и обычного Telegram-бота.")
    settings = settings_for(root, current)
    check_volumes(settings, current)
    if os.name != "nt":
        raise ControlError("Для отдельного окна Mini App требуется Windows.")
    script = Path(__file__).with_name("miniapp_trial.py")
    try:
        subprocess.Popen(
            [sys.executable, str(script)],
            cwd=script.parent,
            env={**os.environ, "SHLUZ_HOME": str(root)},
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    except OSError as exc:
        raise ControlError("Не удалось открыть отдельное окно Mini App.") from exc
    print("Проверка Mini App запущена в отдельном окне. Её готовность — в этом окне.")


def update_services(root):
    # Держим lock на предохранительных проверках и предварительном backup;
    # updater.main затем самостоятельно блокирует собственную миграцию.
    with updater.update_lock(root):
        require_docker()
        if trial_occupied(root):
            raise ControlError("Сначала безопасно остановите Mini App.")
        current = containers()
        if current["shluz"]:
            backup_services(root)
        elif any((root / name).exists() for name in (
            "installed.json", "runtime.json", "config/.env.owner"
        )):
            raise ControlError(
                "Найдены данные без контейнера шлюза. "
                "Автоматическую установку поверх неизвестной базы запрещаю."
            )
    result = updater.main()
    if result:
        raise ControlError("Обновление не завершилось. Старые volumes сохранены.")


def status(root):
    print("\nPortable:", root)
    installed = root / "installed.json"
    if installed.is_file():
        data = json.loads(installed.read_text(encoding="utf-8"))
        print("Версия:", str(data.get("commit", "неизвестна"))[:12])
    else:
        print("Версия: ещё не зарегистрирована")
    if (root / "migration.json").exists():
        print("AppData: копия перенесена и проверена; источник сохранён")
    elif legacy_home().exists():
        print("AppData: найден старый каталог, перенос ещё не подтверждён")
    try:
        require_docker()
        for service, item in containers().items():
            if not item:
                label = "нет контейнера"
            elif not running(item):
                label = "остановлен"
            else:
                health = item["State"].get("Health", {}).get("Status", "без health")
                label = "запущен / " + str(health)
            print(service + ":", label)
        present = trial_occupied(root) or trial_state(legacy_home()).exists()
        print("Mini App:", "работает / требуется Stop" if present else "не активна")
    except (ControlError, updater.UpdateError):
        print("Docker: недоступен или конфигурация требует проверки")
        print("Mini App:", "есть файл восстановления" if trial_state(root).exists()
              else "неизвестно")
    backups = root / "backups"
    print("Backups:", sum(1 for p in backups.iterdir() if p.is_dir())
          if backups.exists() else 0)


ACTIONS = {
    "1": "status",
    "2": "start",
    "3": "stop",
    "4": "backup",
    "5": "miniapp-start",
    "6": "miniapp-stop",
    "7": "update",
    "8": "migrate",
}


def action(command, root):
    if command == "status":
        status(root)
        return
    if command == "miniapp-stop":
        # Работает также для ещё не перенесённой старой AppData-проверки.
        with updater.update_lock(root):
            require_docker()
            target = legacy_home() if (
                not trial_state(root).exists() and trial_state(legacy_home()).exists()
            ) else root
            stop_miniapp(target)
        return
    if command == "update":
        with updater.update_lock(root):
            copied = migrate_legacy(root)
            if copied:
                print("Проверено и скопировано файлов из AppData:", copied)
        update_services(root)  # Свой lock внутри updater.main, backup без удаления.
        return
    with updater.update_lock(root):
        copied = migrate_legacy(root)
        if command == "migrate":
            print("Проверка переноса завершена. Новых файлов:", copied)
            return
        if copied:
            print("Проверено и скопировано файлов из AppData:", copied)
        if command == "start":
            start_services(root)
        elif command == "stop":
            stop_services(root)
        elif command == "backup":
            backup_services(root)
        elif command == "miniapp-start":
            start_miniapp(root)
        else:
            raise ControlError("Неизвестная операция.")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    root = portable_home()
    os.environ["SHLUZ_HOME"] = str(root)
    root.mkdir(parents=True, exist_ok=True)
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv:
            action(argv[0], root)
            return 0
        while True:
            print(
                "\nNoClip / SHLUZ — portable control\n"
                "1. Статус\n2. Запустить Gateway + Telegram\n"
                "3. Остановить Gateway + Telegram\n4. Резервная копия\n"
                "5. Запустить Telegram Mini App (отдельное окно)\n"
                "6. Остановить Mini App, вернуть классического бота\n"
                "7. Установить / обновить проверенный шлюз (с backup)\n"
                "8. Проверить / перенести AppData\n0. Выход"
            )
            choice = input("Действие: ").strip()
            if choice == "0":
                return 0
            if choice not in ACTIONS:
                print("Выберите число из меню.")
                continue
            try:
                action(ACTIONS[choice], root)
            except (ControlError, updater.UpdateError, OSError, ValueError, KeyError) as exc:
                message = str(exc) if isinstance(exc, (ControlError, updater.UpdateError)) else (
                    "Операция не завершена. Данные и Docker volumes не удалялись."
                )
                print("Ошибка:", message)
    except (ControlError, updater.UpdateError, OSError, ValueError, KeyError) as exc:
        message = str(exc) if isinstance(exc, (ControlError, updater.UpdateError)) else (
            "Операция остановлена для сохранности данных."
        )
        print("Ошибка:", message)
        return 1
    except (KeyboardInterrupt, EOFError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
