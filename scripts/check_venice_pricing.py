"""Сверка проверенных цен шлюза (wallet_model_bounds) с официальным каталогом Venice.

Только отчёт в stdout: скрипт НИКОГДА не пишет в БД и не меняет цены — решение
о каждой цене принимает владелец (см. docs/pricing/reconciliation-2026-10-30.md).
Запуск: python scripts/check_venice_pricing.py --db <shluz.db> --official <venice-models-raw-*.json>
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

USD_SCALE = 100_000_000


def load_official(path: str) -> dict[str, dict]:
    """Официальный каталог: модель → ставки USD/1M и поддержка function calling."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rates: dict[str, dict] = {}
    for model in data.get("data", []):
        if (model.get("type") or (model.get("model_spec") or {}).get("type")) != "text":
            continue
        spec = model.get("model_spec") or {}
        pricing = spec.get("pricing") or {}
        rates[model["id"]] = {
            "input": (pricing.get("input") or {}).get("usd"),
            "output": (pricing.get("output") or {}).get("usd"),
            "cache_input": (pricing.get("cache_input") or {}).get("usd"),
            "function_calling": bool(
                (spec.get("capabilities") or {}).get("supportsFunctionCalling")
            ),
        }
    return rates


def read_bounds(db_path: str) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, max_cost_usd, max_input_chars, max_output_tokens, reviewed, expires "
            "FROM wallet_model_bounds ORDER BY id"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def analyze(rows: list[dict], rates: dict[str, dict], now: int | None = None) -> list[dict]:
    """Связать каждую запись шлюза с каталогом и вернуть список находок."""
    now = now or int(time.time())
    findings = []
    for row in rows:
        model_id = row["id"].split(":", 1)[1] if ":" in row["id"] else row["id"]
        official = rates.get(model_id)
        finding: dict = {"bound_id": row["id"], "model": model_id, "official": official}
        if official is None:
            finding["level"] = "warning"
            finding["issue"] = "модели нет в официальном каталоге — цену не с чем сверить"
            findings.append(finding)
            continue
        if not row["reviewed"]:
            finding["level"] = "info"
            finding["issue"] = "цена не помечена проверенной (reviewed=false) — вызовы блокируются"
            findings.append(finding)
            continue
        if int(row["expires"]) <= now:
            finding["level"] = "warning"
            finding["issue"] = (
                "проверка просрочена (expires в прошлом) — вызовы блокируются до ревью"
            )
            findings.append(finding)
            continue
        max_cost = (
            float(row["max_cost_usd"]) / USD_SCALE
            if isinstance(row["max_cost_usd"], int)
            else float(row["max_cost_usd"])
        )
        finding["max_cost_usd"] = max_cost
        output = official.get("output")
        if output:
            # Стоимость чистого выхода при заявленном лимите: нижняя граница реальной цены.
            output_only = row["max_output_tokens"] * output / 1_000_000
            finding["output_only_upper"] = round(output_only, 8)
            if max_cost < output_only:
                finding["level"] = "warning"
                finding["issue"] = (
                    f"max_cost_usd ({max_cost}) НИЖЕ стоимости одного выхода при лимите "
                    f"({output_only:.8f} = {row['max_output_tokens']} tok × {output}/1M) — "
                    "граница занижена, резерв сорвётся при полном ответе"
                )
                findings.append(finding)
                continue
        if not official.get("function_calling"):
            finding["level"] = "info"
            finding["issue"] = (
                "каталог не заявляет function calling — tools-запросы к ней не рассчитывать"
            )
            findings.append(finding)
            continue
        finding["level"] = "ok"
        finding["issue"] = "сверена, в срок, лимиты совместимы с каталогом"
        findings.append(finding)
    return findings


def print_report(findings: list[dict]) -> int:
    warnings = 0
    for finding in findings:
        level = finding.get("level", "?")
        if level in {"warning", "error"}:
            warnings += 1
        detail = finding.get("issue", "")
        extra = ""
        if finding.get("official"):
            extra = (
                f" [каталог: input={finding['official'].get('input')}, "
                f"output={finding['official'].get('output')}, "
                f"cache={finding['official'].get('cache_input')} USD/1M]"
            )
        print(f"[{level.upper():7}] {finding['bound_id']}: {detail}{extra}")
    print(f"\nИтого записей: {len(findings)}, предупреждений: {warnings}")
    return 1 if any(f.get("level") == "error" for f in findings) else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Сверка цен шлюза с каталогом Venice (read-only).")
    parser.add_argument("--db", required=True, help="файл SQLite шлюза (доступ только чтение)")
    parser.add_argument(
        "--official", required=True, help="сырой дамп каталога venice-models-raw-*.json"
    )
    args = parser.parse_args(argv)
    findings = analyze(read_bounds(args.db), load_official(args.official))
    return print_report(findings)


if __name__ == "__main__":
    sys.exit(main())
