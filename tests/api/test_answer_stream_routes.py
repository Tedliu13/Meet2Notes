from __future__ import annotations

import json
from types import SimpleNamespace

from local_meeting_ai.domain.entities import SummaryResult
from local_meeting_ai.domain.enums import SourceType


def test_prompt_stream_preserves_final_metadata_and_json_endpoint(client, monkeypatch):
    async def summarize(transcript, config, progress, is_cancelled):
        assert not is_cancelled()
        if getattr(progress, "on_token", None) and config["streaming"]:
            progress.on_token("**Hello")
            progress.on_token(" world**")
        return SummaryResult(content_markdown="**Hello world**")
    monkeypatch.setattr(
        client.app.state.container.prompt_service.summary_engine, "summarize", summarize,
    )
    payload = {"question": "Hello", "use_rag": False, "history": []}
    response = client.post("/api/prompt/stream", json=payload)
    assert response.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in response.text.splitlines()]
    source_index = next(index for index, event in enumerate(events) if event["type"] == "sources")
    assert events[source_index] == {"type": "sources", "citations": []}
    assert source_index < next(
        index for index, event in enumerate(events) if event["type"] == "delta"
    )
    assert "".join(item["text"] for item in events if item["type"] == "delta") == "**Hello world**"
    final = events[-1]
    assert final["type"] == "done"
    assert final["result"]["answer"] == "**Hello world**"
    assert "sources" in final["result"] and "context_usage" in final["result"]
    assert client.post("/api/prompt", json=payload).json()["answer"] == "**Hello world**"
    saved = client.put("/api/settings", json={"summary_engine": {"streaming": False}})
    assert saved.status_code == 200
    assert client.get("/api/settings").json()["summary_engine"]["streaming"] is False
    response = client.post("/api/prompt/stream", json=payload)
    assert '"delta"' not in response.text and '"done"' in response.text


def test_stream_error_is_terminal_and_not_a_success(client, monkeypatch):
    async def fail(*args, **kwargs):
        kwargs["progress"].on_token("Partial answer")
        raise ValueError("Provider disconnected")
    monkeypatch.setattr(client.app.state.container.prompt_service, "ask", fail)
    response = client.post("/api/prompt/stream", json={"question": "Hello"})
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[-1] == {"type": "error", "message": "Provider disconnected"}
    assert not any(item["type"] == "done" for item in events)


def test_live_question_streams_text_and_persists_only_complete_answer(client, monkeypatch):
    container = client.app.state.container
    config = client.get("/api/live-assistant").json()["settings"]
    config.update(enabled=True, streaming=True, preload_on_start=False)
    assert client.put("/api/live-assistant/settings", json=config).status_code == 200
    meeting = container.meetings.create(
        title="Streaming test", description=None, source_type=SourceType.MANUAL, language="en",
    )
    transcription = container.transcriptions.create(
        meeting_id=meeting.id, title="Live", engine="test", model="test",
        language="en", settings={},
    )
    session = SimpleNamespace(
        session_id="stream-test", meeting_id=meeting.id,
        transcription_id=transcription.id, title=meeting.title,
    )
    client.portal.call(container.live_assistant_service.ensure_session, session)
    async def summarize(transcript, config, progress, is_cancelled):
        raw = json.dumps({"respond": True, "kind": "information", "text": "**Ready** 👋"})
        for char in raw:
            progress.on_token(char)
        return SummaryResult(content_markdown=raw)
    monkeypatch.setattr(container.live_assistant_service.engine, "summarize", summarize)
    response = client.post(
        f"/api/live-assistant/meetings/{meeting.id}/questions/stream",
        json={"question": "Are we ready?"},
    )
    events = [json.loads(line) for line in response.text.splitlines()]
    assert "".join(item["text"] for item in events if item["type"] == "delta") == "**Ready** 👋"
    assert events[-1]["result"]["answer"]["text"] == "**Ready** 👋"
    saved = container.live_assistant_repository.insights(meeting.id, 10)
    assert len(saved) == 2
    assert {item.kind for item in saved} == {"user_question", "answer"}
