from __future__ import annotations

import json
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from local_meeting_ai.adapters.diarization import profile_matching
from local_meeting_ai.adapters.diarization.voice_sampling import (
    exclusive_ranges,
    identify,
    sample_ranges,
)
from local_meeting_ai.domain.entities import DiarizationSegment
from local_meeting_ai.domain.errors import JobCancelledError
from local_meeting_ai.infrastructure.voiceprint_cache import VoiceprintCache, voiceprint_cache_path


def make_wave(path: Path, seconds: int = 70) -> Path:
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16000)
        signal = (np.sin(np.arange(seconds * 16000) * 0.17) * 10000).astype("<i2")
        target.writeframes(signal.tobytes())
    return path


def test_sampling_excludes_overlap_and_duplicate_turns():
    clean = exclusive_ranges([
        DiarizationSegment(0, 10000, 0), DiarizationSegment(1000, 2000, 0),
        DiarizationSegment(4000, 7000, 1), DiarizationSegment(10000, 20000, 1),
    ])
    assert clean == {0: [(0, 4000), (7000, 10000)], 1: [(10000, 20000)]}
    ranges = sample_ranges(clean[0], 20000)
    assert all(end <= 4000 or start >= 7000 for start, end in ranges)


def test_three_hour_speaker_has_bounded_distributed_samples():
    ranges = sample_ranges([(0, 10800000)], 10800000)
    assert len(ranges) == 10
    assert sum(end - start for start, end in ranges[:5]) <= 30000
    assert sum(end - start for start, end in ranges) <= 60000
    assert min(start for start, _ in ranges[:5]) == 3000
    assert max(end for _, end in ranges[:5]) > 10790000


