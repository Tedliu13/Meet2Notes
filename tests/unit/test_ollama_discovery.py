from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from local_meeting_ai.adapters.summary import llama_cpp
from local_meeting_ai.domain.errors import ValidationError
from local_meeting_ai.infrastructure import ollama


def fake_api(monkeypatch: Any, handler: Any) -> None:
    factory = httpx.AsyncClient
    monkeypatch.setattr(
        ollama.httpx,
        "AsyncClient",
        lambda **kw: factory(
            **kw,
            transport=httpx.MockTransport(handler),
        ),
    )
    monkeypatch.setattr(ollama, "executable_path", lambda: "/test/ollama")


def test_discovery_only_offers_text_models_and_never_generates(monkeypatch: Any) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        assert "authorization" not in request.headers
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.34.4"})
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "chat:8b", "size": 123, "details": {"parameter_size": "8B"}},
                        {"name": "embedding"},
                        {"name": "chat:cloud"},
                        {"name": "unknown"},
                    ]
                },
            )
        assert request.url.path == "/api/show"
        import json

        name = json.loads(request.content)["model"]
        if name == "unknown":
            return httpx.Response(200, json={})
        return httpx.Response(
            200,
            json={
                "capabilities": ["embedding"] if name == "embedding" else ["completion"],
                "model_info": {"qwen.context_length": 32768},
            },
        )

    fake_api(monkeypatch, handler)
    result = asyncio.run(ollama.discover_ollama("http://127.0.0.1:11434"))
    assert result["state"] == "ready"
    assert [m["name"] for m in result["models"]] == ["chat:8b", "chat:cloud"]
    assert result["models"][1]["cloud"] is True
    assert result["models"][0]["context_length"] == 32768
    assert result["excluded_models"] == result["unverified_models"] == 1
    assert set(requests) == {"/api/version", "/api/tags", "/api/show"}


@pytest.mark.parametrize(
    "base,state",
    [
        ("http://127.0.0.1:11434", "stopped"),
        ("http://example.test:11434", "unreachable"),
    ],
)
def test_installed_does_not_mean_running(base: str, state: str, monkeypatch: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    fake_api(monkeypatch, handler)
    result = asyncio.run(ollama.discover_ollama(base))
    assert result["state"] == state
    assert not result["models"]


def test_unrecognized_server_is_not_reported_as_ollama(monkeypatch: Any) -> None:
    fake_api(monkeypatch, lambda request: httpx.Response(200, json={"status": "ok"}))
    assert asyncio.run(ollama.discover_ollama())["state"] == "connection_error"


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "http://u:p@localhost", "http://x?q=1", "http://x:invalid"]
)
def test_invalid_discovery_url_is_rejected(url: str) -> None:
    with pytest.raises(ValidationError):
        ollama.normalize_url(url)


def test_environment_host_is_normalized(monkeypatch: Any) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0:11434")
    assert ollama.normalize_url(None) == "http://127.0.0.1:11434"
    assert ollama.normalize_url("http://[::]:11434/") == "http://[::1]:11434"
    assert ollama.normalize_url("https://example.test/ollama/") == "https://example.test/ollama"


def test_ollama_context_and_credentials_are_isolated(tmp_path: Path, monkeypatch: Any) -> None:
    key_reads = []
    monkeypatch.setattr(
        llama_cpp, "get_litellm_api_key", lambda: key_reads.append(True) or "other-provider-secret"
    )
    calls = []
    monkeypatch.setattr(
        llama_cpp,
        "litellm_completion",
        lambda client, args: calls.append(args) or {"choices": [{"message": {"content": "Notes"}}]},
    )
    engine = llama_cpp.LlamaCppSummaryEngine(tmp_path)
    try:
        engine._litellm_completion(
            [],
            {
                "profile_id": "ollama",
                "provider": "litellm",
                "model": "ollama_chat/qwen3:8b",
                "base_url": "http://127.0.0.1:11434",
                "context_length": 8192,
                "keep_model_loaded": False,
            },
        )
        assert calls[0]["num_ctx"] == 8192
        assert calls[0]["keep_alive"] == 0
        assert calls[0]["think"] is False
        assert "api_key" not in calls[0]
        assert key_reads == []
        engine._litellm_completion([], {"model": "openai/example"})
        assert "num_ctx" not in calls[1]
        assert calls[1]["api_key"] == "other-provider-secret"
    finally:
        engine.shutdown()
