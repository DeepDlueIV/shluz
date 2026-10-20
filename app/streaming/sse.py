"""Ограниченный разбор SSE без накопления всего ответа и без логирования содержимого."""

from collections.abc import AsyncIterable, AsyncIterator


class SSEError(ValueError):
    pass


async def events(
    chunks: AsyncIterable[bytes],
    *,
    max_frame: int = 262144,
    max_total: int = 8 * 1024**2,
) -> AsyncIterator[str]:
    buffer = bytearray()
    lines: list[bytes] = []
    total = frame_size = 0
    async for chunk in chunks:
        total += len(chunk)
        if total > max_total:
            raise SSEError("stream_size_limit")
        buffer.extend(chunk)
        while (end := buffer.find(b"\n")) >= 0:
            line = bytes(buffer[:end]).rstrip(b"\r")
            del buffer[: end + 1]
            frame_size += len(line)
            if frame_size > max_frame:
                raise SSEError("stream_frame_limit")
            if not line:
                if lines:
                    try:
                        yield b"\n".join(lines).decode("utf-8")
                    except UnicodeError as exc:
                        raise SSEError("stream_encoding") from exc
                lines, frame_size = [], 0
            elif line.startswith(b"data:"):
                value = line[5:]
                lines.append(value[1:] if value.startswith(b" ") else value)
        if len(buffer) + frame_size > max_frame:
            raise SSEError("stream_frame_limit")
    # Не выдаём обрезанный последний frame за завершённый SSE event.
    if buffer or lines:
        raise SSEError("stream_truncated_frame")
