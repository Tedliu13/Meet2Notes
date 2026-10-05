from __future__ import annotations

import keyring
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from local_meeting_ai.api.app import create_app
from local_meeting_ai.config import AppSettings


@pytest.fixture
def hosted_client(settings: AppSettings):
    previous = keyring.get_keyring()
    configured = settings.model_copy(
        update={
            "hosted": True,
            "allowed_hosts": "meet2notes.ncdrcc.com",
            "auth_username": "owner",
            "auth_password": "test-password",
            "secrets_key": Fernet.generate_key().decode(),
            "max_heavy_jobs": 4,
            "max_upload_mb": 0,
        }
    )
    try:
        with TestClient(create_app(configured), base_url="https://meet2notes.ncdrcc.com") as client:
            yield client
    finally:
        keyring.set_keyring(previous)


def test_authentication_and_host_validation(hosted_client):
    assert hosted_client.get("/api/health").status_code == 200
    for path in ("/", "/api/meetings", "/api/settings", "/api/docs", "/static/js/app.js"):
        assert hosted_client.get(path).status_code == 401
    assert hosted_client.get("/", auth=("owner", "wrong")).status_code == 401
    assert hosted_client.get("/", headers={"Authorization": "Basic !!!"}).status_code == 401
    assert hosted_client.get("/", auth=("owner", "test-password")).status_code == 200
    assert hosted_client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400


def test_hosted_import_workspace_and_disabled_capture(hosted_client):
    hosted_client.auth = ("owner", "test-password")
    assert 'data-hosted="true"' in hosted_client.get("/").text
    assert hosted_client.get("/settings").status_code == 200
    assert hosted_client.get("/api/capture/session").status_code == 200
    sources = hosted_client.get("/api/audio/sources").json()
    assert sources["sources"] == []
    assert sources["capability"]["available"] is False
    for path in (
        "/api/capture/sessions",
        "/api/application/shutdown",
        "/api/settings/data-directory/schedule",
        "/api/live-assistant/settings",
    ):
        assert hosted_client.post(path, json={}).status_code == 409
    assert hosted_client.app.state.container.queue.worker_count == 4
    assert hosted_client.app.state.container.storage.max_upload_bytes == 0
    meeting = hosted_client.post("/api/meetings", json={"title": "Hosted meeting"})
    assert meeting.status_code == 201
    assert hosted_client.get("/api/meetings").json()[0]["title"] == "Hosted meeting"
    upload = hosted_client.post(
        f"/api/meetings/{meeting.json()['id']}/import",
        files={"file": ("sample.wav", b"RIFF" + b"x" * (3 * 1024 * 1024), "audio/wav")},
    )
    assert upload.status_code == 202


def test_hosted_gguf_picker_lists_models(hosted_client):
    hosted_client.auth = ("owner", "test-password")
    root = hosted_client.app.state.container.paths.models
    model = root / "custom.gguf"
    model.write_bytes(b"test-placeholder")
    assert str(model.resolve()) in hosted_client.get("/api/models/gguf-files").json()["files"]
    config = hosted_client.get("/api/mcp/configuration").json()
    assert '"command": "python"' in config["claude_desktop"]["content"]
    assert "https://meet2notes.ncdrcc.com" in config["claude_desktop"]["content"]
    assert "test-password" not in config["claude_desktop"]["content"]


def test_hosted_api_key_uses_encrypted_store(hosted_client):
    hosted_client.auth = ("owner", "test-password")
    response = hosted_client.put(
        "/api/settings/summary-api-key", json={"api_key": "private-test-provider-key"}
    )
    assert response.status_code == 200
    assert response.json() == {"available": True, "configured": True}
    path = hosted_client.app.state.container.paths.root / "secrets" / "credentials.enc"
    assert b"private-test-provider-key" not in path.read_bytes()


def test_reject_cross_origin_writes(hosted_client):
    hosted_client.auth = ("owner", "test-password")
    for headers in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
        assert (
            hosted_client.post("/api/meetings", json={"title": "bad"}, headers=headers).status_code
            == 403
        )
    assert (
        hosted_client.post(
            "/api/meetings",
            json={"title": "good"},
            headers={"Origin": "https://meet2notes.ncdrcc.com"},
        ).status_code
        == 201
    )


def test_hosted_requires_secrets(settings):
    with pytest.raises(ValueError, match="requires"):
        create_app(settings.model_copy(update={"hosted": True}))
