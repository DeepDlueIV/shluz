"""Сборка function call по index. Фрагменты arguments не обязаны быть JSON."""

from app.api.chat_types import ToolCall, ToolDelta
from app.providers.base import ProviderError


class ToolAccumulator:
    def __init__(self):
        self.calls = {}
        self.size = 0

    def accept(self, deltas):
        normalized = []
        try:
            if not isinstance(deltas, list) or len(deltas) > 128:
                raise ValueError("Invalid tool deltas")
            for raw in deltas:
                value = ToolDelta.model_validate(raw).model_dump(exclude_none=True)
                index = value["index"]
                fn = value.get("function", {})
                if index not in self.calls:
                    # AI SDK требует ID и name в первом фрагменте данного индекса.
                    if not value.get("id") or not fn.get("name"):
                        raise ValueError("Missing initial tool identity")
                    if any(c["id"] == value["id"] for c in self.calls.values()):
                        raise ValueError("Duplicate tool identity")
                    self.calls[index] = {
                        "id": value["id"],
                        "type": "function",
                        "function": {"name": fn["name"], "arguments": ""},
                    }
                call = self.calls[index]
                if value.get("id", call["id"]) != call["id"] or (
                    fn.get("name", call["function"]["name"]) != call["function"]["name"]
                ):
                    raise ValueError("Changing tool identity")
                fragment = fn.get("arguments", "")
                self.size += len(fragment)
                if self.size > 200000:
                    raise ValueError("Tool arguments limit")
                call["function"]["arguments"] += fragment
                normalized.append(value)
            return normalized
        except (ValueError, TypeError) as exc:
            raise ProviderError("Unsupported tool stream") from exc

    def complete(self):
        try:
            return [ToolCall.model_validate(self.calls[i]).model_dump() for i in sorted(self.calls)]
        except ValueError as exc:
            raise ProviderError("Invalid completed tool calls") from exc
