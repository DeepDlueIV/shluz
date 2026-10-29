"""Подпись Telegram Mini App; секреты не покидают процесс бота."""

import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qsl

from pydantic import SecretStr


def validate_init_data(raw: str, bot_token: str, *, now: float | None = None) -> dict:
    if not isinstance(raw, str) or not raw or len(raw.encode()) > 16384:
        raise ValueError("Invalid Telegram authorization")
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=32)
        values = dict(pairs)
        if len(values) != len(pairs) or any("\n" in key for key in values):
            raise ValueError("Duplicate fields")
        received = values.pop("hash")
        if not re.fullmatch(r"[a-f0-9]{64}", received):
            raise ValueError("Invalid hash")
        check = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received):
            raise ValueError("Invalid signature")
        issued = int(values["auth_date"])
        age = (time.time() if now is None else now) - issued
        if not -60 <= age <= 300:
            raise ValueError("Expired authorization")
        user = json.loads(values["user"])
        if (
            not isinstance(user, dict)
            or type(user.get("id")) is not int
            or not 0 < user["id"] < 2**52
            or user.get("is_bot", False)
        ):
            raise ValueError("Invalid user")
        if not isinstance(user.get("first_name"), str):
            raise ValueError("Invalid user name")
        # Только нужные проверенные поля; аватар/произвольные URLs не подгружаются.
        return {
            "id": user["id"],
            "first_name": user["first_name"][:200],
            "username": user.get("username") if isinstance(user.get("username"), str) else None,
        }
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("Invalid Telegram authorization") from exc


@dataclass
class PanelSession:
    user: dict
    account_id: str
    gateway_token: SecretStr
    expires: float
    confirmation: tuple | None = None
    results: dict = field(default_factory=dict)


class Sessions:
    """Короткие bearer-сессии: TTL не продлевается повторным initData."""

    def __init__(self, *, ttl=900, capacity=1024):
        self.ttl, self.capacity = ttl, capacity
        self.items: dict[str, PanelSession] = {}
        self.exchanges: dict[str, str] = {}

    def prune(self):
        now = time.monotonic()
        self.items = {key: value for key, value in self.items.items() if value.expires > now}
        self.exchanges = {key: val for key, val in self.exchanges.items() if val in self.items}

    def create(self, raw, user, account_id, gateway_token):
        self.prune()
        digest = hashlib.sha256(raw.encode()).hexdigest()
        token = self.exchanges.get(digest)
        if token:
            return token, self.items[token]
        if len(self.items) >= self.capacity:
            raise ValueError("Session capacity exceeded")
        token = secrets.token_urlsafe(32)
        session = PanelSession(user, account_id, gateway_token, time.monotonic() + self.ttl)
        self.items[token] = session
        self.exchanges[digest] = token
        return token, session

    def get(self, token):
        self.prune()
        return self.items.get(token)
