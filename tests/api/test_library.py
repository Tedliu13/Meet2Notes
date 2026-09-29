from __future__ import annotations

from fastapi.testclient import TestClient

from local_meeting_ai.domain.entities import SegmentDraft
from local_meeting_ai.domain.enums import SourceType
from local_meeting_ai.infrastructure.database.library import LibraryRepository


def completed(client: TestClient, title: str, text: str) -> tuple[int, int]:
    container = client.app.state.container
    meeting = container.meetings.create(
        title=title,
        description=None,
        source_type=SourceType.MANUAL,
        language="en",
    )
    transcription = container.transcriptions.create(
        meeting_id=meeting.id,
        title=title,
        engine="test",
        model="test",
        language="en",
        settings={},
    )
    container.transcriptions.complete(
        transcription.id,
        language="en",
        segments=[
            SegmentDraft(index=0, start_ms=42000, end_ms=45000, text=text),
        ],
    )
    return meeting.id, transcription.id


def test_library_search_finds_spoken_words_and_tracks_edits(client: TestClient) -> None:
    meeting_id, transcription_id = completed(client, "Friday meeting", "Café launch is approved.")
    other_id, _ = completed(client, "Launch overview", "The unrelated topic.")
    response = client.get("/api/library", params={"query": "cafe launch", "scope": "transcript"})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    item = response.json()["items"][0]
    assert item["id"] == meeting_id
    hit = item["matches"][0]
    assert hit["start_ms"] == 42000
    assert hit["text"] == "Café launch is approved."
    assert client.get("/api/library", params={"query": "launch"}).json()["total"] == 2
    title_only = client.get("/api/library", params={"query": "launch", "scope": "title"}).json()
    assert [item["id"] for item in title_only["items"]] == [other_id]

    container = client.app.state.container
    container.transcriptions.update_segment(hit["segment_id"], "Budget approved instead.")
    assert client.get("/api/library", params={"query": "cafe"}).json()["total"] == 0
    assert client.get("/api/library", params={"query": "budget"}).json()["total"] == 1
    replacement = container.transcriptions.create(
        meeting_id=meeting_id,
        title="New version",
        engine="test",
        model="test",
        language="en",
        settings={},
    )
    container.transcriptions.complete(
        replacement.id,
        language="en",
        segments=[
            SegmentDraft(index=0, start_ms=0, end_ms=2000, text="A corrected transcript."),
        ],
    )
    assert client.get("/api/library", params={"query": "budget"}).json()["total"] == 0
    container.transcriptions.activate(transcription_id)
    assert client.get("/api/library", params={"query": "budget"}).json()["total"] == 1
    client.delete(f"/api/meetings/{meeting_id}")
    assert client.get("/api/library", params={"query": "budget"}).json()["total"] == 0


def test_tags_persist_filter_and_validate_atomically(client: TestClient) -> None:
    meeting_id, _ = completed(client, "Product review", "Ready for launch.")
    completed(client, "Sales review", "Ready for launch.")
    tag = client.post("/api/tags", json={"name": "  Product   Team  "}).json()
    assert tag["name"] == "Product Team"
    assert client.post("/api/tags", json={"name": "product team"}).status_code == 422
    url = f"/api/meetings/{meeting_id}/tags"
    assert client.put(url, json={"tag_ids": [tag["id"], tag["id"]]}).status_code == 200
    assert len(client.get(url).json()) == 1
    assert client.put(url, json={"tag_ids": [999999]}).status_code == 404
    assert client.get(url).json()[0]["id"] == tag["id"]
    assert client.put(url, json={"tag_ids": [True]}).status_code == 422
    assert client.put(url, json={"tag_ids": list(range(1, 32))}).status_code == 422
    # A new repository connection observes the saved association.
    repository = LibraryRepository(client.app.state.container.database)
    assert repository.tags(meeting_id)[0]["id"] == tag["id"]
    filtered = client.get("/api/library", params={"query": "launch", "tag_id": tag["id"]}).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["id"] == meeting_id
    assert client.patch(f"/api/tags/{tag['id']}", json={"name": "Marketing"}).status_code == 200
    assert client.get(url).json()[0]["name"] == "Marketing"
    assert client.delete(f"/api/tags/{tag['id']}").status_code == 204
    assert client.get(url).json() == []
    assert client.get(f"/api/meetings/{meeting_id}").status_code == 200
    assert client.get("/api/meetings/999999/tags").status_code == 404


def test_library_pagination_and_untrusted_queries(client: TestClient) -> None:
    for title in ["One", "Two", "Three"]:
        completed(client, title, 'Budget <script>alert("x")</script>')
    first = client.get("/api/library", params={"query": "budget", "limit": 2}).json()
    second = client.get("/api/library", params={"query": "budget", "limit": 2, "offset": 2}).json()
    assert first["total"] == second["total"] == 3
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert {x["id"] for x in first["items"]}.isdisjoint(x["id"] for x in second["items"])
    for query in ['" OR *', '"; DROP TABLE meetings; --', "*", "(budget)", "a", "预算"]:
        assert client.get("/api/library", params={"query": query}).status_code == 200
    assert client.get("/api/library").json()["total"] == 3
    assert client.get("/api/library", params={"limit": 101}).status_code == 422
    assert client.get("/api/library", params={"scope": "anything"}).status_code == 422


def test_quick_actions_roundtrip_and_validation(client: TestClient) -> None:
    payload = {"name": "  Launch brief  ", "prompt": " Summarize decisions and cite evidence. "}
    response = client.post("/api/assistant/actions", json=payload)
    assert response.status_code == 201
    item = response.json()
    assert item["name"] == "Launch brief"
    url = f"/api/assistant/actions/{item['id']}"
    assert client.get("/api/assistant/actions").json() == [item]
    repository = LibraryRepository(client.app.state.container.database)
    assert repository.actions() == [item]
    assert client.patch(url, json={"name": "Updated", "prompt": "List risks."}).status_code == 200
    assert client.get("/api/assistant/actions").json()[0]["prompt"] == "List risks."
    for bad in [
        {"name": " ", "prompt": "text"},
        {"name": "name", "prompt": " "},
        {"name": "name", "prompt": "x" * 4001},
    ]:
        assert client.post("/api/assistant/actions", json=bad).status_code == 422
    assert client.delete(url).status_code == 204
    assert client.get("/api/assistant/actions").json() == []
    assert client.patch(url, json=payload).status_code == 404
    assert client.delete(url).status_code == 404


def test_tag_links_are_removed_with_meetings(client: TestClient) -> None:
    meeting_id, _ = completed(client, "Example", "A sample.")
    tag = client.post("/api/tags", json={"name": "Sample"}).json()
    client.put(f"/api/meetings/{meeting_id}/tags", json={"tag_ids": [tag["id"]]})
    assert client.get("/api/tags").json()[0]["meeting_count"] == 1
    assert client.delete(f"/api/meetings/{meeting_id}").status_code == 204
    assert client.get("/api/tags").json()[0]["meeting_count"] == 0
