"""Venice: безопасная проверка ключа и не более одной явной короткой генерации.

Для Codex/разработчика, не ручная приёмка владельца. Не используется в обычном CI.
Секрет берётся из окружения или локального .env. В отчёт не входят ключ/тексты/ID аккаунта.
"""

import argparse
import json
import os
from datetime import UTC, datetime

import httpx
from dotenv import dotenv_values

BASE_URL = "https://api.venice.ai/api/v1"
DEFAULT_MODEL = "zai-org-glm-5-2"
MAX_BYTES = 4 * 1024 * 1024


def _fetch(client, key, method, path, checks, **kwargs):
    check = {"method": method, "endpoint": path, "status": None}
    checks.append(check)
    try:
        # Не следуем перенаправлениям с секретом; ни GET, ни POST не повторяются.
        with client.stream(method, path, headers={"Authorization": f"Bearer {key}"},
                           follow_redirects=False, **kwargs) as response:
            check["status"] = response.status_code
            check["rate_headers"] = {
                name: value for name, value in response.headers.items()
                if name.startswith("x-ratelimit-") and len(value) <= 100
            }
            if response.status_code != 200:
                return None
            body = bytearray()
            for chunk in response.iter_bytes(chunk_size=16384):
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    check["error"] = "ResponseTooLarge"
                    return None
            payload = json.loads(body)
            if not isinstance(payload, dict):
                check["error"] = "InvalidResponse"
                return None
            return payload
    except (httpx.HTTPError, ValueError, UnicodeError) as exc:
        # Текст сетевой ошибки/ответа может содержать секреты — не печатаем его.
        check["error"] = type(exc).__name__
        return None


def inspect(client, key: str, model: str, *, chat: bool = False) -> dict:
    report = {"checked_at": datetime.now(UTC).isoformat(), "checks": [],
              "chat": {"sent": False}, "access_permitted": None}
    checks = report["checks"]
    # /models публичен и НЕ доказывает корректность ключа.
    payload = _fetch(client, key, "GET", "/api_keys/rate_limits", checks)
    if payload is None:
        return report
    data = payload.get("data")
    if not isinstance(data, dict):
        checks[-1]["error"] = "InvalidResponse"
        return report
    report["access_permitted"] = data.get("accessPermitted")
    for source, target in [("apiTier", "api_tier"), ("balances", "balances"),
                           ("keyExpiration", "key_expiration"),
                           ("nextEpochBegins", "next_epoch_begins"),
                           ("consumptionLimits", "consumption_limits")]:
        if source in data:
            report[target] = data[source]
    # Не выдумываем лимиты: только строки для запрошенной модели, если Venice их вернул.
    report["model_rate_limits"] = []
    limits = data.get("rateLimits")
    if isinstance(limits, list):
        for item in limits:
            if isinstance(item, dict) and item.get("apiModelId") == model:
                report["model_rate_limits"] = item.get("rateLimits", [])
    payload = _fetch(client, key, "GET", "/models", checks, params={"type": "text"})
    items = payload.get("data", []) if payload else []
    found = next((item for item in items if isinstance(item, dict) and item.get("id") == model),
                 None) if isinstance(items, list) else None
    report["model"] = None
    if found:
        spec = found.get("model_spec", {})
        if not isinstance(spec, dict):
            spec = {}
        report["model"] = {"id": model, **{k: spec[k] for k in
            ("name", "privacy", "capabilities", "pricing", "availableContextTokens", "traits")
            if k in spec}}
    if chat and report["access_permitted"] is True and found:
        report["chat"]["sent"] = True
        payload = _fetch(client, key, "POST", "/chat/completions", checks, json={
            "model": model,
            "messages": [{"role": "user", "content": "Reply with only OK."}],
            "stream": False, "max_completion_tokens": 128,
        })
        if payload:
            choices = payload.get("choices")
            first = choices[0] if isinstance(choices, list) and choices else {}
            message = first.get("message") if isinstance(first, dict) else {}
            content = message.get("content") if isinstance(message, dict) else None
            report["chat"]["content_nonempty"] = isinstance(content, str) and bool(content.strip())
            for field in ("usage", "cost"):
                if field in payload:
                    report["chat"][field] = payload[field]
    # Дополнительная защита от неожиданного отражения ключа в upstream JSON/заголовках.
    return json.loads(json.dumps(report, ensure_ascii=False).replace(key, "[SECRET]"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--chat", action="store_true", help="Один запрос, максимум 128 токенов")
    args = parser.parse_args()
    env = {**dotenv_values(args.env_file, encoding="utf-8-sig"), **os.environ}
    key = env.get("SHLUZ_VENICE_API_KEY") or env.get("VENICE_API_KEY")
    if not key or not key.strip():
        print("Ключ Venice не настроен. Разработчик должен задать VENICE_API_KEY локально.")
        return 2
    key = key.strip()
    with httpx.Client(base_url=BASE_URL, timeout=httpx.Timeout(30, connect=10),
                      follow_redirects=False) as client:
        report = inspect(client, key, args.model, chat=args.chat)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    healthy = bool(report["checks"]) and all(
        c["status"] == 200 and "error" not in c for c in report["checks"]
    )
    if args.chat:
        healthy = healthy and report["chat"].get("content_nonempty") is True
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
