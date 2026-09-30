import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from local_meeting_ai.domain.entities import TranscriptionEngineRequest
from local_meeting_ai.infrastructure.model_memory import ModelMemory


@pytest.mark.asyncio
async def test_stages_release_audio_but_keep_summary_cache_for_questions():
    memory = ModelMemory(lambda: True)
    audio = Mock(name="audio", transcribe=AsyncMock())
    llm = Mock(name="llm", summarize=AsyncMock())
    wrapped_audio = memory.wrap("audio", audio)
    wrapped_llm = memory.wrap("summary", llm)
    await wrapped_audio.transcribe()
    await wrapped_llm.summarize()
    audio.unload.assert_called_once()
    await wrapped_llm.summarize()
    llm.unload.assert_not_called()


@pytest.mark.asyncio
async def test_cancelled_caller_does_not_release_an_inference_in_flight():
    memory = ModelMemory(lambda: True)
    started, finish = asyncio.Event(), asyncio.Event()

    async def infer():
        started.set()
        await finish.wait()

    original = Mock(summarize=infer)
    engine = memory.wrap("summary", original)
    second = memory.wrap("audio", Mock(transcribe=AsyncMock()))
    task = asyncio.create_task(engine.summarize())
    await started.wait()
    task.cancel()
    waiting = asyncio.create_task(second.transcribe())
    await asyncio.sleep(0)
    engine.unload()
    assert not waiting.done()
    original.unload.assert_not_called()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await waiting
    original.unload.assert_called_once()


@pytest.mark.asyncio
async def test_manual_mode_preserves_residency():
    memory = ModelMemory(lambda: False)
    original = Mock(transcribe=AsyncMock())
    await memory.wrap("audio", original).transcribe()
    await memory.wrap("summary", Mock(summarize=AsyncMock())).summarize()
    original.unload.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
async def test_gpu_failure_retries_only_before_any_segments(partial, monkeypatch):
    monkeypatch.setattr(
        "local_meeting_ai.infrastructure.model_memory.available_vram_mib", lambda index: 8192)
    memory = ModelMemory(lambda: True)
    calls = []

    async def transcribe(request, progress, cancelled, emit):
        calls.append(request.device)
        if request.device == "cuda":
            if partial:
                emit("first")
            raise RuntimeError("CUDA out of memory")
        assert request.compute_type == "int8"
        return "success"

    engine = memory.wrap("audio", Mock(transcribe=transcribe))
    request = TranscriptionEngineRequest(
        audio_path=Path("sample.wav"), model="turbo", device="cuda", compute_type="auto",
        language="es", task="transcribe", beam_size=5, vad_filter=True, allow_model_download=False)
    if partial:
        with pytest.raises(RuntimeError, match="out of memory"):
            await engine.transcribe(request, Mock(), lambda: False, Mock())
        assert calls == ["cuda"]
    else:
        assert await engine.transcribe(request, Mock(), lambda: False, Mock()) == "success"
        assert calls == ["cuda", "cpu"]


@pytest.mark.asyncio
async def test_cold_load_checks_free_memory_even_on_a_large_gpu(monkeypatch):
    monkeypatch.setattr(
        "local_meeting_ai.infrastructure.model_memory.available_vram_mib", lambda index: 512)
    original = Mock(transcribe=AsyncMock(return_value="complete"))
    engine = ModelMemory(lambda: True).wrap("audio", original)
    request = TranscriptionEngineRequest(
        audio_path=Path("sample.wav"), model="turbo", device="cuda", compute_type="auto",
        language="es", task="transcribe", beam_size=5, vad_filter=True, allow_model_download=False)
    await engine.transcribe(request, Mock(), lambda: False, Mock())
    assert original.transcribe.call_args.args[0].device == "cpu"
    assert request.device == "cuda"
