"""Совместимый запуск для прежней команды python -m telegram_bot.bot."""

from telegram_bot.runtime import main

if __name__ == "__main__":
    raise SystemExit(main())
