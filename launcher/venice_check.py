"""Ограниченная проверка Venice. Только stdlib; секрет не покидает api.venice.ai.

Не включает Venice в Shluz, не меняет БД/тарифы. Один платный POST только с --chat.
Отчёт содержит измерения, но не ключ, вопрос, ответ, ID аккаунта или сырой stderr.
"""

import argparse
import getpass
import http.client
import json
import os
import re
import ssl
import subprocess
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

HOST = "api.venice.ai"
MODEL = "zai-org-glm-5-2"
PATHS = {"/api_keys/rate_limits", "/models?type=text", "/chat/completions"}


class ProbeError(Exception):
    def __init__(self, code):
        self.code = code


def http_request(method, path, key, body=None):
    if path not in PATHS or (method, path) not in {
        ("GET", "/api_keys/rate_limits"), ("GET", "/models?type=text"),
        ("POST", "/chat/completions"),
    }:
        raise ValueError("Недопустимый адрес проверки")
    conn = http.client.HTTPSConnection(HOST, timeout=10, context=ssl.create_default_context())
    try:
        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
        conn.request(method, "/api/v1" + path, headers=headers,
                     body=json.dumps(body).encode() if body else None)
        response = conn.getresponse()
        if response.status != 200:
            # В том числе 3xx: ключ никогда не отправляется по Location.
            raise ProbeError("HTTP_" + str(response.status))
        return response, conn.close
    except Exception:
        conn.close()
        raise


