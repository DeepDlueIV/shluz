"""Безопасное отображение ответов и ограничение контекста без незаметного обрезания ответа."""

import html
import re

STYLES = {
    "balanced": "Отвечай понятно и по делу. Сохраняй язык собеседника.",
    "short": "Отвечай кратко, без ненужных вступлений. Сохраняй язык собеседника.",
    "detailed": "Давай подробные структурированные ответы с примерами. Сохраняй язык собеседника.",
}


def split_text(text: str, limit: int = 3800) -> list[str]:
    """Считаем UTF-16: эмодзи не ломают ограничение длины Telegram."""
    if not 2 <= limit <= 4096:
        raise ValueError("Invalid Telegram part limit")
    chunks = []
    start = 0
    while start < len(text):
        end = start
        size = 0
        while end < len(text):
            width = 2 if ord(text[end]) > 0xFFFF else 1
            if size + width > limit:
                break
            size += width
            end += 1
        if end < len(text):
            # Предпочитаем целые абзацы; не отбрасываем пробелы и разделители.
            midpoint = start + (end - start) // 2
            for separator in ("\n\n", "\n", " "):
                boundary = text.rfind(separator, midpoint, end)
                if boundary >= midpoint:
                    end = boundary + len(separator)
                    break
        chunks.append(text[start:end])
        start = end
    return chunks


def render_answer(text: str) -> str:
    # Только ограниченное форматирование. Произвольный HTML модели никогда не исполняется.
    escaped = html.escape(text, quote=False)
    pieces = re.split(r"(```[^\n`]*\n[\s\S]*?```)", escaped)
    rendered = []
    for part in pieces:
        if part.startswith("```") and part.endswith("```") and "\n" in part:
            rendered.append("<pre>" + part.split("\n", 1)[1][:-3] + "</pre>")
        else:
            rendered.append(re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", part))
    return "".join(rendered)


def prepare_messages(
    history: list[dict[str, str]], text: str, *, style: str = "balanced", budget: int = 24000
) -> tuple[list[dict[str, str]], bool]:
    system = {"role": "system", "content": STYLES.get(style, STYLES["balanced"])}
    current = {"role": "user", "content": text}
    available = budget - len(system["content"]) - len(text)
    if available < 0:
        raise ValueError("Message exceeds context budget")
    selected = []
    # История состоит только из успешно завершённых пар user/assistant.
    for index in range(len(history) - 2, -1, -2):
        pair = history[index : index + 2]
        size = sum(len(item["content"]) for item in pair)
        if size > available:
            break
        selected[0:0] = pair
        available -= size
    return [system, *selected, current], len(selected) < len(history)
