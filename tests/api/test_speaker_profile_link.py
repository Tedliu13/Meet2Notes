from local_meeting_ai.domain.entities import DiarizationSegment, SegmentDraft


def setup_speakers(client):
    container = client.app.state.container
    meeting = client.post("/api/meetings", json={"title": "Identity test"}).json()
    transcript = container.transcriptions.create(
        meeting_id=meeting["id"], title="Test", engine="test", model="test",
        language="zh", settings={},
    )
    container.transcriptions.complete(
        transcript.id, language="zh",
        segments=[SegmentDraft(index=0, start_ms=0, end_ms=1000, text="甲"),
                  SegmentDraft(index=1, start_ms=1000, end_ms=2000, text="乙")],
    )
    container.transcriptions.assign_diarization(
        meeting_id=meeting["id"], transcription_id=transcript.id,
        diarization=[DiarizationSegment(0, 1000, 0), DiarizationSegment(1000, 2000, 1)],
        minimum_overlap_ratio=0.5,
    )
    profile = container.speaker_profiles.create(name="同一人物", sample_path=None)
    sample = container.storage.speaker_profile_path(profile.id)
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_bytes(b"retained voice sample")
    container.speaker_profiles.update(profile.id, sample_path=str(sample))
    speakers = container.transcriptions.speakers_for_transcription(transcript.id)
    return container, transcript, profile, sample, speakers


def test_two_speakers_link_to_one_identity_without_changing_samples_or_segments(client):
    container, transcript, profile, sample, speakers = setup_speakers(client)
    with container.database.read() as connection:
        before = [tuple(row) for row in connection.execute(
            "SELECT id, speaker_id, start_ms, end_ms, text FROM transcript_segments"
        )]
    for speaker in [*speakers, speakers[0]]:  # Repeating the assignment is safe.
        response = client.post(
            f"/api/transcriptions/{transcript.id}/speakers/{speaker.id}/profile",
            json={"profile_id": profile.id},
        )
        assert response.status_code == 200
        assert response.json()["display_name"] == "同一人物"
        assert response.json()["profile_id"] == profile.id
    assert sample.read_bytes() == b"retained voice sample"
    assert len(container.speaker_profiles.list()) == 1
    assert container.speaker_profiles.list()[0].meeting_count == 1
    with container.database.read() as connection:
        after = [tuple(row) for row in connection.execute(
            "SELECT id, speaker_id, start_ms, end_ms, text FROM transcript_segments"
        )]
    assert after == before
    detail = client.get(f"/api/transcriptions/{transcript.id}").json()
    assert all(speaker["profile_id"] == profile.id for speaker in detail["speakers"])


def test_link_validates_target_and_does_not_mutate_on_failure(client):
    container, transcript, profile, sample, speakers = setup_speakers(client)
    other_meeting = client.post("/api/meetings", json={"title": "Other"}).json()
    other = container.transcriptions.create(
        meeting_id=other_meeting["id"], title="Other", engine="test", model="test",
        language=None, settings={},
    )
    assert client.post(
        f"/api/transcriptions/{other.id}/speakers/{speakers[0].id}/profile",
        json={"profile_id": profile.id},
    ).status_code == 404
    path = f"/api/transcriptions/{transcript.id}/speakers/{speakers[0].id}/profile"
    assert client.post(path, json={"profile_id": 999999}).status_code == 404
    assert client.post(path, json={"profile_id": 0}).status_code == 422
    unassigned = container.transcriptions.create(
        meeting_id=transcript.meeting_id, title="Unassigned", engine="test", model="test",
        language=None, settings={},
    )
    assert client.post(
        f"/api/transcriptions/{unassigned.id}/speakers/{speakers[0].id}/profile",
        json={"profile_id": profile.id},
    ).status_code == 404
    sample.unlink()
    assert client.post(path, json={"profile_id": profile.id}).status_code == 404
    assert container.transcriptions.get_speaker(speakers[0].id).profile_id is None


def test_person_assignment_can_be_corrected_without_replacing_either_sample(client):
    container, transcript, profile, sample, speakers = setup_speakers(client)
    second = container.speaker_profiles.create(name="正確人物", sample_path=None)
    second_sample = container.storage.speaker_profile_path(second.id)
    second_sample.write_bytes(b"another saved voice")
    container.speaker_profiles.update(second.id, sample_path=str(second_sample))
    path = f"/api/transcriptions/{transcript.id}/speakers/{speakers[0].id}/profile"
    for target in (profile, second):
        assert client.post(path, json={"profile_id": target.id}).status_code == 200
    speaker = container.transcriptions.get_speaker(speakers[0].id)
    assert speaker.profile_id == second.id
    assert speaker.display_name == second.name
    assert sample.read_bytes() == b"retained voice sample"
    assert second_sample.read_bytes() == b"another saved voice"
