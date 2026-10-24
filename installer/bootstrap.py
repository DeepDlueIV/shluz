"""NoClip one-click code updater for the existing Windows owner installation.

Installer runs from an extracted, immutable ZIP. It updates ONLY listed program files
under the existing Windows installation, never Docker volumes, secrets or backups.
Source revisions and file SHA256 values are checked before any mutation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import webbrowser
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

PRODUCT = "noclip-shluz-local-update"
ROOT = Path(r"D:\NoClip\Shluz")
CONTROL = ".noclip-installer"
PROJECT = "shluz-owner-check"
GATEWAY_SHA = "c47ff5b8acc4a7b24b8f34b19e3a9d024aba0fea"
FILES = frozenset(
    {
        "NoClip.cmd",
        "Stop-NoClip.cmd",
        "Status-NoClip.cmd",
        "Backup-NoClip.cmd",
        "Start-Website.cmd",
        "Stop-Website.cmd",
        "launcher/updater.py",
        "launcher/miniapp_trial.py",
        "launcher/miniapp_live.py",
        "launcher/site_bridge.py",
        "web/tunnel/start-windows.ps1",
    }
)
CHECKSUM = re.compile(r"[a-f0-9]{64}\Z")
REVISION = re.compile(r"[a-f0-9]{40}\Z")


class InstallError(RuntimeError):
    pass


def now():
    return datetime.now(UTC).isoformat()


def is_link(path):
    path = Path(path)
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def put_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent,
        prefix=".noclip-json-", delete=False,
    ) as stream:
        tmp = Path(stream.name)
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def safe_path(root, relative):
    if relative not in FILES:
        raise InstallError("Архив содержит неподдерживаемый путь программы.")
    root = Path(root).resolve()
    target = root / relative
    current = target
    while current != root:
        if is_link(current):
            raise InstallError("Обнаружен symlink/junction: установка остановлена.")
        current = current.parent
        if root not in current.parents and current != root:
            raise InstallError("Путь файла выходит за каталог SHLUZ.")
    if target.exists() and not target.is_file():
        raise InstallError("Управляемый файл занят каталогом.")
    return target


def read_package(source):
    source = Path(source).resolve()
    path = source / "package-manifest.json"
    if is_link(path) or not path.is_file():
        raise InstallError("Не найден manifest проверенной сборки.")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InstallError("Manifest повреждён.") from exc
    if not isinstance(manifest, dict) or (
        manifest.get("schema") != 1
        or manifest.get("product") != PRODUCT
        or not isinstance(manifest.get("revision"), str)
        or not REVISION.fullmatch(manifest["revision"])
        or manifest.get("gateway_revision") != GATEWAY_SHA
        or manifest.get("mode") != "launcher-code-only"
        or not isinstance(manifest.get("files"), dict)
        or set(manifest["files"]) != FILES
    ):
        raise InstallError("Manifest не соответствует проверенной локальной сборке.")
    payload = source / "payload"
    if is_link(payload) or not payload.is_dir():
        raise InstallError("Не найдена папка с программой.")
    for relative, expected in manifest["files"].items():
        if not isinstance(expected, str) or not CHECKSUM.fullmatch(expected):
            raise InstallError("Неверная контрольная сумма программы.")
        file = safe_path(payload, relative)
        if not file.is_file() or file.stat().st_size > 2 * 1024 * 1024:
            raise InstallError("Отсутствует или слишком большой файл программы.")
        if sha256(file) != expected:
            raise InstallError("Архив не прошёл SHA-256; ничего не устанавливалось.")
    return manifest


def install_root():
    # Тестовый override не используется стандартным Windows CMD-лаунчером.
    return Path(os.environ.get("NOCLIP_TEST_INSTALL_ROOT", str(ROOT))).resolve()


@contextmanager
def runtime_lock(root):
    """Same cross-process lock as launcher/updater.py: never overwrite running update."""
    path = Path(root) / ".update.lock"
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            if stream.seek(0, 2) == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise InstallError("Сейчас уже выполняется операция SHLUZ.") from exc
        else:
            import fcntl

            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise InstallError("Сейчас уже выполняется операция SHLUZ.") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def existing_install(root):
    root = Path(root)
    if is_link(root):
        raise InstallError("Каталог SHLUZ является ссылкой; установка остановлена.")
    if not root.is_dir():
        raise InstallError(
            "Рабочая папка D:\\NoClip\\Shluz не найдена. "
            "Никакие данные или Docker volumes не изменены."
        )
    if not (root / "NoClip.cmd").is_file() or not (root / "launcher").is_dir():
        raise InstallError(
            "В D:\\NoClip\\Shluz не найдены NoClip.cmd и launcher. "
            "Пустую установку или новую базу автоматически не создаю."
        )
    for folder in ("launcher", CONTROL, ".git"):
        if is_link(root / folder):
            raise InstallError("Рабочий каталог содержит неподдерживаемую ссылку.")
    for relative in FILES:
        safe_path(root, relative)


def git_checkout_preflight(root, previous_state, manifest):
    """Allow an existing Git checkout but never overwrite Codex's uncommitted edits.

    Installed code and Git remain distinct concerns. The .git directory is never
    accessed for writing. Existing tracked local edits are checked before replacing
    a launcher file; updates installed by us are checked via current.json instead.
    """
    root = Path(root).resolve()
    marker = root / ".git"
    if not marker.exists():
        return False
    if is_link(marker) or not (marker.is_dir() or marker.is_file()):
        raise InstallError("Нестандартная папка Git: установка не изменена.")

    def run_git(*arguments):
        try:
            result = subprocess.run(
                ["git", "-C", str(root), *arguments],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=20, check=False,
                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InstallError(
                "Обнаружен Git, но не удалось безопасно проверить его локальные правки."
            ) from exc
        if result.returncode:
            raise InstallError(
                "Git-копия не прошла проверку. Файлы программы сохранены."
            )
        return result.stdout

    top = run_git("rev-parse", "--show-toplevel").strip()
    if os.path.normcase(str(Path(top).resolve())) != os.path.normcase(str(root)):
        raise InstallError("SHLUZ расположен внутри другого Git-проекта.")
    if previous_state is not None:
        # User edits are already caught by ensure_local_changes_safe using SHA-256.
        return True

    # --no-renames and -z produce literal paths; exclude unrelated project edits.
    dirty = run_git(
        "-c", "status.renames=false", "status", "--porcelain=v1", "-z",
        "--untracked-files=no", "--", *sorted(FILES),
    )
    for entry in dirty.split("\x00"):
        if not entry:
            continue
        relative = entry[3:]
        if relative not in FILES:
            raise InstallError("Git сообщил о неизвестной локальной правке.")
        current = safe_path(root, relative)
        if not current.is_file() or sha256(current) != manifest["files"][relative]:
            raise InstallError(
                "Обнаружены локальные правки Codex в файлах лаунчера. "
                "Установщик их не перезаписывает; сначала сохраните их в Git."
            )
    return True


def docker_command(*args):
    try:
        result = subprocess.run(
            ["docker", *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallError("Docker Desktop не отвечает. Данные не менялись.") from exc
    if result.returncode:
        raise InstallError("Docker не отвечает или отсутствует управляемый контейнер.")
    return result.stdout


def docker_preflight(root):
    """Read-only identity checks; never print env, image inspect or credentials."""
    platform = docker_command("info", "--format", "{{.OSType}}/{{.Architecture}}").strip()
    if platform not in {"linux/amd64", "linux/x86_64"}:
        raise InstallError("Для NoClip требуется Docker Desktop Linux/x64.")
    state_file = Path(root) / "runtime.json"
    if is_link(state_file):
        raise InstallError("Неподдерживаемая ссылка на конфигурацию.")
    settings = {}
    if state_file.is_file():
        try:
            settings = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise InstallError("Конфигурация прежнего шлюза повреждена.") from exc
        if settings.get("project") != PROJECT:
            raise InstallError("Неверный Docker-проект в runtime.json.")
    volume_names = (settings.get("volumes") or {}) if settings else {}
    for service, kind in (("shluz", "gateway"), ("telegram_bot", "bot")):
        ids = docker_command(
            "ps", "-aq",
            "--filter", f"label=com.docker.compose.project={PROJECT}",
            "--filter", f"label=com.docker.compose.service={service}",
        ).split()
        if len(ids) != 1:
            raise InstallError("Неоднозначный или отсутствующий Docker-контейнер SHLUZ.")
        item = json.loads(docker_command("inspect", ids[0]))[0]
        labels = item.get("Config", {}).get("Labels") or {}
        if (labels.get("com.docker.compose.project"),
                labels.get("com.docker.compose.service")) != (PROJECT, service):
            raise InstallError("Подмена контейнера SHLUZ; обновление остановлено.")
        mounts = [
            mount for mount in item.get("Mounts", [])
            if mount.get("Destination") == "/app/data"
        ]
        if len(mounts) != 1 or mounts[0].get("Type") != "volume":
            raise InstallError("Нестандартная база SHLUZ; новый том не создаю.")
        expected = volume_names.get(kind)
        if expected is not None and expected != mounts[0].get("Name"):
            raise InstallError("Имя постоянного Docker volume изменилось.")
    return True


def load_state(root):
    file = Path(root) / CONTROL / "current.json"
    if is_link(file):
        raise InstallError("Недопустимая ссылка в состоянии установщика.")
    if not file.exists():
        return None
    try:
        state = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InstallError("Состояние прежнего обновления повреждено.") from exc
    if state.get("product") != PRODUCT or not REVISION.fullmatch(
        str(state.get("revision", ""))
    ):
        raise InstallError("Состояние прежней установки не распознано.")
    if not isinstance(state.get("files"), dict) or set(state["files"]) != FILES:
        raise InstallError("Состояние файлов не совпало.")
    return state


def ensure_local_changes_safe(root, state):
    if not state:
        return
    for relative, expected in state["files"].items():
        file = safe_path(root, relative)
        if not file.exists() or sha256(file) != expected:
            raise InstallError(
                "Есть локальные изменения программы. Они не перезаписаны. "
                "Сохраните их в Git через Codex перед обновлением."
            )


def take_snapshot(root, folder, manifest, previous_state):
    before = {}
    data = Path(folder) / "before"
    for relative in FILES:
        src = safe_path(root, relative)
        if not src.exists():
            before[relative] = None
            continue
        digest = sha256(src)
        before[relative] = digest
        dest = data / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        if sha256(dest) != digest:
            raise InstallError("Не удалось проверить копию исходной программы.")
    snapshot = {
        "product": PRODUCT,
        "schema": 1,
        "before": before,
        "candidate": manifest["files"],
        "revision": manifest["revision"],
        "created_at": now(),
        "previous_state": previous_state,
    }
    put_json(Path(folder) / "snapshot.json", snapshot)
    return snapshot


def restore_files(root, folder, snapshot):
    for relative in FILES:
        target = safe_path(root, relative)
        candidate = snapshot["candidate"][relative]
        before = snapshot["before"][relative]
        if target.exists():
            digest = sha256(target)
            if digest not in {before, candidate}:
                raise InstallError(
                    "Файл изменился во время обновления. Ничего не затираю; "
                    "папка восстановления сохранена."
                )
        if before is None:
            if target.exists() and sha256(target) == candidate:
                target.unlink()
            continue
        source = Path(folder) / "before" / relative
        if not source.is_file() or sha256(source) != before:
            raise InstallError("Копия прежней версии повреждена, автоматический откат запрещён.")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
        try:
            shutil.copyfile(source, temporary)
            if sha256(temporary) != before:
                raise InstallError("Отказ проверки восстановленного кода.")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def restore_installed_state(root, snapshot):
    state = Path(root) / CONTROL / "current.json"
    previous = snapshot.get("previous_state")
    if previous is None:
        state.unlink(missing_ok=True)
    else:
        put_json(state, previous)


def prune_managed_history(root, keep=2):
    """Remove only older, checksum-verified code snapshots created by this installer."""
    history = Path(root) / CONTROL / "history"
    if not history.is_dir() or is_link(history):
        return
    snapshots = sorted((p for p in history.iterdir() if p.is_dir()), reverse=True)
    for folder in snapshots[keep:]:
        if is_link(folder):
            continue
        record = folder / "snapshot.json"
        if is_link(record) or not record.is_file():
            continue
        try:
            meta = json.loads(record.read_text(encoding="utf-8"))
            if meta.get("product") != PRODUCT or set(meta["before"]) != FILES:
                continue
            # If a developer added files inside the directory, preserve it.
            allowed = {"snapshot.json"} | {
                str(Path("before") / p) for p in FILES if meta["before"][p]
            }
            actual = {
                str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()
            }
            if actual != allowed or any(is_link(p) for p in folder.rglob("*")):
                continue
            if any(
                sha256(folder / "before" / p) != digest
                for p, digest in meta["before"].items() if digest
            ):
                continue
            shutil.rmtree(folder)
        except (OSError, ValueError, TypeError, KeyError):
            # Housekeeping cannot prevent a working update.
            pass


def recover_interrupted(root):
    journal_file = Path(root) / CONTROL / "journal.json"
    if not journal_file.exists():
        return
    if is_link(journal_file):
        raise InstallError("Неподдерживаемая ссылка на журнал установки.")
    try:
        journal = json.loads(journal_file.read_text(encoding="utf-8"))
        folder = Path(root) / CONTROL / "work" / journal["operation"]
        if not re.fullmatch(r"[a-f0-9]{32}", journal["operation"]):
            raise ValueError("operation")
        snapshot = json.loads((folder / "snapshot.json").read_text(encoding="utf-8"))
        if snapshot.get("product") != PRODUCT or set(snapshot["before"]) != FILES:
            raise ValueError("snapshot")
        if set(snapshot["candidate"]) != FILES:
            raise ValueError("candidate")
        state = load_state(root)
        completed = (
            state is not None
            and state["revision"] == snapshot["revision"]
            and state["files"] == snapshot["candidate"]
            and all(
                safe_path(root, name).is_file()
                and sha256(safe_path(root, name)) == digest
                for name, digest in snapshot["candidate"].items()
            )
        )
        if completed:
            # Commit marker is only written after successful NoClip.cmd launch.
            journal_file.unlink()
            print("Проверенная установка восстановлена после прерывания.")
            return
        restore_files(root, folder, snapshot)
        restore_installed_state(root, snapshot)
        journal_file.unlink()
        print("Незавершённое обновление откатилось к предыдущему коду.")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise InstallError(
            "Обнаружено незавершённое обновление. Автоматическое продолжение "
            "запрещено; файлы для восстановления сохранены."
        ) from exc


def write_candidate(root, source, manifest):
    for relative, digest in sorted(manifest["files"].items()):
        target = safe_path(root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=".noclip-new-", delete=False
        ) as stream:
            temporary = Path(stream.name)
        try:
            shutil.copyfile(Path(source) / "payload" / relative, temporary)
            if sha256(temporary) != digest:
                raise InstallError("Ошибка проверки временного файла.")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    if any(sha256(safe_path(root, p)) != h for p, h in manifest["files"].items()):
        raise InstallError("Проверка установленной программы не прошла.")


def commit_snapshot(root, operation, manifest, snapshot):
    control = Path(root) / CONTROL
    # Write marker only AFTER successful NoClip startup, with journal still
    # available to make a crash between these operations recoverable.
    put_json(control / "current.json", {
        "product": PRODUCT,
        "revision": manifest["revision"],
        "gateway_revision": manifest["gateway_revision"],
        "files": manifest["files"],
        "installed_at": now(),
    })
    (control / "journal.json").unlink(missing_ok=True)
    history = control / "history"
    history.mkdir(parents=True, exist_ok=True)
    item = history / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + operation
    )
    # Clean up only installer-owned, verified old CODE snapshots: keep latest two.
    (control / "work" / operation).rename(item)
    prune_managed_history(root, keep=2)
    return item


def run_existing(root):
    if os.name != "nt":
        raise InstallError("Однокликовый запуск поддерживает Windows.")
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", str(Path(root) / "NoClip.cmd")],
        cwd=root, capture_output=False, timeout=900, check=False,
    )
    if result.returncode:
        raise InstallError(
            "Новая версия не прошла локальный запуск. Старые данные сохранены."
        )


def install(source, root, *, docker_check=docker_preflight, launch=run_existing):
    source = Path(source).resolve()
    root = Path(root).resolve()
    manifest = read_package(source)
    existing_install(root)
    control = root / CONTROL
    control.mkdir(parents=True, exist_ok=True)
    # An owner may have cloned Git into the same folder. Keep snapshots and any
    # future runtime secrets invisible to git add -A, even with an older root
    # .gitignore. This does not touch .git or the Git commit history.
    local_ignore = control / ".gitignore"
    if is_link(local_ignore):
        raise InstallError("Служебный файл .gitignore является ссылкой.")
    if not local_ignore.exists():
        try:
            with local_ignore.open("x", encoding="utf-8") as stream:
                stream.write("*\\n!.gitignore\\n")
        except FileExistsError:
            pass
    with runtime_lock(root):
        recover_interrupted(root)
        state = load_state(root)
        ensure_local_changes_safe(root, state)
        git_checkout = git_checkout_preflight(root, state, manifest)
        docker_check(root)
        if state and state["revision"] == manifest["revision"] and all(
            sha256(safe_path(root, p)) == h for p, h in manifest["files"].items()
        ):
            print("Эта версия уже установлена. Данные и настройки не изменены.")
            changed = False
            operation = None
            snapshot = None
        else:
            required = sum(
                (source / "payload" / p).stat().st_size for p in FILES
            ) + sum(
                safe_path(root, p).stat().st_size
                for p in FILES if safe_path(root, p).exists()
            )
            if shutil.disk_usage(root).free < required * 3 + 64 * 1024**2:
                raise InstallError("Недостаточно свободного места для безопасного обновления.")
            operation = uuid.uuid4().hex
            work = control / "work" / operation
            work.mkdir(parents=True, exist_ok=False)
            snapshot = take_snapshot(root, work, manifest, state)
            put_json(control / "journal.json", {"operation": operation})
            try:
                write_candidate(root, source, manifest)
            except Exception:
                restore_files(root, work, snapshot)
                restore_installed_state(root, snapshot)
                (control / "journal.json").unlink(missing_ok=True)
                raise
            changed = True
            print("Код подготовлен. Конфигурация и Docker volumes не изменялись.")
    # Release cross-process lock BEFORE starting NoClip.cmd, which takes the same lock.
    try:
        launch(root)
    except Exception:
        if changed:
            with runtime_lock(root):
                restore_files(root, work, snapshot)
                restore_installed_state(root, snapshot)
                (control / "journal.json").unlink(missing_ok=True)
            print("Код прежней версии восстановлен; пользовательские базы не затронуты.")
        raise
    if changed:
        with runtime_lock(root):
            commit_snapshot(root, operation, manifest, snapshot)
        print("NoClip обновлён до " + manifest["revision"][:12] + ".")
    if git_checkout and changed:
        print(
            "Git-история не менялась. Обновлённые файлы программы могут "
            "отображаться как изменения Git; не выполняйте git add -A "
            "без проверки в Codex."
        )
    if os.name == "nt":
        webbrowser.open("http://127.0.0.1:18000/admin")
    return 0


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        root = install_root()
        return install(Path(__file__).parent, root)
    except (InstallError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        message = str(exc) if isinstance(exc, InstallError) else (
            "Установка не завершена, прежние файлы и данные не удалены."
        )
        print("NoClip: " + message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
