"""Disposable, local voice embeddings stored beside their source WAV."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
CACHE_VERSION = 1


def voiceprint_cache_path(audio: Path) -> Path:
    return audio.with_name(audio.name + ".voiceprints.json")


def file_identity(path: Path) -> list[str | int]:
    stat = path.stat()
    return [str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def signature(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class VoiceprintCache:
    def __init__(self, audio: Path, model_key: str, turns_key: str = "profile") -> None:
        self.audio = audio
        self.path = voiceprint_cache_path(audio)
        self.identity = file_identity(audio)
        self.key = signature([CACHE_VERSION, self.identity, model_key, turns_key])
        self.entries: dict[str, list[float]] = {}
        self.dirty = False
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if (payload.get("key") == self.key and isinstance(payload.get("entries"), dict)
                    and payload.get("checksum") == signature(payload["entries"])):
                for key, vector in payload["entries"].items():
                    if (isinstance(vector, list) and len(vector) <= 8192
                            and all(isinstance(v, (float, int)) and math.isfinite(v)
                                    for v in vector)):
                        self.entries[key] = [float(v) for v in vector]
        except (OSError, ValueError, AttributeError, TypeError):
            pass  # Missing, outdated or damaged caches are rebuilt from the WAV.

    def save(self) -> None:
        if not self.dirty:
            return
        temporary = self.path.with_name(self.path.name + f".{uuid.uuid4().hex}.tmp")
        try:
            if file_identity(self.audio) != self.identity:
                return
            temporary.write_text(
                json.dumps({"key": self.key, "entries": self.entries,
                            "checksum": signature(self.entries)}), encoding="utf-8",
            )
            os.replace(temporary, self.path)
            self.dirty = False
        except OSError as error:
            logger.warning("Voiceprint cache could not be saved: %s", error)
        finally:
            temporary.unlink(missing_ok=True)
