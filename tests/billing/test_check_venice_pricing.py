"""Скрипт сверки цен: только отчёт, никаких записей в БД."""

import json
import sqlite3
from pathlib import Path

from scripts.check_venice_pricing import analyze, load_official, read_bounds

SCHEMA = (
    "CREATE TABLE wallet_model_bounds ("
    "id TEXT PRIMARY KEY, max_cost_usd INTEGER NOT NULL, "
    "max_input_chars INTEGER NOT NULL, max_output_tokens INTEGER NOT NULL, "
    "reviewed INTEGER NOT NULL DEFAULT 0, expires INTEGER NOT NULL DEFAULT 0)"
)


def _db(tmp_path: Path, rows: list[tuple]) -> str:
    path = tmp_path / "bounds.db"
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA)
    conn.executemany("INSERT INTO wallet_model_bounds VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return str(path)


def _official(tmp_path: Path) -> str:
    payload = {
        "data": [
            {
                "id": "cheap-model",
                "type": "text",
                "model_spec": {
                    "type": "text",
                    "pricing": {
                        "input": {"usd": 1.0},
                        "output": {"usd": 2.0},
                        "cache_input": {"usd": 0.1},
                    },
                    "capabilities": {"supportsFunctionCalling": True},
                },
            },
            {
                "id": "no-tools-model",
                "type": "text",
                "model_spec": {
                    "type": "text",
                    "pricing": {"input": {"usd": 1.0}, "output": {"usd": 2.0}},
                    "capabilities": {"supportsFunctionCalling": False},
                },
            },
        ]
    }
    path = tmp_path / "official.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _rows(*rows):
    return [
        dict(
            id=i,
            max_cost_usd=cost,
            max_input_chars=3000,
            max_output_tokens=out,
            reviewed=reviewed,
            expires=expires,
        )
        for i, cost, out, reviewed, expires in rows
    ]


def test_analyze_flags_undersized_bound_and_expires(tmp_path):
    now = 1_900_000_000
    rows = _rows(
        # 100k токенов × 2.0 USD/1M = 0.2 USD — граница 0.1 занижена.
        ("venice:cheap-model", 10_000_000, 100_000, 1, now + 3600),
        # Просроченная проверка.
        ("venice:cheap-model", 50_000_000, 10_000, 1, now - 1),
    )
    findings = analyze(rows, load_official(_official(tmp_path)), now=now)
    levels = [(f["bound_id"], f["level"]) for f in findings]
    assert ("venice:cheap-model", "warning") in levels
    undersized = next(f for f in findings if "НИЖЕ стоимости" in f.get("issue", ""))
    assert undersized["output_only_upper"] == 0.2


def test_analyze_ok_bound_and_missing_model_and_unreviewed(tmp_path):
    now = 1_900_000_000
    rows = _rows(
        # 10k × 2.0 = 0.02 выхода — граница 0.5 покрывает.
        ("venice:cheap-model", 50_000_000, 10_000, 1, now + 3600),
        ("venice:ghost-model", 50_000_000, 10_000, 1, now + 3600),
        ("venice:cheap-model", 50_000_000, 10_000, 0, now + 3600),
    )
    findings = analyze(rows, load_official(_official(tmp_path)), now=now)
    by_id = {f["bound_id"]: f["level"] for f in findings}
    assert by_id["venice:ghost-model"] == "warning"
    # Две записи с одним id: просроченная reviewed=0 и ok.
    cheap = [f["level"] for f in findings if f["bound_id"] == "venice:cheap-model"]
    assert "ok" in cheap and "info" in cheap


def test_read_bounds_reads_real_db_schema(tmp_path):
    path = _db(
        tmp_path,
        [("venice:cheap-model", 50_000_000, 3000, 10_000, 1, 1_900_000_000)],
    )
    rows = read_bounds(path)
    assert rows[0]["max_cost_usd"] == 50_000_000
