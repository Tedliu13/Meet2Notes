from __future__ import annotations

import importlib
from dataclasses import replace
from typing import Any, cast

from local_meeting_ai.domain.entities import SegmentDraft


class ChineseTranscriptConverter:
    """Normalize Chinese final transcripts without touching other languages or timings."""

    def __init__(self, script: str, *, task: str = "transcribe") -> None:
        self.enabled = script == "traditional" and task == "transcribe"
        self._converter: Any = None

    def segment(self, segment: SegmentDraft, language: str | None) -> SegmentDraft:
        if not self.enabled or not language or language.lower().split("-")[0] != "zh":
            return segment
        if self._converter is None:
            # Pure Python and bundled dictionaries: no native ISA, model or network request.
            self._converter = importlib.import_module("opencc").OpenCC("s2tw")
        text = cast(str, self._converter.convert(segment.text))
        metadata = dict(segment.metadata or {})
        if text != segment.text:
            metadata.setdefault("transcription_original_text", segment.text)
        words = metadata.get("words")
        if isinstance(words, list):
            metadata["words"] = [
                {**word, "word": self._converter.convert(word["word"])}
                if isinstance(word, dict) and isinstance(word.get("word"), str) else word
                for word in words
            ]
        return replace(segment, text=text, metadata=metadata)
