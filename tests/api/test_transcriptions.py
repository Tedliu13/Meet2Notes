from __future__ import annotations

import asyncio
import io
import shutil
import threading
import time
import wave
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from local_meeting_ai.api.app import create_app
from local_meeting_ai.config import AppSettings
from local_meeting_ai.domain.entities import (
    DiarizationSegment,
    MediaProbe,
    SegmentDraft,
    TranscriptionEngineRequest,
    TranscriptionResult,
)
from local_meeting_ai.domain.protocols import (
    CancellationCheck,
    ProgressReporter,
    SegmentReporter,
)


class FakeNormalizer:
    async def normalize_for_transcription(
        self,
        source: Path,
        destination: Path,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        is_cancelled: CancellationCheck | None = None,
    ) -> MediaProbe:
        if is_cancelled and is_cancelled():
            raise RuntimeError("cancelled")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        await asyncio.sleep(0)
        return MediaProbe(
            format_name="wav",
            duration_ms=100,
            size_bytes=destination.stat().st_size,
            sample_rate=sample_rate,
            channels=channels,
            has_audio=True,
            has_video=False,
            metadata={"format": "wav"},
        )


class FakeTranscriptionEngine:
    name = "fake-whisper"

    def capability(self) -> dict[str, Any]:
        return {
            "engine": self.name,
            "available": True,
            "cuda_available": False,
            "installed_models": ["small"],
        }

    async def prepare(
        self,
        profile: Any,
        *,
        allow_model_download: bool,
    ) -> None:
        del profile, allow_model_download
        await asyncio.sleep(0)

    async def transcribe(
        self,
        request: TranscriptionEngineRequest,
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
        segment_ready: SegmentReporter,
    ) -> TranscriptionResult:
        assert request.audio_path.suffix == ".wav"
        assert request.compute_type == "int8"
        drafts = [
            SegmentDraft(
                index=0,
                start_ms=0,
                end_ms=1200,
                text="Welcome to the product review.",
                confidence=0.94,
            ),
            SegmentDraft(
                index=1,
                start_ms=1200,
                end_ms=2800,
                text="We approved the product launch.",
                confidence=0.91,
            ),
        ]
        for index, segment in enumerate(drafts):
            assert not is_cancelled()
            segment_ready(segment)
            progress((index + 1) / len(drafts), f"Segment {index + 1}")
            await asyncio.sleep(0)
        return TranscriptionResult(
            language="en",
            language_probability=0.99,
            duration_ms=2800,
            segments=drafts,
        )

    def shutdown(self) -> None:
        return None

    def unload(self) -> None:
        return None


class CompositeTranscriptionEngine(FakeTranscriptionEngine):
    async def transcribe(
        self,
        request: TranscriptionEngineRequest,
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
        segment_ready: SegmentReporter,
    ) -> TranscriptionResult:
        result = await super().transcribe(request, progress, is_cancelled, segment_ready)
        return TranscriptionResult(
            language=result.language,
            language_probability=result.language_probability,
            duration_ms=result.duration_ms,
            segments=result.segments,
            speaker_turns=[
                DiarizationSegment(start_ms=0, end_ms=1200, speaker=0),
                DiarizationSegment(start_ms=1200, end_ms=2800, speaker=1),
            ],
        )


def _wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 1600)
    return buffer.getvalue()


@contextmanager
def _client(
    tmp_path: Path,
    engine: FakeTranscriptionEngine | None = None,
) -> Iterator[TestClient]:
    settings = AppSettings(
        data_dir=tmp_path / "transcription-data",
        testing=True,
        open_browser=False,
        log_level="WARNING",
    )
    with TestClient(
        create_app(
            settings,
            transcription_engine=engine or FakeTranscriptionEngine(),
            audio_normalizer=FakeNormalizer(),
        )
    ) as client:
        yield client


def _wait_for_job(client: TestClient, job_uuid: str) -> dict[str, Any]:
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_uuid}").json()
        if job["status"] in {"completed", "failed", "cancelled"}:
            return job
        time.sleep(0.02)
    raise AssertionError("Job did not reach a terminal state")


