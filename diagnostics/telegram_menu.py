"""Safe, read-only diagnosis of Telegram Mini App menu after a failed SHLUZ start.

Never changes Docker containers, menu buttons, files under the installed SHLUZ root,
or service configuration. Never logs bot tokens, Telegram user IDs or raw Bot API
responses. No AI provider requests.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

PRODUCT = "noclip-telegram-menu-diagnosis-v1"
HOME = Path(r"D:\NoClip\Shluz")
PROJECT = "shluz-owner-check"
EXPECTED_URL = re.compile(
    r"https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com/miniapp\Z"
)
VALID_STATUS = frozenset(
    {
        "match", "commands", "default", "web_app_other_url", "unexpected",
        "api_400", "api_401", "api_403", "api_404", "api_429",
        "api_5xx", "api_other", "network_error", "invalid_response",
    }
)


def docker(*args, timeout=15):
    try:
        run = subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        if run.returncode:
            return None
        return run.stdout
    except (OSError, subprocess.TimeoutExpired):
        return None


def inspect(name):
    raw = docker("inspect", name)
    if not raw:
        return None
    try:
        result = json.loads(raw)
        return result[0] if len(result) == 1 else None
    except (TypeError, ValueError, IndexError):
        return None


def env(container):
    result = {}
    if not isinstance(container, dict):
        return result
    for entry in container.get("Config", {}).get("Env", []) or []:
        if isinstance(entry, str) and "=" in entry:
            key, value = entry.split("=", 1)
            if key.startswith("TELEGRAM_"):
                result[key] = value
    return result


def owned_bot():
    ids = docker(
        "ps", "-aq", "--no-trunc",
        "--filter", f"label=com.docker.compose.project={PROJECT}",
        "--filter", "label=com.docker.compose.service=telegram_bot",
    )
    if ids is None:
        return None, "docker_not_available"
    unique = [x.strip() for x in ids.splitlines() if x.strip()]
    if len(unique) != 1:
        return None, "legacy_container_missing_or_ambiguous"
    item = inspect(unique[0])
    labels = (item or {}).get("Config", {}).get("Labels") or {}
    if (
        labels.get("com.docker.compose.project") != PROJECT
        or labels.get("com.docker.compose.service") != "telegram_bot"
    ):
        return None, "legacy_ownership_mismatch"
    return item, "verified"


def expected_miniapp(root):
    """Parse ONLY the target URL from the protected local .env, never return a token."""
    p = Path(root) / "live-miniapp" / "live.env"
    if p.is_symlink() or not p.is_file():
        return None, "missing"
    try:
        if p.stat().st_size > 65536:
            return None, "invalid"
        match = None
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            if line.startswith("TELEGRAM_MINIAPP_URL="):
                if match is not None:
                    return None, "invalid"
                match = line.partition("=")[2]
        if match and EXPECTED_URL.fullmatch(match):
            return match, "valid"
    except (OSError, UnicodeError):
        return None, "unreadable"
    return None, "invalid"


def owner_chat_id(root):
    bases = [
        Path(root) / "miniapp-trial" / "owner.json",
        Path(os.environ.get("LOCALAPPDATA") or str(Path.home())) / "Shluz"
        / "miniapp-trial" / "owner.json",
    ]
    for p in bases:
        try:
            if p.is_symlink() or not p.is_file() or p.stat().st_size > 2048:
                continue
            value = json.loads(p.read_text(encoding="utf-8")).get("telegram_id")
            if type(value) is int and 0 < value < 2**52:
                return value
        except (OSError, ValueError, TypeError):
            continue
    return None


def telegram_request(token, method, payload=None, *, timeout=8):
    if method not in {"getMe", "getWebhookInfo", "getChatMenuButton"}:
        raise ValueError("Non-read-only Telegram method is disallowed")
    body = json.dumps(payload or {}, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/" + method,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read(65537)
        if len(raw) > 65536:
            return None, "invalid_response"
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("ok") is not True:
            return None, "invalid_response"
        return data.get("result"), "ok"
    except urllib.error.HTTPError as exc:
        status = exc.code
        if status in {400, 401, 403, 404, 429}:
            return None, "api_" + str(status)
        return None, "api_5xx" if status >= 500 else "api_other"
    except (OSError, ValueError, TypeError, UnicodeError):
        # HTTPError/URLError include the full token-containing URL. Never print.
        return None, "network_error"


def menu_status(result, error, expected):
    if error != "ok":
        return error
    if not isinstance(result, dict):
        return "invalid_response"
    type_ = result.get("type")
    if type_ in ("commands", "default"):
        return type_
    if type_ != "web_app":
        return "unexpected"
    web = result.get("web_app")
    if not isinstance(web, dict):
        return "invalid_response"
    if expected and web.get("url") == expected:
        return "match"
    return "web_app_other_url"


def live_state(root):
    p = Path(root) / "live-miniapp" / "state.json"
    try:
        if p.is_symlink() or not p.is_file() or p.stat().st_size > 65536:
            return "missing"
        phase = json.loads(p.read_text(encoding="utf-8")).get("phase")
        return phase if phase in {
            "active", "menu_unverified", "preparing", "stopped", "stop_failed",
        } else "unknown"
    except (OSError, ValueError, UnicodeError, TypeError):
        return "invalid"


def runtime_state(name, *, label=None):
    c = inspect(name)
    if c is None:
        return "missing"
    if label and ((c.get("Config", {}).get("Labels") or {}).get(label) != "1"):
        return "ownership_mismatch"
    state = c.get("State", {})
    if not state.get("Running"):
        return "stopped"
    health = (state.get("Health") or {}).get("Status")
    return "running_" + health if health in {"healthy", "unhealthy", "starting"} else "running"


def other_running_pollers(token):
    """In-memory comparison only; never include Docker env strings in the report."""
    all_ids = docker("ps", "-q", "--no-trunc")
    if all_ids is None:
        return "not_checked"
    count = 0
    for identifier in all_ids.splitlines()[:100]:
        item = inspect(identifier.strip())
        if env(item).get("TELEGRAM_BOT_TOKEN") == token:
            count += 1
    return str(count)


def inspect_result(root, *, api=telegram_request):
    root = Path(root)
    info = {
        "diagnostic": PRODUCT,
        "time_utc": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "root_present": str(root.is_dir()).lower(),
        "root_has_git": str((root / ".git").exists()).lower(),
        "state": live_state(root),
        "gateway": "not_checked",
        "new_bot": "not_checked",
        "miniapp_tunnel": "not_checked",
        "original_bot": "not_checked",
        "bot_env_same": "not_checked",
        "expected_miniapp_url": "not_checked",
        "telegram_auth": "not_checked",
        "telegram_webhook": "not_checked",
        "global_menu": "not_checked",
        "owner_scope": "not_checked",
        "owner_menu": "not_checked",
        "possible_same_token_running": "not_checked",
        "interpretation": "not_checked",
    }
    _, info["expected_miniapp_url"] = expected_miniapp(root)
    expected, _ = expected_miniapp(root)
    bot, owner_status = owned_bot()
    info["original_bot"] = owner_status
    if bot:
        info["original_bot"] = (
            "running" if bot.get("State", {}).get("Running") else "stopped"
        )
    info["gateway"] = runtime_state(
        "shluz-owner-check-shluz-1"
    )
    info["new_bot"] = runtime_state(
        "noclip-miniapp", label="io.noclip.permanent-miniapp"
    )
    info["miniapp_tunnel"] = runtime_state(
        "noclip-miniapp-https", label="io.noclip.permanent-miniapp"
    )
    live = inspect("noclip-miniapp")
    if live and (
        (live.get("Config", {}).get("Labels") or {}).get(
            "io.noclip.permanent-miniapp"
        ) == "1"
    ):
        info["bot_env_same"] = str(
            bool(
                env(bot).get("TELEGRAM_BOT_TOKEN")
                and env(bot).get("TELEGRAM_BOT_TOKEN")
                == env(live).get("TELEGRAM_BOT_TOKEN")
            )
        ).lower()
    token = env(bot).get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        info["interpretation"] = "original_bot_token_unavailable"
        return info
    me, error = api(token, "getMe")
    info["telegram_auth"] = (
        "valid" if error == "ok" and isinstance(me, dict) and me.get("id")
        else error if error != "ok" else "invalid_response"
    )
    if info["telegram_auth"] != "valid":
        info["interpretation"] = "telegram_api_not_authenticated_or_unreachable"
        return info
    webhook, error = api(token, "getWebhookInfo")
    info["telegram_webhook"] = (
        "configured" if error == "ok" and isinstance(webhook, dict) and webhook.get("url")
        else "not_configured" if error == "ok" else error
    )
    global_value, error = api(token, "getChatMenuButton")
    info["global_menu"] = menu_status(global_value, error, expected)
    owner = owner_chat_id(root)
    if owner is None:
        info["owner_scope"] = "unknown"
    else:
        info["owner_scope"] = "known"
        personal, personal_error = api(
            token, "getChatMenuButton", {"chat_id": owner}
        )
        info["owner_menu"] = menu_status(personal, personal_error, expected)
    info["possible_same_token_running"] = other_running_pollers(token)
    global_menu = info["global_menu"]
    personal = info["owner_menu"]
    if global_menu == "match" and personal in ("match", "default", "not_checked"):
        info["interpretation"] = "menu_configured"
    elif global_menu in ("commands", "default"):
        info["interpretation"] = "global_menu_not_web_app"
    elif global_menu == "web_app_other_url":
        info["interpretation"] = "global_web_app_url_mismatch"
    elif personal in ("commands", "web_app_other_url"):
        info["interpretation"] = "owner_chat_menu_override"
    elif global_menu.startswith("api_") or global_menu == "network_error":
        info["interpretation"] = "telegram_readback_unavailable"
    else:
        info["interpretation"] = "needs_detailed_local_codex_review"
    return info


def format_report(info):
    safe_values = set(VALID_STATUS) | {
        "valid", "configured", "not_configured", "unknown", "known", "missing",
        "unreadable", "invalid", "verified", "stopped", "running",
        "running_healthy", "running_unhealthy", "running_starting",
        "ownership_mismatch", "docker_not_available", "legacy_container_missing_or_ambiguous",
        "legacy_ownership_mismatch", "not_checked", "true", "false",
        "original_bot_token_unavailable", "telegram_api_not_authenticated_or_unreachable",
        "menu_configured", "global_menu_not_web_app", "global_web_app_url_mismatch",
        "owner_chat_menu_override", "telegram_readback_unavailable",
        "needs_detailed_local_codex_review", "active", "menu_unverified",
        "preparing", "stop_failed", "unknown",
    }
    ordered = [
        "diagnostic", "time_utc", "root_present", "root_has_git", "state",
        "gateway", "original_bot", "new_bot", "miniapp_tunnel",
        "bot_env_same", "expected_miniapp_url", "telegram_auth",
        "telegram_webhook", "global_menu", "owner_scope", "owner_menu",
        "possible_same_token_running", "interpretation",
    ]
    rows = []
    for key in ordered:
        value = str(info.get(key, "not_checked"))
        # Explicit enum allowlist: no API raw payload, secret, URL, user ID in report.
        if key in ("diagnostic", "time_utc"):
            value = value if key == "diagnostic" and value == PRODUCT or (
                key == "time_utc" and re.fullmatch(
                    r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC", value
                )
            ) else "invalid"
        elif key == "possible_same_token_running":
            value = value if (value == "not_checked" or (
                value.isdecimal() and int(value) <= 100
            )) else "invalid"
        elif value not in safe_values:
            value = "invalid"
        rows.append(key + ": " + value)
    rows += [
        "",
        "This report contains NO token, Telegram ID, passwords, raw Bot API body or URL.",
        "Read-only: no Docker/container changes, no model inference, no paid calls.",
    ]
    return "\n".join(rows) + "\n"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    root = Path(os.environ.get("NOCLIP_DIAG_TEST_HOME", str(HOME)))
    try:
        info = inspect_result(root)
        report = format_report(info)
        saved = Path(__file__).resolve().parent / "NoClip-Telegram-Diagnostic.txt"
        saved.write_text(report, encoding="utf-8")
        print(report)
        print("Отчёт рядом с Diagnose-NoClip.cmd: NoClip-Telegram-Diagnostic.txt")
        print("Пришлите только этот TXT для установления причины сбоя.")
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        # Do not include the exception; urllib HTTP errors can carry secret URLs.
        print("Диагностика не завершилась. Настройки и Docker не изменялись.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
