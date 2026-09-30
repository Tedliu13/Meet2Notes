"""Optional answer fragments without changing the summary plugin protocol."""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any


class AnswerProgress:
    def __init__(self, emit: Callable[[dict[str, Any]], None], *, structured: bool = False) -> None:
        self.emit = emit
        self.structured = structured
        self.raw = ""
        self.visible = ""

    def __call__(self, progress: float, message: str) -> None:
        self.emit({"type": "status", "phase": "working"})

    def on_token(self, text: str) -> None:
        self.raw += text
        answer = partial_answer(self.raw) if self.structured else self.raw
        if answer.startswith(self.visible) and len(answer) > len(self.visible):
            self.emit({"type": "delta", "text": answer[len(self.visible):]})
            self.visible = answer

    def on_sources(self, citations: list[dict[str, Any]]) -> None:
        self.emit({"type": "sources", "citations": citations})

    def on_phase(self, phase: str) -> None:
        self.emit({"type": "status", "phase": phase})

    def on_context(self, progress: dict[str, Any]) -> None:
        self.emit({"type": "context", **{
            key: max(0, int(progress.get(key) or 0)) for key in ("total", "processed", "cache")
        }})


def partial_answer(raw: str) -> str:
    """Expose only Live Assistant's JSON text field, including split escapes."""
    match = re.search(r'"text"\s*:\s*"', raw)
    if match is None:
        return ""
    text = raw[match.end():]
    escaped = False
    for index, char in enumerate(text):
        if char == '"' and not escaped:
            text = text[:index]
            break
        escaped = char == "\\" and not escaped
    # A chunk can end halfway through a JSON escape or a UTF-16 surrogate pair.
    for trim in range(min(12, len(text)) + 1):
        candidate = text[:-trim] if trim else text
        try:
            decoded = json.loads('"' + candidate + '"')
            decoded.encode("utf-8")
            return str(decoded)
        except (ValueError, UnicodeError):
            continue
    return ""