@pytest.mark.parametrize("language", [None, "zh"])
def test_chinese_transcript_converts_stream_and_final_with_snapshot_setting(
    tmp_path: Path, language: str | None,
) -> None:
    released = threading.Event()

    class ChineseEngine(FakeTranscriptionEngine):
        async def transcribe(self, request, progress, is_cancelled, segment_ready):
            segment = SegmentDraft(
                index=0, start_ms=10, end_ms=900, text="我们讨论气候变化。",
                metadata={"detected_language": "zh"},
            )
            segment_ready(segment)
            while not released.is_set():
                await asyncio.sleep(0.005)
            return TranscriptionResult(
                language="zh", language_probability=0.99, duration_ms=1000, segments=[segment],
            )

    with _client(tmp_path, ChineseEngine()) as client:
        response = client.put("/api/settings", json={"chinese_transcript_script": "traditional"})
        assert response.status_code == 200
        assert response.json()["chinese_transcript_script"] == "traditional"
        invalid = client.put("/api/settings", json={"chinese_transcript_script": "invalid"})
        assert invalid.status_code == 422
        invalid = client.put("/api/settings", json={"chinese_transcript_script": None})
        assert invalid.status_code == 422
        meeting = client.post("/api/meetings", json={"title": "Chinese meeting"}).json()
        client.post(f"/api/meetings/{meeting['id']}/import",
                    files={"file": ("meeting.wav", _wav_bytes(), "audio/wav")})
        response = client.post(f"/api/meetings/{meeting['id']}/transcriptions",
                               json={"profile_id": "balanced", "language": language})
        assert response.status_code == 202, response.text
        started = response.json()
        transcript_id = started["transcription"]["id"]
        try:
            for _ in range(200):
                detail = client.get(f"/api/transcriptions/{transcript_id}").json()
                if detail["segments"]:
                    break
                time.sleep(0.01)
            assert detail["segments"][0]["text"] == "我們討論氣候變化。"
            assert detail["segments"][0]["is_final"] is False
            # Another browser changing settings must not change an in-flight job.
            client.put("/api/settings", json={"chinese_transcript_script": "original"})
        finally:
            released.set()
        assert _wait_for_job(client, started["job"]["uuid"])["status"] == "completed"
        detail = client.get(f"/api/transcriptions/{transcript_id}").json()
        segment = detail["segments"][0]
        assert segment["text"] == "我們討論氣候變化。"
        assert segment["metadata"]["transcription_original_text"] == "我们讨论气候变化。"
        assert (segment["start_ms"], segment["end_ms"]) == (10, 900)
        assert segment["is_final"] is True
        assert detail["transcription"]["settings"]["chinese_transcript_script"] == "traditional"
        assert client.get("/api/settings").json()["chinese_transcript_script"] == "original"


