import threading
from concurrent.futures import ThreadPoolExecutor

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


def test_overlap_computation_allows_other_writes_and_rechecks_edited_spans(client, monkeypatch):
    container = client.app.state.container
    meeting = client.post("/api/meetings", json={"title": "Concurrent edit"}).json()
    transcript = container.transcriptions.create(
        meeting_id=meeting["id"], title="Test", engine="test", model="test",
        language="zh", settings={},
    )
    container.transcriptions.complete(
        transcript.id, language="zh",
        segments=[SegmentDraft(index=i, start_ms=i * 1000, end_ms=(i + 1) * 1000,
                               text=str(i)) for i in range(2)],
    )
    entered, release = threading.Event(), threading.Event()
    original = container.transcriptions._speaker_for_span

    def blocked_computation(*args):
        if not entered.is_set():
            entered.set()
            assert release.wait(10)
        return original(*args)

    def edit_while_computing():
        with container.database.transaction() as connection:
            connection.execute(
                "UPDATE transcript_segments SET start_ms=3000, end_ms=4000 "
                "WHERE transcription_id=? AND segment_index=0", (transcript.id,),
            )

    monkeypatch.setattr(container.transcriptions, "_speaker_for_span", blocked_computation)
    with ThreadPoolExecutor(max_workers=2) as workers:
        assignment = workers.submit(
            container.transcriptions.assign_diarization,
            meeting_id=meeting["id"], transcription_id=transcript.id,
            diarization=[DiarizationSegment(0, 1000, 0), DiarizationSegment(1000, 2000, 1)],
            minimum_overlap_ratio=0.5,
        )
        try:
            assert entered.wait(2)
            workers.submit(edit_while_computing).result(timeout=2)
        finally:
            release.set()
        assert assignment.result(timeout=2) == 1
    detail = client.get(f"/api/transcriptions/{transcript.id}").json()
    assert detail["segments"][0]["start_ms"] == 3000
    assert detail["segments"][0]["speaker_id"] is None
    assert detail["segments"][1]["speaker_id"] is not None
