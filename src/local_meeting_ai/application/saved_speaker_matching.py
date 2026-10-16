"""Use the same saved voice identities for separate and integrated diarization."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from local_meeting_ai.domain.entities import DiarizationSegment
from local_meeting_ai.domain.protocols import (
    CancellationCheck,
    ProgressReporter,
    SpeakerProfileMatcher,
)
from local_meeting_ai.infrastructure.database.repositories import SpeakerProfileRepository

logger = logging.getLogger(__name__)


async def match_saved_speakers(
    audio_path: Path,
    turns: list[DiarizationSegment],
    config: dict[str, Any],
    profiles: SpeakerProfileRepository | None,
    matcher: SpeakerProfileMatcher | None,
    *,
    job_uuid: str,
    transcription_id: int,
    progress: ProgressReporter,
    is_cancelled: CancellationCheck,
) -> dict[int, Any]:
    enabled = bool(config.get("recognize_saved_speakers", True))
    saved = profiles.list() if profiles is not None and enabled else []
    usable = [profile for profile in saved if profile.sample_path]
    reason = (
        "recognition_disabled" if not enabled else
        "no_speaker_turns" if not turns else
        "profile_repository_unavailable" if profiles is None else
        "no_saved_profiles" if not saved else
        "no_saved_sample_paths" if not usable else
        "matcher_unavailable" if matcher is None else None
    )
    if reason is not None:
        logger.info(
            "Saved voice matching not run: job=%s transcription=%s reason=%s saved=%d samples=%d",
            job_uuid, transcription_id, reason, len(saved), len(usable),
        )
        return {}
    assert matcher is not None
    logger.info(
        "Saved voice matching started: job=%s transcription=%s saved=%d samples=%d "
        "speakers=%d threshold=%.3f",
        job_uuid, transcription_id, len(saved), len(usable), len({t.speaker for t in turns}),
        float(config.get("profile_match_threshold", 0.72)),
    )
    matched = await matcher.match(
        audio_path, turns, usable,
        {**config, "_voice_match_job_uuid": job_uuid,
         "_voice_match_transcription_id": transcription_id},
        progress=progress, is_cancelled=is_cancelled,
    )
    logger.info(
        "Saved voice matching finished: job=%s transcription=%s matched=%d speakers=%d",
        job_uuid, transcription_id, len(matched), len({t.speaker for t in turns}),
    )
    return matched
