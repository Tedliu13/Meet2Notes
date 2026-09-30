from __future__ import annotations

import json
from unittest.mock import Mock

import httpx
import pytest

from local_meeting_ai.domain.errors import CapabilityUnavailableError
from local_meeting_ai.infrastructure import bonsai_runtime as runtime


@pytest.fixture
def fake_runtime(tmp_path, monkeypatch):
    executable = tmp_path / "runtime" / "llama-server.exe"
    executable.parent.mkdir()
    executable.touch()
    (executable.parent / ".verified").touch()
    monkeypatch.setattr(runtime, "runtime_executable", lambda directory: executable)
    process = Mock()
    process.poll.return_value = None
    launch = Mock(return_value=process)
    monkeypatch.setattr(runtime.subprocess, "Popen", launch)
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1, 2, 3]})
        return httpx.Response(200, content=(
            'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":" world"}}]}\n\n'
            'data: [DONE]\n\n'
        ))

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://127.0.0.1")
    factory = Mock(return_value=client)
    monkeypatch.setattr(runtime.httpx, "Client", factory)
    return tmp_path, process, launch, requests, factory, client


def test_native_runtime_streams_and_releases_only_its_process(fake_runtime):
    root, process, launch, requests, factory, client = fake_runtime
    model = runtime.BonsaiModel(root, root / "test.gguf", {"main_gpu": 2, "context_length": 8192})
    args = launch.call_args.args[0]
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert args[args.index("-np") + 1] == "1"
    assert args[args.index("-c") + 1] == "8192"
    assert args[args.index("-ctk") + 1] == "q4_0"
    assert args[args.index("-ctv") + 1] == "q4_0"
    assert args[args.index("--checkpoint-min-step") + 1] == "256"
    assert "--no-context-shift" in args
    assert launch.call_args.kwargs["env"]["CUDA_VISIBLE_DEVICES"] == "2"
    token = launch.call_args.kwargs["env"]["LLAMA_API_KEY"]
    assert factory.call_args.kwargs["headers"]["Authorization"] == f"Bearer {token}"
    assert token not in args
    assert model.tokenize(b"hello", add_bos=True) == [1, 2, 3]
    chunks = list(model.create_chat_completion(
        messages=[{"role": "user", "content": "test"}], stream=True,
    ))
    assert "".join(chunk["choices"][0]["delta"]["content"] for chunk in chunks) == "Hello world"
    assert json.loads(requests[-1].content)["stream"] is True
    payload = json.loads(requests[-1].content)
    assert payload["chat_template_kwargs"]["enable_thinking"] is False
    assert payload["reasoning_effort"] == "none"
    assert payload["cache_prompt"] is True
    assert payload["stream_options"] == {"include_usage": True}
    model.close()
    model.close()
    process.terminate.assert_called_once()
    assert client.is_closed


def test_start_failure_cleans_resources_and_explains_recovery(fake_runtime):
    root, process, _launch, _requests, _factory, client = fake_runtime
    process.poll.return_value = 1
    with pytest.raises(CapabilityUnavailableError, match="driver and free GPU memory"):
        runtime.BonsaiModel(root, root / "test.gguf", {})
    assert client.is_closed
    process.terminate.assert_not_called()


def test_stream_can_be_closed_early(fake_runtime):
    root, _process, _launch, _requests, _factory, _client = fake_runtime
    model = runtime.BonsaiModel(root, root / "test.gguf", {})
    try:
        stream = model.create_chat_completion(messages=[], stream=True)
        assert next(stream)["choices"][0]["delta"]["content"] == "Hello"
        stream.close()
        # The resident model remains usable after a request is cancelled.
        assert model.tokenize(b"next") == [1, 2, 3]
    finally:
        model.close()


def test_incomplete_stream_cannot_be_saved_as_a_complete_answer(fake_runtime):
    root, _process, _launch, _requests, _factory, client = fake_runtime
    model = runtime.BonsaiModel(root, root / "test.gguf", {})
    client.close()
    model._client = type(client)(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, content='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
        )),
    )
    try:
        with pytest.raises(CapabilityUnavailableError, match="before completing"):
            list(model.create_chat_completion(messages=[], stream=True))
    finally:
        model.close()


def test_every_turn_disables_thinking_without_dropping_history(fake_runtime):
    root, _process, _launch, requests, _factory, _client = fake_runtime
    model = runtime.BonsaiModel(root, root / "test.gguf", {})
    history = [
        {"role": "user", "content": "quien ha hablado?"},
        {"role": "assistant", "content": "Harold, Evelyn, Victor y Sandra."},
        {"role": "user", "content": "que se ha dicho del pdf?"},
    ]
    options = {"enable_thinking": True, "custom_option": "keep"}
    try:
        list(model.create_chat_completion(messages=history[:1], stream=True))
        list(model.create_chat_completion(
            messages=history, stream=True, max_tokens=1024,
            chat_template_kwargs=options,
        ))
        calls = [json.loads(request.content) for request in requests
                 if request.url.path == "/v1/chat/completions"]
        assert len(calls) == 2
        assert all(call["chat_template_kwargs"]["enable_thinking"] is False for call in calls)
        assert all(call["reasoning_effort"] == "none" for call in calls)
        assert calls[1]["messages"] == history
        assert calls[1]["max_tokens"] == 1024
        assert calls[1]["chat_template_kwargs"]["custom_option"] == "keep"
        assert options["enable_thinking"] is True
    finally:
        model.close()


@pytest.mark.parametrize("reasoning", [True, False])
def test_empty_native_response_has_actionable_error_without_exposing_reasoning(
    fake_runtime, caplog, reasoning,
):
    root, _process, _launch, _requests, _factory, client = fake_runtime
    model = runtime.BonsaiModel(root, root / "test.gguf", {})
    client.close()
    delta = {"reasoning_content": "private internal text"} if reasoning else {"content": "  "}
    chunks = [
        {"choices": [{"delta": delta}]},
        {"choices": [{"delta": {}, "finish_reason": "length" if reasoning else "stop"}]},
    ]
    response = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    model._client = type(client)(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=response)),
    )
    try:
        expected = "output limit for internal reasoning" if reasoning else "without returning"
        with pytest.raises(CapabilityUnavailableError, match=expected):
            list(model.create_chat_completion(messages=[], stream=True))
        assert "private internal text" not in caplog.text
        assert "finish_reason=" in caplog.text
    finally:
        model.close()
