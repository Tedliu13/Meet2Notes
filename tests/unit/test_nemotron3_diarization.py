from __future__ import annotations

import io
import json
from itertools import pairwise
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from local_meeting_ai.adapters.diarization.nemotron3 import (
    MODEL_REVISION,
    TRANSFORMERS_REVISION,
    Nemotron3DiarizationEngine,
)
from local_meeting_ai.adapters.diarization.nemotron3_worker import audio_chunks, clip_segments
from local_meeting_ai.domain.errors import CapabilityUnavailableError, JobCancelledError


@pytest.fixture
def engine(tmp_path):
    instance = Nemotron3DiarizationEngine(tmp_path)
    yield instance
    instance._process = None
    instance.shutdown()


def test_installed_requires_completed_download_and_matching_runtime(engine):
    engine._runtime_python().parent.mkdir(parents=True)
    engine._runtime_python().touch()
    engine._marker_path().write_text(json.dumps([TRANSFORMERS_REVISION, MODEL_REVISION]))
    assert not engine.capability()["installed"]
    engine.cache_dir.mkdir(parents=True)
    (engine.cache_dir / "model.safetensors").touch()
    assert engine.capability()["installed"]
    engine._marker_path().write_text('["old runtime", "old model"]')
    assert not engine.capability()["installed"]


def test_install_does_not_load_model(engine, monkeypatch):
    monkeypatch.setattr(engine, "_installed", lambda: True)
    ensure = Mock()
    monkeypatch.setattr(engine, "_ensure_worker", ensure)
    engine._prepare_sync({}, True)
    ensure.assert_not_called()
    assert engine.capability()["worker"]["state"] == "idle"


def test_explicit_load_uses_selected_device(engine, monkeypatch):
    monkeypatch.setattr(engine, "_installed", lambda: True)
    monkeypatch.setattr(engine, "_ensure_worker", Mock())
    request = Mock(return_value={"ok": True})
    monkeypatch.setattr(engine, "_request_worker", request)
    engine._prepare_sync({"provider": "cuda"}, False)
    request.assert_called_once_with({"action": "load", "config": {"provider": "cuda"}})


def test_progress_messages_are_consumed_until_final_result(engine):
    engine._process = Mock(stdin=io.StringIO(), stdout=io.StringIO(
        'diagnostic\n{"progress": 0.4, "message": "30s analyzed"}\n'
        '{"progress": 0.8, "message": "60s analyzed"}\n'
        '{"ok": true, "segments": []}\n',
    ))
    progress = Mock()
    assert engine._request_worker({"action": "diarize"}, progress)["ok"]
    assert [call.args[0] for call in progress.call_args_list] == [0.4, 0.8]


def test_failure_is_visible_and_unloads_worker(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "_ensure_worker", Mock())
    monkeypatch.setattr(engine, "_request_worker", Mock(return_value={
        "ok": False, "error": "CUDA out of memory",
    }))
    unload = Mock()
    monkeypatch.setattr(engine, "unload", unload)
    with pytest.raises(CapabilityUnavailableError, match="CUDA out of memory"):
        engine._diarize_sync(tmp_path / "audio.wav", {}, Mock(), lambda: False)
    unload.assert_called()
    assert engine.capability()["worker"]["state"] == "error"


def test_cancelled_and_unsupported_speaker_count_fail_before_loading(engine, tmp_path, monkeypatch):
    ensure = Mock()
    monkeypatch.setattr(engine, "_ensure_worker", ensure)
    with pytest.raises(JobCancelledError):
        engine._diarize_sync(tmp_path / "audio.wav", {}, Mock(), lambda: True)
    with pytest.raises(CapabilityUnavailableError, match="at most 8"):
        engine._diarize_sync(tmp_path / "audio.wav", {"num_speakers": 9}, Mock(), lambda: False)
    ensure.assert_not_called()


@pytest.mark.parametrize("samples", [1, 486440, 486441, 16000 * 245, 16000 * 10800])
def test_chunk_windows_cover_recording_with_bounded_memory(samples):
    processor = SimpleNamespace(
        num_samples_first_audio_chunk=486440,
        num_samples_per_audio_chunk=486800,
        num_mel_frames_per_step=2720,
        audio_chunk_start=lambda frame: frame * 160 - 256,
    )
    chunks = list(audio_chunks(processor, samples))
    assert chunks[0][0] == 0 and chunks[0][2]
    assert chunks[-1][0] + chunks[-1][1] == samples and chunks[-1][3]
    assert all(not item[2] for item in chunks[1:])
    assert all(not item[3] for item in chunks[:-1])
    for current, following in pairwise(chunks):
        assert current[0] < following[0] < current[0] + current[1]
        assert following[1] <= 486800


def test_segments_preserve_overlap_speaker_ids_and_clip_padding():
    result = clip_segments([
        {"Start": 0.1, "End": 1.0, "Speaker": 7},
        {"Start": 0.2, "End": 1.2, "Speaker": 2},
        {"Start": 1.1, "End": 1.2, "Speaker": 0},
    ], 1050)
    assert result == [
        {"start_ms": 100, "end_ms": 1000, "speaker": 7},
        {"start_ms": 200, "end_ms": 1050, "speaker": 2},
    ]
