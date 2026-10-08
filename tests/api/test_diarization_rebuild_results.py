from local_meeting_ai.domain.entities import DiarizationSegment, SegmentDraft


def test_repeated_diarization_replaces_speaker_results_exposed_by_api(client):
    container = client.app.state.container
    meeting = client.post("/api/meetings", json={"title": "Rebuild results"}).json()
    transcript = container.transcriptions.create(
        meeting_id=meeting["id"], title="Test", engine="test", model="test",
        language="zh", settings={},
    )
    container.transcriptions.complete(
        transcript.id, language="zh",
        segments=[SegmentDraft(index=i, start_ms=i * 1000, end_ms=(i + 1) * 1000,
                               text=f"片段 {i}") for i in range(10)],
    )
    # Persist actual returned turns, including a later run whose output is six
    # speakers even though the user may have requested ten clusters.
    for count in (6, 10, 6):
        assigned = container.transcriptions.assign_diarization(
            meeting_id=meeting["id"], transcription_id=transcript.id,
            diarization=[DiarizationSegment(i * 1000, (i + 1) * 1000, i % count)
                         for i in range(10)],
            minimum_overlap_ratio=0.5,
        )
        detail = client.get(f"/api/transcriptions/{transcript.id}").json()
        assert assigned == 10
        assert len(detail["speakers"]) == count
        assert len({segment["speaker_id"] for segment in detail["segments"]}) == count
        assert [segment["text"] for segment in detail["segments"]] == [
            f"片段 {i}" for i in range(10)
        ]
