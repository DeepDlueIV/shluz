"""Узкий служебный ключ и короткие сессии. Ни BotFather, ни admin-ключ здесь не нужны."""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from contextlib import suppress
from pathlib import Path

SESSION_PREFIX = "shluz_tg."
SESSION_TTL = 900
_KEY = re.compile(r"shluz_connector_[A-Za-z0-9_-]{43}\Z")


def read_connector_key(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        if path.is_symlink() or path.stat().st_size > 128:
            return None
        key = path.read_text(encoding="ascii").strip()
        return key if _KEY.fullmatch(key) else None
    except (OSError, UnicodeError):
        return None


def ensure_connector_key(path: Path) -> None:
    """Публикуем полностью записанный файл; параллельный запуск не меняет ключ."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.parent / (".key-" + secrets.token_hex(12))
    if not path.exists():
        try:
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as stream:
                stream.write("shluz_connector_" + secrets.token_urlsafe(32))
                stream.flush()
                os.fsync(stream.fileno())
            with suppress(FileExistsError):
                os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    if read_connector_key(path) is None:
        raise RuntimeError("Служебный ключ Telegram повреждён или недоступен")


def issue_session(key: str, admission_id: str, telegram_id: str, version: int) -> str:
    payload = json.dumps(
        {
            "access": admission_id,
            "sub": telegram_id,
            "version": version,
            "exp": int(time.time()) + SESSION_TTL,
        },
        separators=(",", ":"),
    ).encode("ascii")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = hmac.new(
        key.encode("ascii"),
        ("shluz-telegram-session-v1:" + encoded).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return SESSION_PREFIX + encoded + "." + signature


def decode_session(token: str, key: str | None) -> dict | None:
    if not key or not token.startswith(SESSION_PREFIX) or len(token) > 1024:
        return None
    try:
        encoded, signature = token[len(SESSION_PREFIX) :].split(".")
        expected = hmac.new(
            key.encode("ascii"),
            ("shluz-telegram-session-v1:" + encoded).encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature.encode("ascii"), expected.encode("ascii")):
            return None
        payload = json.loads(
            base64.b64decode(
                encoded + "=" * (-len(encoded) % 4),
                altchars=b"-_",
                validate=True,
            )
        )
        if not isinstance(payload, dict):
            return None
        if (
            type(payload.get("exp")) is not int
            or not time.time() < payload["exp"] <= time.time() + SESSION_TTL + 1
            or type(payload.get("version")) is not int
            or not isinstance(payload.get("access"), str)
            or not isinstance(payload.get("sub"), str)
        ):
            return None
        return payload
    except (ValueError, TypeError, UnicodeError):
        return None
