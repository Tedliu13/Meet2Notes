from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from local_meeting_ai.application.saved_speaker_matching import match_saved_speakers
from local_meeting_ai.domain.entities import DiarizationSegment
from local_meeting_ai.domain.errors import JobCancelledError


@pytest.mark.asyncio
async def test_saved_profiles_are_loaded_and_forwarded_with_normalized_audio_and_job_context():
    profile = SimpleNamespace(id=4, sample_path="saved.wav")
    incomplete = SimpleNamespace(id=5, sample_path=None)
    profiles = Mock()
    profiles.list.return_value = [profile, incomplete]
    matcher = SimpleNamespace(match=AsyncMock(return_value={3: profile}))
    config = {"recognize_saved_speakers": True}
    turns = [DiarizationSegment(0, 5000, 3)]
    result = await match_saved_speakers(
        Path("normalized.wav"), turns, config, profiles, matcher,
        job_uuid="job", transcription_id=9, progress=Mock(), is_cancelled=lambda: False,
    )
    assert result == {3: profile}
    profiles.list.assert_called_once_with()
    arguments = matcher.match.call_args.args
    assert arguments[:3] == (Path("normalized.wav"), turns, [profile])
    assert arguments[3]["_voice_match_job_uuid"] == "job"
    assert config == {"recognize_saved_speakers": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [
    "recognition_disabled", "no_saved_profiles", "no_saved_sample_paths", "matcher_unavailable",
])
async def test_skipping_comparison_has_an_explicit_reason(reason, caplog):
    profiles = Mock()
    profiles.list.return_value = (
        [] if reason == "no_saved_profiles" else
        [SimpleNamespace(sample_path=None if reason == "no_saved_sample_paths" else "sample.wav")]
    )
    matcher = SimpleNamespace(match=AsyncMock())
    with caplog.at_level("INFO"):
        result = await match_saved_speakers(
            Path("normalized.wav"), [DiarizationSegment(0, 5000, 0)],
            {"recognize_saved_speakers": reason != "recognition_disabled"}, profiles,
            None if reason == "matcher_unavailable" else matcher,
            job_uuid="job", transcription_id=9, progress=Mock(), is_cancelled=lambda: False,
        )
    assert result == {}
    matcher.match.assert_not_called()
    assert f"reason={reason}" in caplog.text


@pytest.mark.asyncio
async def test_matching_cancellation_is_not_hidden():
    profiles = Mock()
    profiles.list.return_value = [SimpleNamespace(sample_path="sample.wav")]
    matcher = SimpleNamespace(match=AsyncMock(side_effect=JobCancelledError("cancelled")))
    with pytest.raises(JobCancelledError):
        await match_saved_speakers(
            Path("normalized.wav"), [DiarizationSegment(0, 5000, 0)], {}, profiles, matcher,
            job_uuid="job", transcription_id=9, progress=Mock(), is_cancelled=lambda: True,
        )