def test_transcription_pipeline_editor_and_versions(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        meeting = client.post("/api/meetings", json={"title": "Product review"}).json()
        imported = client.post(
            f"/api/meetings/{meeting['id']}/import",
            files={"file": ("review.wav", _wav_bytes(), "audio/wav")},
        )
        assert imported.status_code == 202
        recording = imported.json()["recording"]

        profiles = client.get("/api/models/transcription")
        assert profiles.status_code == 200
        balanced = next(item for item in profiles.json() if item["id"] == "balanced")
        assert balanced["installed"] is True
        assert balanced["compute_type"] == "int8"

        started = client.post(
            f"/api/meetings/{meeting['id']}/transcriptions",
            json={"profile_id": "balanced", "language": "en"},
        )
        assert started.status_code == 202
        started_payload = started.json()
        terminal = _wait_for_job(client, started_payload["job"]["uuid"])
        assert terminal["status"] == "completed"
        assert terminal["result"]["segment_count"] == 2

        transcription_id = started_payload["transcription"]["id"]
        detail = client.get(f"/api/transcriptions/{transcription_id}")
        assert detail.status_code == 200
        assert detail.json()["transcription"]["is_active"] is True
        assert detail.json()["transcription"]["title"] == "New Transcription"
        assert [segment["text"] for segment in detail.json()["segments"]] == [
            "Welcome to the product review.",
            "We approved the product launch.",
        ]
        assert all(segment["is_final"] for segment in detail.json()["segments"])
        assert 'data-default-title="New Transcription 2"' in client.get("/").text

        renamed = client.patch(
            f"/api/transcriptions/{transcription_id}",
            json={"title": "Product launch decision"},
        )
        assert renamed.status_code == 200
        assert renamed.json()["title"] == "Product launch decision"
        assert client.get(f"/api/meetings/{meeting['id']}").json()["title"] == (
            "Product launch decision"
        )
        assert 'data-default-title="New Transcription"' in client.get("/").text

        first_segment = detail.json()["segments"][0]
        edited = client.patch(
            f"/api/transcript-segments/{first_segment['id']}",
            json={"text": "Welcome to the release review."},
        )
        assert edited.status_code == 200
        assert edited.json()["text"] == "Welcome to the release review."

        replaced = client.post(
            f"/api/transcriptions/{transcription_id}/find-replace",
            json={"find": "product", "replacement": "release", "case_sensitive": False},
        )
        assert replaced.status_code == 200
        assert replaced.json()["replacements"] == 1

        media = client.get(f"/api/recordings/{recording['id']}/media")
        assert media.status_code == 200
        assert media.content.startswith(b"RIFF")

        versions = client.get(f"/api/meetings/{meeting['id']}/transcriptions")
        assert versions.status_code == 200
        assert versions.json()[0]["segment_count"] == 2

        workspace = client.get(f"/?meeting={meeting['id']}")
        assert workspace.status_code == 200
        assert 'id="workspace-meeting-select"' not in workspace.text
        assert 'id="start-transcription"' in workspace.text
        assert 'id="transcription-dialog"' in workspace.text
        assert "Choose your audio source" in workspace.text
        assert "Import media" not in workspace.text
        assert 'href="/?new=1"' in workspace.text
        assert "New meeting" in workspace.text
        assert 'href="/prompt"' in workspace.text
        assert "Prompt" in workspace.text
        assert 'href="/settings"' in workspace.text
        assert "minimal-settings-link" not in workspace.text
        assert 'id="postprocess-dialog"' in workspace.text
        assert 'data-meeting-tab="transcript"' in workspace.text
        assert 'data-meeting-tab="speakers"' in workspace.text
        assert 'data-meeting-tab="intelligence"' in workspace.text
        assert 'data-meeting-tab="utilities"' in workspace.text
        assert 'id="speaker-rebuild-identification"' in workspace.text
        assert 'id="speaker-rebuild-dialog"' in workspace.text
        assert 'id="ai-toggle-view"' in workspace.text
        assert "Who spoke during the meeting" not in workspace.text
        assert "Use your meeting outside Meet2Notes" not in workspace.text
        assert 'id="export-dialog"' in workspace.text
        assert 'data-audio-export="mp3"' in workspace.text
        assert 'data-export-source="transcript" data-export-format="markdown"' in workspace.text
        assert 'data-export-source="notes" data-export-format="pdf"' in workspace.text
        assert 'id="download-meeting-json"' in workspace.text

        library = client.get("/meetings")
        assert library.status_code == 200
        assert "Product launch decision" in library.text
        assert "Product review" not in library.text
        assert f'href="/?meeting={meeting["id"]}"' in library.text

        meeting_redirect = client.get(
            f"/meetings/{meeting['id']}",
            follow_redirects=False,
        )
        assert meeting_redirect.status_code == 307
        assert meeting_redirect.headers["location"] == f"/?meeting={meeting['id']}"


def test_meeting_audio_can_be_deleted_without_removing_transcript(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        meeting = client.post("/api/meetings", json={"title": "Keep the text"}).json()
        imported = client.post(
            f"/api/meetings/{meeting['id']}/import",
            files={"file": ("private.wav", _wav_bytes(), "audio/wav")},
        ).json()
        _wait_for_job(client, imported["job"]["uuid"])
        started = client.post(
            f"/api/meetings/{meeting['id']}/transcriptions",
            json={"profile_id": "balanced", "language": "en"},
        ).json()
        _wait_for_job(client, started["job"]["uuid"])

        recordings_before = client.get(
            f"/api/meetings/{meeting['id']}/recordings"
        ).json()
        assert {item["role"] for item in recordings_before} == {"original", "normalized"}

        deleted = client.delete(f"/api/meetings/{meeting['id']}/audio")

        assert deleted.status_code == 200
        assert deleted.json()["audio_deleted_at"]
        assert deleted.json()["audio_deleted_bytes"] > 0
        assert deleted.json()["recording_count"] == 0
        assert client.get(f"/api/meetings/{meeting['id']}/recordings").json() == []
        detail = client.get(
            f"/api/transcriptions/{started['transcription']['id']}"
        ).json()
        assert [segment["text"] for segment in detail["segments"]] == [
            "Welcome to the product review.",
            "We approved the product launch.",
        ]
        assert "Keep the text" in client.get("/meetings").text
        assert "Audio deleted" in client.get("/meetings").text


def test_model_download_requires_explicit_confirmation(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        meeting = client.post("/api/meetings", json={"title": "Download consent"}).json()
        client.post(
            f"/api/meetings/{meeting['id']}/import",
            files={"file": ("review.wav", _wav_bytes(), "audio/wav")},
        )

        blocked = client.post(
            f"/api/meetings/{meeting['id']}/transcriptions",
            json={"profile_id": "fast", "allow_model_download": False},
        )
        assert blocked.status_code == 409
        assert "Confirm the model download" in blocked.json()["detail"]

        confirmed = client.post(
            f"/api/meetings/{meeting['id']}/transcriptions",
            json={"profile_id": "fast", "allow_model_download": True},
        )
        assert confirmed.status_code == 202


def test_composite_asr_persists_integrated_speaker_turns(tmp_path: Path) -> None:
    with _client(tmp_path, CompositeTranscriptionEngine()) as client:
        meeting = client.post("/api/meetings", json={"title": "Composite ASR"}).json()
        client.post(
            f"/api/meetings/{meeting['id']}/import",
            files={"file": ("composite.wav", _wav_bytes(), "audio/wav")},
        )
        started = client.post(
            f"/api/meetings/{meeting['id']}/transcriptions",
            json={"profile_id": "balanced", "language": "en"},
        ).json()

        terminal = _wait_for_job(client, started["job"]["uuid"])
        detail = client.get(
            f"/api/transcriptions/{started['transcription']['id']}"
        ).json()

        assert terminal["result"]["speaker_count"] == 2
        assert terminal["result"]["assigned_segments"] == 2
        assert [segment["speaker_id"] is not None for segment in detail["segments"]] == [
            True,
            True,
        ]


@pytest.mark.parametrize("integrated", [True, False])
@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("selected", [True, False])
def test_existing_voice_is_loaded_and_applied_to_a_later_meeting(
    tmp_path: Path, monkeypatch: Any, integrated: bool, enabled: bool, selected: bool,
) -> None:
    from local_meeting_ai.adapters.diarization.profile_matching import (
        SherpaOnnxSpeakerProfileMatcher,
    )

    calls = []

    async def match(self, audio_path, turns, profiles, config, **kwargs):
        calls.append((audio_path, turns, profiles, config))
        return {0: profiles[0]}

    monkeypatch.setattr(SherpaOnnxSpeakerProfileMatcher, "match", match)
    engine = CompositeTranscriptionEngine() if integrated else FakeTranscriptionEngine()
    with _client(tmp_path, engine) as client:
        container = client.app.state.container
        sample = container.storage.speaker_profile_path(1)
        sample.parent.mkdir(parents=True, exist_ok=True)
        sample.write_bytes(_wav_bytes())
        if selected:
            container.speaker_profiles.create(name="Not invited", sample_path=str(sample))
        profile = container.speaker_profiles.create(name="已儲存人物", sample_path=str(sample))
        client.put("/api/settings", json={"diarization": {"recognize_saved_speakers": enabled}})
        ids = [profile.id] if selected else []
        meeting = client.post("/api/meetings", json={
            "title": "Later meeting", "known_speaker_profile_ids": ids,
        }).json()
        stored_meeting = client.get(f"/api/meetings/{meeting['id']}").json()
        assert stored_meeting["known_speaker_profile_ids"] == ids
        client.post(f"/api/meetings/{meeting['id']}/import",
                    files={"file": ("later.wav", _wav_bytes(), "audio/wav")})
        started = client.post(
            f"/api/meetings/{meeting['id']}/transcriptions",
            json={"profile_id": "balanced", "language": "en"},
        ).json()
        terminal = _wait_for_job(client, started["job"]["uuid"])
        transcription_id = started["transcription"]["id"]
        if not integrated:
            original = container.diarization_service.engine
            container.diarization_service.engine = SimpleNamespace(
                capability=lambda: {"available": True, "installed": True},
                diarize=AsyncMock(return_value=[
                    DiarizationSegment(0, 1200, 0), DiarizationSegment(1200, 2800, 1),
                ]),
            )
            try:
                job = client.post(f"/api/transcriptions/{transcription_id}/diarize", json={}).json()
                terminal = _wait_for_job(client, job["uuid"])
            finally:
                container.diarization_service.engine = original
        assert terminal["status"] == "completed", terminal
        assert terminal["result"]["matched_speaker_count"] == int(enabled)
        detail = client.get(f"/api/transcriptions/{transcription_id}").json()
        linked = [speaker for speaker in detail["speakers"] if speaker["profile_id"] == profile.id]
        assert len(linked) == int(enabled)
        if enabled:
            assert len(calls) == 1
            audio_path, turns, forwarded_profiles, config = calls[0]
            assert {turn.speaker for turn in turns} == {0, 1}
            normalized = container.recordings.latest_for_role(meeting["id"], "normalized")
            assert audio_path == Path(normalized.local_path)
            assert forwarded_profiles[0].id == profile.id
            assert len(forwarded_profiles) == 1
            assert forwarded_profiles[0].sample_path == str(sample)
            assert config["_voice_match_transcription_id"] == transcription_id
            assert linked[0]["display_name"] == profile.name
            assert sum(segment["speaker_id"] == linked[0]["id"]
                       for segment in detail["segments"]) == 1
        else:
            assert calls == []


@pytest.mark.parametrize("integrated", [True, False])
def test_liveness_responds_while_speaker_assignment_waits(tmp_path, monkeypatch, integrated):
    entered, release = threading.Event(), threading.Event()
    engine = CompositeTranscriptionEngine() if integrated else FakeTranscriptionEngine()
    with _client(tmp_path, engine) as client:
        container = client.app.state.container
        original_assignment = container.transcriptions.assign_diarization

        def blocked_assignment(**kwargs):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("Test assignment was not released")
            return original_assignment(**kwargs)

        monkeypatch.setattr(container.transcriptions, "assign_diarization", blocked_assignment)
        meeting = client.post("/api/meetings", json={"title": "Busy assignment"}).json()
        client.post(f"/api/meetings/{meeting['id']}/import",
                    files={"file": ("meeting.wav", _wav_bytes(), "audio/wav")})
        started = client.post(f"/api/meetings/{meeting['id']}/transcriptions",
                              json={"profile_id": "balanced", "language": "en"}).json()
        job_uuid = started["job"]["uuid"]
        original_engine = container.diarization_service.engine
        try:
            if not integrated:
                assert _wait_for_job(client, job_uuid)["status"] == "completed"
                container.diarization_service.engine = SimpleNamespace(
                    capability=lambda: {"available": True, "installed": True},
                    diarize=AsyncMock(return_value=[DiarizationSegment(0, 2800, 0)]),
                )
                response = client.post(
                    f"/api/transcriptions/{started['transcription']['id']}/diarize", json={},
                )
                assert response.status_code == 202
                job_uuid = response.json()["uuid"]
            assert entered.wait(2)
            with ThreadPoolExecutor(max_workers=1) as requests:
                try:
                    response = requests.submit(client.get, "/api/health/live").result(timeout=2)
                    assert response.status_code == 200
                    assert not release.is_set()
                finally:
                    release.set()
            assert _wait_for_job(client, job_uuid)["status"] == "completed"
        finally:
            release.set()
            container.diarization_service.engine = original_engine
