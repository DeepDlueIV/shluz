"""Приёмка тестового шлюза. Только стандартная библиотека; ключи не выводятся в лог."""

import argparse
import getpass
import json
import os
import secrets
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Не пересылаем персональный токен по чужому Location.


def prepare(root: Path) -> Path:
    path = root / ".env.owner"
    content = (
        "# Только локальная приёмка. Не заменять рабочий .env этим файлом.\n"
        "SHLUZ_ENVIRONMENT=development\n"
        "SHLUZ_ADMIN_USERNAME=owner\n"
        f"SHLUZ_ADMIN_PASSWORD={secrets.token_urlsafe(32)}\n"
        f"SHLUZ_BOOTSTRAP_API_TOKEN={secrets.token_urlsafe(40)}\n"
        "SHLUZ_DATABASE_URL=sqlite+pysqlite:///./data/shluz.db\n"
        "SHLUZ_ACTIVE_PROVIDER=mock\n"
        "SHLUZ_VENICE_API_KEY=\n"
        "SHLUZ_CREDIT_UNIT_USD=0.001\n"
    )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)
    return path


def request_json(base: str, method: str, path: str, token=None, payload=None) -> tuple[int, dict]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(base.rstrip("/") + path, data=body, headers=headers, method=method)
    try:
        try:
            response = urllib.request.build_opener(NoRedirect).open(req, timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            status = response.code
            raw = response.read(1048577)
        if len(raw) > 1048576:
            return status, {}
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            result = {}
        return status, result if isinstance(result, dict) else {}
    except (OSError, urllib.error.URLError, TimeoutError):
        return 0, {}


def check_gateway(base: str, token: str, *, send_mock=False, request=request_json) -> list[dict]:
    checks = []

    def record(name, ok, status):
        checks.append({"check": name, "ok": bool(ok), "http": status})
        return bool(ok)

    code, data = request(base, "GET", "/health")
    if not record("Шлюз отвечает /health", code == 200 and data.get("status") == "ok", code):
        return checks
    code, _ = request(base, "GET", "/admin")
    record("Админка требует авторизацию", code == 401, code)
    for auth in (None, "invalid-owner-check"):
        code, _ = request(base, "GET", "/v1/models", auth)
        record("API не принимает отсутствующий/неверный ключ", code == 401, code)
    code, data = request(base, "GET", "/v1/models", token)
    rows = data.get("data")
    if not record(
        "Личный ключ получает список моделей", code == 200 and isinstance(rows, list), code
    ):
        return checks
    models = [row.get("id") for row in rows if isinstance(row, dict)]
    code, profile = request(base, "GET", "/v1/me", token)
    if not record(
        "Личный профиль доступен (не bootstrap)",
        code == 200 and isinstance(profile.get("account"), dict),
        code,
    ):
        return checks
    if not send_mock:
        return checks
    if not record("Для генерации выбрана только mock-chat", "mock-chat" in models, 0):
        return checks
    if not record("Тестовому аккаунту назначен тариф", isinstance(profile.get("plan"), dict), 0):
        return checks
    # При подозрительной проверке доступа не выполняем даже тестовую генерацию.
    if any(not check["ok"] for check in checks):
        return checks
    before = profile.get("allowance", {}).get("requests", {}).get("used")
    code, result = request(
        base,
        "POST",
        "/v1/chat/completions",
        token,
        {
            "model": "mock-chat",
            "messages": [{"role": "user", "content": "owner-check"}],
            "stream": False,
        },
    )
    try:
        answer = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        answer = None
    if not record(
        "Получен точный ответ mock", code == 200 and answer == "Shluz mock reply: owner-check", code
    ):
        return checks
    code, profile = request(base, "GET", "/v1/me", token)
    after = profile.get("allowance", {}).get("requests", {}).get("used")
    record(
        "Учтён ровно один запрос",
        code == 200 and isinstance(before, int) and after == before + 1,
        code,
    )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Безопасная приёмка тестового Shluz")
    parser.add_argument(
        "--prepare", action="store_true", help="Создать только .env.owner без перезаписи"
    )
    parser.add_argument("--url", default="http://127.0.0.1:18000", help="Адрес тестового шлюза")
    parser.add_argument(
        "--send-mock", action="store_true", help="Разрешить один mock-запрос (тратит квоту)"
    )
    args = parser.parse_args()
    if args.prepare:
        try:
            path = prepare(Path(__file__).resolve().parents[1])
        except FileExistsError:
            print(".env.owner уже существует. Ничего не перезаписано.")
            return 2
        print(f"Создан {path.name}. Логин owner и пароль — внутри файла. Рабочий .env не изменён.")
        return 0
    parsed = urlsplit(args.url)
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"})
    ):
        print("Нужен локальный HTTP или HTTPS адрес без секретов и query-параметров.")
        return 2
    token = getpass.getpass("Персональный токен тестового пользователя (shluz_...): ").strip()
    if not token.startswith("shluz_"):
        print("Нужен персональный токен из админки, не bootstrap и не BotFather.")
        return 2
    for_result = check_gateway(args.url, token, send_mock=args.send_mock)
    for item in for_result:
        label = "OK" if item["ok"] else "FAIL"
        print(f"[{label}] {item['check']} (HTTP {item['http']})")
    print("Тексты ответов и ключи не выведены. Платные модели не запрашивались.")
    return 0 if all(item["ok"] for item in for_result) else 1


if __name__ == "__main__":
    raise SystemExit(main())
