from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from local_meeting_ai.adapters.transcription.faster_whisper import (
    FasterWhisperEngine,
    _inference_slot,
)
from local_meeting_ai.domain.entities import ModelProfile, TranscriptionEngineRequest
from local_meeting_ai.domain.errors import JobCancelledError


def test_model_is_loaded_once_on_the_dedicated_worker(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    constructed_on: list[str] = []

    class FakeWhisperModel:
        def __init__(self, *_: Any, **__: Any) -> None:
            constructed_on.append(threading.current_thread().name)

    engine = FasterWhisperEngine(tmp_path / "models")
    monkeypatch.setattr(engine, "_model_class", lambda: FakeWhisperModel)
    profile = ModelProfile(
        id="default",
        display_name="Default",
        description="Configured default",
        engine="faster-whisper",
        model="small",
        device="cpu",
        compute_type="int8",
        beam_size=5,
        vad_filter=True,
        installed=True,
    )

    try:
        asyncio.run(engine.prepare(profile, allow_model_download=False))
        asyncio.run(engine.prepare(profile, allow_model_download=False))

        assert len(constructed_on) == 1
        assert constructed_on[0].startswith("faster-whisper")
        capability = engine.capability()
        assert capability["loaded_models"] == ["small"]
        assert capability["worker"]["model_resident"] is True
        assert capability["worker"]["state"] == "ready"
    finally:
        engine.shutdown()


def test_progress_identifies_analysis_and_decoding_after_model_load(tmp_path, monkeypatch):
    engine = FasterWhisperEngine(tmp_path)
    messages = []
    fake = Mock()

    def transcribe(*args, **kwargs):
        assert "Analyzing audio" in messages[-1]

        def segments():
            assert "waiting for the first transcript segment" in messages[-1]
            yield SimpleNamespace(text="First segment", start=0, end=1)

        return segments(), SimpleNamespace(duration=1, language="en")

    fake.transcribe = transcribe
    monkeypatch.setattr(engine, "_model_class", lambda: Mock())
    monkeypatch.setattr(engine, "_get_model", lambda *args, **kwargs: (
        fake, threading.BoundedSemaphore(1), ("small",),
    ))
    request = TranscriptionEngineRequest(
        audio_path=tmp_path / "audio.wav", model="small", device="cpu", compute_type="int8",
        language="en", task="transcribe", beam_size=5, vad_filter=True, allow_model_download=False,
    )
    try:
        result = engine._transcribe_sync(
            request, lambda value, text: messages.append(text), lambda: False, Mock(),
        )
        assert len(result.segments) == 1
        assert any("Waiting for an inference slot" in message for message in messages)
    finally:
        engine.shutdown()


def test_cancelled_waiter_does_not_start_inference_or_release_another_requests_slot():
    slot = threading.BoundedSemaphore(1)
    slot.acquire()
    with pytest.raises(JobCancelledError, match="waiting"), _inference_slot(slot, lambda: True):
        pytest.fail("Cancelled waiter entered inference")
    assert not slot.acquire(blocking=False)
    slot.release()
    with pytest.raises(JobCancelledError), _inference_slot(slot, lambda: True):
        pytest.fail("Cancelled request entered inference")
    assert slot.acquire(blocking=False)
    slot.release()
