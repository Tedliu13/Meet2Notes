from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from local_meeting_ai.adapters.summary import llama_cpp
from local_meeting_ai.api.answer_stream import answer_stream
from local_meeting_ai.application.answer_stream import AnswerProgress
from local_meeting_ai.domain.errors import CapabilityUnavailableError, JobCancelledError


@pytest.fixture
def engine(tmp_path, monkeypatch):
    original_import = llama_cpp.importlib.import_module
    monkeypatch.setattr(
        llama_cpp.importlib, "import_module",
        lambda name: SimpleNamespace() if name == "litellm" else original_import(name),
    )
    instance = llama_cpp.LlamaCppSummaryEngine(tmp_path)
    yield instance
    instance.shutdown()


def delta(text):
    return {"choices": [{"delta": {"content": text}}]}


def test_live_json_fragments_only_expose_answer_and_handle_split_unicode():
    events = []
    progress = AnswerProgress(events.append, structured=True)
    answer = '**Acción**\nSay "hello" 👋'
    raw = json.dumps({"respond": True, "text": answer, "memory_summary": "PRIVATE"})
    for char in raw:
        progress.on_token(char)
    assert "".join(item["text"] for item in events) == answer
    assert "PRIVATE" not in str(events)


def test_remote_fragments_usage_and_cleanup(engine, monkeypatch):
    closed = []
    def chunks():
        try:
            yield delta("**Hello")
            yield delta(" world**")
            yield {"choices": [], "usage": {"completion_tokens": 4}}
        finally:
            closed.append(True)
    requests = []
    def complete(_module, args):
        requests.append(args)
        return chunks()
    monkeypatch.setattr(llama_cpp, "litellm_completion", complete)
    fragments = []
    result = engine._litellm_completion(
        [], {"profile_id": "ollama", "model": "ollama_chat/test"},
        on_token=fragments.append,
    )
    assert requests[0]["stream"] is True
    assert fragments == ["**Hello", " world**"]
    assert result["usage"]["completion_tokens"] == 4
    assert closed == [True]


def test_explicit_unsupported_stream_retries_without_stream(engine, monkeypatch):
    requests = []
    def complete(_module, args):
        requests.append(dict(args))
        if args.get("stream"):
            raise ValueError("Streaming is not supported for this model")
        return {"choices": [{"message": {"content": "Buffered answer"}}]}
    monkeypatch.setattr(llama_cpp, "litellm_completion", complete)
    result = engine._litellm_completion([], {"api_key": ""}, on_token=lambda text: None)
    assert result["choices"][0]["message"]["content"] == "Buffered answer"
    assert len(requests) == 2 and "stream" not in requests[1]


def test_broken_stream_never_retries_and_closes(engine, monkeypatch):
    requests = []
    closed = []
    def chunks():
        try:
            yield delta("Partial")
            raise RuntimeError("Connection lost")
        finally:
            closed.append(True)
    def complete(_module, args):
        requests.append(args)
        return chunks()
    monkeypatch.setattr(llama_cpp, "litellm_completion", complete)
    fragments = []
    with pytest.raises(CapabilityUnavailableError, match="Connection lost"):
        engine._litellm_completion([], {"api_key": ""}, on_token=fragments.append)
    assert fragments == ["Partial"] and len(requests) == 1 and closed == [True]


@pytest.mark.parametrize("streaming,emit_tokens", [(True, True), (False, True), (True, False)])
def test_local_stream_toggle_and_intermediate_evidence_are_respected(
    engine, streaming, emit_tokens,
):
    engine._fits_context = lambda *args: True
    model = SimpleNamespace(create_chat_completion=lambda **kwargs: iter([delta("Answer")]))
    events = []
    result = engine._complete_once(
        model, [], {"streaming": streaming}, 128, AnswerProgress(events.append),
        lambda: False, 0, 1, "Generating", emit_tokens=emit_tokens,
    )
    assert result["choices"][0]["message"]["content"] == "Answer"
    assert any(item["type"] == "delta" for item in events) == (streaming and emit_tokens)


def test_cancellation_closes_remote_stream(engine, monkeypatch):
    closed = []
    def chunks():
        try:
            yield delta("Do not show")
        finally:
            closed.append(True)
    monkeypatch.setattr(llama_cpp, "litellm_completion", lambda *args: chunks())
    with pytest.raises(JobCancelledError):
        engine._litellm_completion(
            [], {"api_key": ""}, on_token=lambda text: pytest.fail(text),
            is_cancelled=lambda: True,
        )
    assert closed == [True]


@pytest.mark.asyncio
async def test_disconnecting_the_response_cancels_producer():
    stopped = asyncio.Event()
    cancellation_checks = []
    async def run(progress, cancelled):
        cancellation_checks.append(cancelled)
        progress.on_token("Partial")
        try:
            await asyncio.Future()
        finally:
            stopped.set()
    response = answer_stream(run)
    iterator = response.body_iterator
    await anext(iterator)  # Immediate activity, before inference starts.
    fragment = await anext(iterator)
    assert '"delta"' in fragment
    await iterator.aclose()
    assert stopped.is_set() and cancellation_checks[0]()


def test_context_progress_is_visible_before_answer_tokens(engine):
    from types import SimpleNamespace

    events = []
    engine._fits_context = lambda *args: True
    model = SimpleNamespace(create_chat_completion=lambda **kwargs: iter([
        {"choices": [], "prompt_progress": {"total": 24000, "processed": 23000,
                                             "cache": 23000}},
        delta("Answer"),
    ]))
    engine._complete_once(
        model, [], {"streaming": True}, 128, AnswerProgress(events.append),
        lambda: False, 0, 1, "Generating", emit_tokens=True,
    )
    context = next(item for item in events if item["type"] == "context")
    assert context == {"type": "context", "total": 24000, "processed": 23000, "cache": 23000}
    first_token = next(i for i, item in enumerate(events) if item["type"] == "delta")
    assert events.index(context) < first_token
