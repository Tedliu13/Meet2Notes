from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from local_meeting_ai.infrastructure import ollama


def test_ollama_discovery_does_not_change_saved_preferences(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    before = client.get("/api/settings").json()["summary_engine"]

    async def discover(base_url: str | None) -> dict[str, Any]:
        assert base_url == "http://127.0.0.1:11434"
        return {"state": "ready", "models": [{"name": "qwen3:8b"}]}

    monkeypatch.setattr(ollama, "discover_ollama", discover)
    response = client.get("/api/runtimes/ollama", params={"base_url": "http://127.0.0.1:11434"})
    assert response.status_code == 200
    assert response.json()["models"][0]["name"] == "qwen3:8b"
    assert client.get("/api/settings").json()["summary_engine"] == before


def test_ollama_selection_round_trip(client: TestClient) -> None:
    config = {
        "provider": "litellm",
        "profile_id": "ollama",
        "model": "ollama_chat/qwen3:8b",
        "base_url": "http://127.0.0.1:11434",
        "context_length": 8192,
    }
    response = client.put("/api/settings", json={"summary_engine": config})
    assert response.status_code == 200
    saved = client.get("/api/settings").json()["summary_engine"]
    assert saved["profile_id"] == "ollama"
    assert saved["model"] == config["model"]
    assert saved["preload_on_start"] is False
    assert saved["context_length"] == 8192
    config["model"] = "openai/example"
    assert client.put("/api/settings", json={"summary_engine": config}).status_code == 422
