"""Локальный read-only отчёт владельца по сохранённым запросам и оценкам, без переписок."""

import argparse
import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

from telegram_bot.config import BotSettings
from telegram_bot.storage import METRICS_SQL


def main() -> int:
    parser = argparse.ArgumentParser(description="Агрегаты Telegram; это не финансовый отчёт")
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    path = args.database if args.database else BotSettings().database_path
    uri = "file:" + quote(str(path.resolve()), safe="/:") + "?mode=ro"
    try:
        db = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in db.execute(METRICS_SQL)]
        finally:
            db.close()
    except sqlite3.Error:
        print("База Telegram недоступна или её схема не обновлена. База не создана и не изменена.")
        return 2
    print(
        json.dumps(
            {
                "basis": "retained_bot_history",
                "financial_report": False,
                "by_model": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