def numeric(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        number = Decimal(str(value))
        if number.is_finite() and 0 <= number <= Decimal("1e12"):
            return format(number, "f")
    except InvalidOperation:
        pass
    return None


def amounts(data):
    if not isinstance(data, dict):
        return {}
    return {k: numeric(data[k]) for k in ("USD", "DIEM", "usd", "diem", "bundledCredits")
            if k in data}


def get_json(request, key, path):
    stream, close = request("GET", path, key)
    try:
        buf = bytearray()
        deadline = time.monotonic() + 20
        while chunk := stream.read1(16384):
            buf.extend(chunk)
            if len(buf) > 4 * 1024 * 1024 or time.monotonic() > deadline:
                raise ProbeError("response_limit")
        result = json.loads(buf)
        if not isinstance(result, dict):
            raise ProbeError("invalid_response")
        return result
    finally:
        close()


def read_stream(stream, report):
    started = time.monotonic()
    pending, lines, size = b"", [], 0
    finish = False
    while chunk := stream.read1(4096):
        size += len(chunk)
        if size > 512 * 1024 or time.monotonic() - started > 45:
            raise ProbeError("stream_limit")
        pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            line = line.rstrip(b"\r")
            if line:
                if line.startswith(b"data:"):
                    lines.append(line[5:].lstrip(b" "))
                continue
            if not lines:
                continue
            data = b"\n".join(lines)
            lines.clear()
            if data == b"[DONE]":
                report["stream_complete"] = finish
                report["duration_ms"] = round((time.monotonic() - started) * 1000, 1)
                return
            payload = json.loads(data)
            if not isinstance(payload, dict) or payload.get("error"):
                raise ProbeError("stream_error")
            for item in payload.get("choices", []):
                delta = item.get("delta", {})
                if isinstance(delta.get("content"), str) and delta["content"].strip():
                    report["content_nonempty"] = True
                    if report["first_text_ms"] is None:
                        report["first_text_ms"] = round((time.monotonic() - started) * 1000, 1)
                if item.get("finish_reason") in {"stop", "length"}:
                    finish = True
                    report["finish_reason"] = item["finish_reason"]
            if isinstance(payload.get("usage"), dict):
                report["usage"] = {k: v for k, v in payload["usage"].items()
                                   if k in {"prompt_tokens", "completion_tokens", "total_tokens"}
                                   and type(v) is int and 0 <= v < 10**9}
            if isinstance(payload.get("cost"), dict):
                report["cost"] = amounts(payload["cost"])
    report["error"] = "incomplete_stream"


def probe(key, *, chat=False, request=http_request):
    report = {"checked_at": datetime.now(UTC).isoformat(), "access_permitted": None,
              "generations_attempted": 0, "model": MODEL, "stream_complete": False,
              "content_nonempty": False, "first_text_ms": None, "usage": None, "cost": None}
    try:
        data = get_json(request, key, "/api_keys/rate_limits").get("data")
        if not isinstance(data, dict):
            raise ProbeError("invalid_rate_limits")
        report["access_permitted"] = data.get("accessPermitted") is True
        report["balances"] = amounts(data.get("balances"))
        if report["access_permitted"] is not True:
            return report
        models = get_json(request, key, "/models?type=text").get("data", [])
        model = next((m for m in models if isinstance(m, dict) and m.get("id") == MODEL), None)
        if model is None:
            raise ProbeError("model_not_available")
        prices = model.get("model_spec", {}).get("pricing", {})
        inp, out = numeric(prices.get("input", {}).get("usd")), numeric(
            prices.get("output", {}).get("usd")
        )
        if inp is None or out is None:
            raise ProbeError("pricing_unknown")
        # 512 входных — консервативный запас для короткого фиксированного вопроса.
        # Оценка по каталогу, не обещание контроля чужого биллинга.
        estimate = (Decimal(inp) * 512 + Decimal(out) * 128) / 1000000
        report["estimated_max_usd"] = str(estimate)
        if estimate > Decimal("0.01"):
            raise ProbeError("price_exceeds_probe_budget")
        if not chat:
            return report
        body = {"model": MODEL, "messages": [{"role": "user", "content": "Reply only OK."}],
                "stream": True, "stream_options": {"include_usage": True},
                "max_completion_tokens": 128,
                "venice_parameters": {"include_venice_system_prompt": False,
                                      "enable_web_search": "off"}}
        report["generations_attempted"] = 1  # Не значит, что сервер подтвердил получение.
        stream, close = request("POST", "/chat/completions", key, body)
        try:
            read_stream(stream, report)
        finally:
            close()
    except ProbeError as exc:
        report["error"] = exc.code
    except (OSError, http.client.HTTPException):
        report["error"] = "network_unavailable"
    except (ValueError, TypeError, KeyError, AttributeError):
        report["error"] = "invalid_response"
    return report


def key_from_env_text(text):
    values = {}
    for line in text.splitlines():
        found = re.match(r"\s*(?:export\s+)?(SHLUZ_VENICE_API_KEY|VENICE_API_KEY)\s*=\s*(.*?)\s*$",
                         line)
        if found:
            value = found[2].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[found[1]] = value
    return values.get("SHLUZ_VENICE_API_KEY") or values.get("VENICE_API_KEY")


def valid_key(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_.-]{20,256}", value))


def local_key():
    value = os.environ.get("SHLUZ_VENICE_API_KEY") or os.environ.get("VENICE_API_KEY")
    if valid_key(value):
        return value
    roots = [Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Shluz" / "config"]
    try:
        result = subprocess.run(
            ["docker", "ps", "-aq", "--filter",
             "label=com.docker.compose.project=shluz-owner-check",
             "--filter", "label=com.docker.compose.service=shluz"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        ids = result.stdout.split()
        if len(ids) == 1 and re.fullmatch("[a-f0-9]+", ids[0]):
            result = subprocess.run(["docker", "inspect", ids[0]], capture_output=True,
                                    text=True, encoding="utf-8", timeout=10, check=False)
            info = json.loads(result.stdout)[0]["Config"]
            value = key_from_env_text("\n".join(info.get("Env", [])))
            if valid_key(value):
                return value
            source = (info.get("Labels") or {}).get("com.docker.compose.project.working_dir")
            if source:
                roots.append(Path(source))
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, IndexError):
        pass
    for root in roots:
        for name in (".env.owner", ".env.venice", ".env"):
            try:
                path = root / name
                if path.is_file() and path.stat().st_size <= 65536:
                    value = key_from_env_text(path.read_text(encoding="utf-8-sig"))
                    if valid_key(value):
                        return value
            except OSError:
                pass
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat", action="store_true")
    args = parser.parse_args()
    print("Venice — отдельная проверка, без изменения шлюза")
    if args.chat:
        print("Не более одного платного запроса, до 128 выходных токенов; без повторов.")
    key = local_key()
    if not key:
        key = getpass.getpass("Ключ Venice (ввод скрыт, сохраняться не будет): ").strip()
    if not valid_key(key):
        print("Ключ не введён или формат не подходит. Генерация не отправлялась.")
        return 2
    report = probe(key, chat=args.chat)
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Shluz" / "reports"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "venice-check.json"
    with path.open("w", encoding="utf-8") as dest:
        os.chmod(path, 0o600)
        json.dump(report, dest, ensure_ascii=False, indent=2)
    if report["access_permitted"] is None:
        print("Нет ответа API: проблема соединения. Действительность ключа не проверена.")
    elif not report["access_permitted"]:
        print("API ответил, но генерация пока не разрешена. Нужен именно доступный API-баланс.")
    else:
        print("API разрешает генерацию.")
    print("Попыток генерации:", report["generations_attempted"])
    print("Поток завершён:", "да" if report["stream_complete"] else "нет")
    print("Текст получен:", "да" if report["content_nonempty"] else "нет")
    print("Токены:", json.dumps(report["usage"], ensure_ascii=False))
    print("Стоимость:", json.dumps(report["cost"], ensure_ascii=False), "(null — не сообщена)")
    if report.get("error"):
        print("Код проверки:", report["error"])
    print("Отчёт без ключа и переписки:", path)
    return 0 if report["stream_complete"] and report["content_nonempty"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, EOFError):
        print("Проверка прервана; автоматического повтора нет.")
        raise SystemExit(1) from None
    except OSError:
        print("Не удалось сохранить отчёт. Генерацию автоматически не повторять.")
        raise SystemExit(1) from None
