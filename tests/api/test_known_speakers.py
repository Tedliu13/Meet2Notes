from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from local_meeting_ai.domain.entities import DiarizationSegment
from local_meeting_ai.infrastructure.jobs import JobContext


def rebuild_source(client):
    container = client.app.state.container
    first = container.speaker_profiles.create(name="Original candidate", sample_path="first.wav")
    second = container.speaker_profiles.create(name="Rebuild candidate", sample_path="second.wav")
    meeting = client.post("/api/meetings", json={
        "title": "Rebuild", "known_speaker_profile_ids": [first.id],
    }).json()
    transcription = container.transcriptions.create(
        meeting_id=meeting["id"], title="Existing", engine="test", model="test",
        language="zh", settings={},
    )
    container.transcriptions.complete(transcription.id, language="zh", segments=[])
    container.recordings.create(
        meeting_id=meeting["id"], role="normalized", local_path="normalized.wav",
        original_filename="meeting.wav", media_type="audio/wav", size_bytes=0, sha256="test",
    )
    return container, meeting, transcription, first, second


@pytest.mark.parametrize("mode", [
    "selected", "all", "default", "deleted_after_enqueue", "disabled",
])
def test_rebuild_uses_its_selected_roster_without_modifying_meeting(client, monkeypatch, mode):
    container, meeting, transcription, first, second = rebuild_source(client)
    service = container.diarization_service
    monkeypatch.setattr(service, "capability", lambda: {"available": True, "installed": True})
    monkeypatch.setattr(service.queue, "submit", AsyncMock())
    monkeypatch.setattr(service.engine, "diarize", AsyncMock(
        return_value=[DiarizationSegment(0, 3000, 0)],
    ))
    matcher = SimpleNamespace(match=AsyncMock(return_value={}))
    monkeypatch.setattr(service, "profile_matcher", matcher)
    if mode == "disabled":
        service.preferences.update({"diarization": {"recognize_saved_speakers": False}})
    payload = {"speaker_count": 7}
    if mode != "default":
        payload["known_speaker_profile_ids"] = [] if mode == "all" else [second.id, second.id]
    response = client.post(f"/api/transcriptions/{transcription.id}/diarize", json=payload)
    assert response.status_code == 202, response.text
    job = container.jobs.get(response.json()["uuid"])
    assert job
    if mode == "deleted_after_enqueue":
        container.speaker_profiles.delete(second.id)
    asyncio.run(service.process(job, JobContext(job.uuid, container.jobs)))
    assert container.meetings.get(meeting["id"]).known_speaker_profile_ids == [first.id]
    if mode in {"deleted_after_enqueue", "disabled"}:
        matcher.match.assert_not_called()
    else:
        expected = [first.id, second.id] if mode == "all" else [
            first.id if mode == "default" else second.id
        ]
        assert [profile.id for profile in matcher.match.call_args.args[2]] == expected
    assert service.engine.diarize.call_args.args[1]["num_speakers"] == 7


def test_rebuild_rejects_invalid_or_unavailable_voices_without_creating_job(client, monkeypatch):
    container, _, transcription, _, _ = rebuild_source(client)
    service = container.diarization_service
    monkeypatch.setattr(service, "capability", lambda: {"available": True, "installed": True})
    incomplete = container.speaker_profiles.create(name="No voice", sample_path=None)
    for ids in ([True], [-1], [1] * 101, [999999], [incomplete.id]):
        response = client.post(f"/api/transcriptions/{transcription.id}/diarize", json={
            "known_speaker_profile_ids": ids,
        })
        assert response.status_code == 422, response.text
    assert list(container.jobs.list(meeting_id=transcription.meeting_id)) == []


def test_roster_is_validated_atomically_persisted_and_survives_profile_deletion(client):
    container = client.app.state.container
    profile = container.speaker_profiles.create(name="Known person", sample_path="saved.wav")
    incomplete = container.speaker_profiles.create(name="No sample", sample_path=None)
    before = len(container.meetings.list())
    for ids in ([999999], [incomplete.id], [True], [-1], [profile.id] * 101):
        response = client.post(
            "/api/meetings",
            json={
                "title": "Invalid roster",
                "known_speaker_profile_ids": ids,
            },
        )
        assert response.status_code == 422, response.text
    assert len(container.meetings.list()) == before
    created = client.post(
        "/api/meetings",
        json={
            "title": "Known voices",
            "known_speaker_profile_ids": [profile.id, profile.id],
        },
    )
    assert created.status_code == 201
    mid = created.json()["id"]
    assert created.json()["known_speaker_profile_ids"] == [profile.id]
    assert container.meetings.get(mid).known_speaker_profile_ids == [profile.id]
    container.speaker_profiles.delete(profile.id)
    assert client.get(f"/api/meetings/{mid}").json()["known_speaker_profile_ids"] == [profile.id]
    assert (
        client.post("/api/meetings", json={"title": "Legacy behavior"}).json()[
            "known_speaker_profile_ids"
        ]
        == []
    )