def test_identity_samples_prefer_separate_long_turns_and_skip_openings():
    turns = [(i * 20000, i * 20000 + 12000) for i in range(14)]
    samples = sample_ranges([(15000, 18000), *turns], 300000)
    assert len(samples) == 10
    assert len({start // 20000 for start, _ in samples}) == 10
    assert all(start % 20000 == 3000 and end - start == 6000 for start, end in samples)


def test_short_turn_fallback_stays_centered_inside_turn():
    assert sample_ranges([(1000, 4000)], 5000) == [(1100, 3900)]
    assert sample_ranges([(1000, 2000)], 5000) == []


@pytest.mark.asyncio
async def test_saved_voice_uses_selected_samples_without_exporting_full_speech(tmp_path):
    from unittest.mock import AsyncMock

    from local_meeting_ai.application.speaker_service import SpeakerService

    turns = [SimpleNamespace(speaker_id=1, start_ms=i * 20000, end_ms=i * 20000 + 12000)
             for i in range(12)]
    turns.append(SimpleNamespace(speaker_id=2, start_ms=3000, end_ms=9000))
    transcriptions = Mock()
    transcriptions.get.return_value = SimpleNamespace(meeting_id=3)
    transcriptions.get_speaker.return_value = SimpleNamespace(meeting_id=3)
    transcriptions.speaker_turns.return_value = turns
    recordings = Mock()
    recordings.latest_for_role.return_value = SimpleNamespace(
        local_path="source.wav", duration_ms=240000,
    )
    exporter = SimpleNamespace(export_audio_ranges=AsyncMock())
    service = SpeakerService(meetings=Mock(), recordings=recordings,
                             transcriptions=transcriptions, storage=Mock(),
                             exporter=exporter, normalizer=Mock(), profiles=Mock())
    await service.export_voice_sample(4, 1, tmp_path / "sample.wav")
    ranges = exporter.export_audio_ranges.call_args.args[2]
    assert len(ranges) == 5
    assert sum(b - a for a, b in ranges) == 30000
    assert all(a >= 23000 for a, b in ranges)  # Overlapped first turn excluded.


def test_unknown_and_ambiguous_voices_are_not_forced_to_a_saved_name():
    assert identify([np.array([0., 1.])], [np.array([1., 0.])], 0.72) == (None, False)
    assert identify([np.array([1., 0.])], [np.array([1., 0.]), np.array([1., 0.])], 0.72) == (
        None, True,
    )
    # A single good fragment cannot override several conflicting fragments.
    assert identify([np.array([1., 0.]), np.array([0., 1.]), np.array([0., 1.])],
                    [np.array([1., 0.])], 0.72)[0] is None


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    matcher = profile_matching.SherpaOnnxSpeakerProfileMatcher(tmp_path / "models")
    model = matcher._model_path({})
    model.write_bytes(b"test model")
    monkeypatch.setattr(profile_matching.importlib.metadata, "version", lambda _: "test")
    factory = Mock(return_value=object())
    compute = Mock(return_value=np.array([1., 0., 0.]))
    monkeypatch.setattr(matcher, "_create_extractor", factory)
    monkeypatch.setattr(matcher, "_embedding", compute)
    profile = SimpleNamespace(id=1, sample_path=str(make_wave(tmp_path / "profile.wav")))
    recording = make_wave(tmp_path / "meeting.wav")
    turns = [DiarizationSegment(0, 70000, 0)]
    yield matcher, factory, compute, profile, recording, turns
    matcher.shutdown()


def test_warm_matching_reads_no_audio_samples_and_loads_no_model(fixture, monkeypatch):
    matcher, factory, compute, profile, recording, turns = fixture
    progress = Mock()
    assert matcher._match_sync(recording, turns, [profile], {}, progress) == {0: profile}
    assert compute.call_count == 10
    assert matcher.capability()["last_match"]["audio_seconds"] <= 60
    assert [call.args[0] for call in progress.call_args_list] == sorted(
        call.args[0] for call in progress.call_args_list)
    factory.reset_mock()
    compute.reset_mock()
    monkeypatch.setattr(wave.Wave_read, "readframes", Mock(side_effect=AssertionError("PCM read")))
    assert matcher._match_sync(recording, turns, [profile], {}) == {0: profile}
    factory.assert_not_called()
    compute.assert_not_called()
    assert matcher.capability()["last_match"]["cache_hits"] == 10


def test_modified_diarization_sample_and_model_invalidate_only_relevant_cache(fixture):
    matcher, _, compute, profile, recording, turns = fixture
    matcher._match_sync(recording, turns, [profile], {})
    compute.reset_mock()
    matcher._match_sync(recording, [DiarizationSegment(0, 65000, 0)], [profile], {})
    assert compute.call_count == 5  # Stored voice is still reusable.
    compute.reset_mock()
    make_wave(Path(profile.sample_path), 60)
    matcher._match_sync(recording, [DiarizationSegment(0, 65000, 0)], [profile], {})
    assert compute.call_count == 5  # Only the changed profile.
    compute.reset_mock()
    matcher._model_path({}).write_bytes(b"different model")
    matcher._match_sync(recording, [DiarizationSegment(0, 65000, 0)], [profile], {})
    assert compute.call_count == 10


def test_cancellation_propagates_instead_of_returning_an_empty_success(fixture):
    matcher, _, compute, profile, recording, turns = fixture
    cancelled = False

    def progress(value, message):
        nonlocal cancelled
        cancelled = "fragment" in message

    with pytest.raises(JobCancelledError):
        matcher._match_sync(recording, turns, [profile], {}, progress, lambda: cancelled)
    assert compute.call_count == 1


def test_uncertain_match_expands_samples_and_reuses_expanded_cache(fixture):
    matcher, factory, compute, profile, recording, turns = fixture
    compute.side_effect = (
        [np.array([1., 0.])] * 5
        + [np.array([0.75, np.sqrt(1 - 0.75 ** 2)])] * 4
        + [np.array([0., 1.])]
        + [np.array([1., 0.])] * 5
    )
    progress = Mock()
    assert matcher._match_sync(recording, turns, [profile], {}, progress) == {0: profile}
    assert compute.call_count == 15
    assert matcher.capability()["last_match"]["audio_seconds"] <= 90
    assert any("uncertain" in call.args[1] for call in progress.call_args_list)
    factory.reset_mock()
    compute.reset_mock()
    assert matcher._match_sync(recording, turns, [profile], {}) == {0: profile}
    factory.assert_not_called()
    compute.assert_not_called()


def test_deleting_saved_voice_removes_its_cache(tmp_path):
    from local_meeting_ai.application.speaker_service import SpeakerService

    sample = make_wave(tmp_path / "profile.wav", 2)
    cache = voiceprint_cache_path(sample)
    cache.write_text("{}")
    profiles = Mock()
    profiles.get.return_value = SimpleNamespace(sample_path=str(sample))
    service = SpeakerService(
        meetings=Mock(), recordings=Mock(), transcriptions=Mock(),
        storage=Mock(), exporter=Mock(), normalizer=Mock(), profiles=profiles,
    )
    service.delete_profile(4)
    assert not sample.exists() and not cache.exists()
    profiles.delete.assert_called_once_with(4)


def test_corrupted_cache_rebuilds_without_trusting_vectors(tmp_path):
    audio = make_wave(tmp_path / "sample.wav", 2)
    cache = VoiceprintCache(audio, "model")
    cache.entries["test"] = [1., 0.]
    cache.dirty = True
    cache.save()
    payload = json.loads(voiceprint_cache_path(audio).read_text())
    payload["entries"]["test"] = [0., 1.]
    voiceprint_cache_path(audio).write_text(json.dumps(payload))
    assert VoiceprintCache(audio, "model").entries == {}
