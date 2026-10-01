"""Проверка живого event loop бота, а не чужого HTTP-порта шлюза."""

import json
import os
import time
from pathlib import Path


def heartbeat_path(database_path: Path) -> Path:
    return database_path.with_suffix(".heartbeat.json")


def write_heartbeat(path: Path) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"pid": os.getpid(), "updated_at": time.time()}), encoding="utf-8"
    )
    temporary.chmod(0o600)
    temporary.replace(path)


def is_healthy(path: Path, max_age: float = 90) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        age = time.time() - float(data["updated_at"])
        if type(data["pid"]) is not int or data["pid"] <= 0 or not 0 <= age <= max_age:
            return False
        os.kill(data["pid"], 0)
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False


if __name__ == "__main__":
    database = Path(os.environ.get("TELEGRAM_DATABASE_PATH", "data/telegram.sqlite"))
    raise SystemExit(0 if is_healthy(heartbeat_path(database)) else 1)
