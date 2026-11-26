from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import keyring
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient

from local_meeting_ai.api.app import create_app
from local_meeting_ai.application.transcription_service import _postprocess_options

PASSWORD = "note-format-test-password"
FORMAT = {
    "name": "My detailed format",
    "description": "Personal instructions",
    "system_prompt": "Private system instruction",
    "user_prompt_template": "Explain decisions",
    "sections": [{"title": "Decisions", "instruction": "Include evidence", "format": "list"}],
}


@pytest.fixture
def hosted(settings):
    previous = keyring.get_keyring()
    config = settings.model_copy(
        update={
            "hosted": True,
            "allowed_hosts": "meet2notes.ncdrcc.com",
            "auth_username": "owner",
            "auth_password": PASSWORD,
            "secrets_key": Fernet.generate_key().decode(),
        }
    )
    try:
        with TestClient(create_app(config), base_url="https://meet2notes.ncdrcc.com") as client:
            yield client
    finally:
        keyring.set_keyring(previous)


def login(client, username="owner"):
    client.cookies.clear()
    result = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert result.status_code == 200, result.text
    return result.json()


def register(client, name):
    client.cookies.clear()
    result = client.post(
        "/api/auth/register",
        json={
            "username": name,
            "name": name,
            "password": PASSWORD,
        },
    )
    assert result.status_code == 201, result.text
    pending = result.json()
    assert not pending["active"]
    login(client)
    assert client.patch(f"/api/users/{pending['id']}", json={"active": True}).status_code == 200
    assert client.post("/api/auth/logout").status_code == 204
    login(client, name)


def test_personal_formats_are_isolated_and_do_not_unlock_settings_or_other_meetings(hosted):
    client = hosted
    login(client)
    shared = client.post("/api/summary-templates", json={**FORMAT, "scope": "shared"}).json()
    meeting = client.post("/api/meetings", json={"title": "Admin meeting"}).json()
    register(client, "alice")
    mine = client.post("/api/summary-templates", json=FORMAT)
    assert mine.status_code == 201
    mine = mine.json()
    assert mine["scope"] == "personal" and mine["owner_id"] is not None
    assert client.get("/note-formats").status_code == 200
    assert client.get("/settings").status_code == 403
    assert (
        client.post("/api/summary-templates", json={**FORMAT, "scope": "shared"}).status_code == 403
    )
    assert client.post("/api/summary-templates", json={**FORMAT, "owner_id": 1}).status_code == 422
    assert client.put(f"/api/summary-templates/{shared['id']}", json=FORMAT).status_code == 403
    assert client.post(f"/api/summary-templates/{mine['id']}/default").status_code == 403
    assert (
        client.post(
            f"/api/meetings/{meeting['id']}/transcriptions",
            json={
                "postprocess": True,
                "postprocess_options": {"summary_template_id": mine["id"]},
            },
        ).status_code
        == 403
    )
    register(client, "bob")
    visible = client.get("/api/summary-templates").json()
    assert shared["id"] in {item["id"] for item in visible}
    assert mine["id"] not in {item["id"] for item in visible}
    assert client.put(f"/api/summary-templates/{mine['id']}", json=FORMAT).status_code == 404
    assert client.delete(f"/api/summary-templates/{mine['id']}").status_code == 404
    assert client.post(f"/api/summary-templates/{mine['id']}/personal-default").status_code == 404
    own_meeting = client.post("/api/meetings", json={"title": "Bob meeting"}).json()
    assert (
        client.post(
            f"/api/meetings/{own_meeting['id']}/transcriptions",
            json={
                "postprocess": True,
                "postprocess_options": {"summary_template_id": mine["id"]},
            },
        ).status_code
        == 404
    )


def test_personal_default_falls_back_without_changing_global_or_other_users(hosted):
    client = hosted
    login(client)
    original = next(x for x in client.get("/api/summary-templates").json() if x["is_default"])
    register(client, "alice")
    mine = client.post("/api/summary-templates", json=FORMAT).json()
    assert client.post(f"/api/summary-templates/{mine['id']}/personal-default").status_code == 200
    prefs = client.get("/api/workspace/preferences").json()
    assert prefs["default_summary_template_id"] == mine["id"]
    register(client, "bob")
    assert (
        client.get("/api/workspace/preferences").json()["default_summary_template_id"]
        == original["id"]
    )
    login(client, "alice")
    assert client.delete(f"/api/summary-templates/{mine['id']}").status_code == 204
    assert (
        client.get("/api/workspace/preferences").json()["default_summary_template_id"]
        == original["id"]
    )
    login(client)
    assert client.get("/api/settings").json()["default_summary_template_id"] in (
        None,
        original["id"],
    )


def test_administrator_manages_formats_but_cannot_make_private_format_global(hosted):
    client = hosted
    register(client, "alice")
    mine = client.post("/api/summary-templates", json=FORMAT).json()
    login(client)
    assert mine["id"] in {x["id"] for x in client.get("/api/summary-templates").json()}
    assert mine["id"] not in {
        x["id"] for x in client.get("/api/summary-templates?scope=shared").json()
    }
    assert (
        client.put(
            f"/api/summary-templates/{mine['id']}", json={**FORMAT, "name": "Revised"}
        ).status_code
        == 200
    )
    assert client.post(f"/api/summary-templates/{mine['id']}/default").status_code == 403
    assert (
        client.put("/api/settings", json={"default_summary_template_id": mine["id"]}).status_code
        == 422
    )
    assert client.delete(f"/api/summary-templates/{mine['id']}").status_code == 204


def test_format_snapshot_survives_edit_delete_and_is_not_exposed_in_job_views(hosted, monkeypatch):
    client = hosted
    register(client, "alice")
    mine = client.post("/api/summary-templates", json=FORMAT).json()
    meeting = client.post("/api/meetings", json={"title": "Test snapshots"}).json()
    container = client.app.state.container
    container.preferences.update({"summary_engine": {"provider": "litellm", "model": "test"}})
    monkeypatch.setattr(container.queue, "submit", AsyncMock())
    with container.database.transaction() as conn:
        transcription_id = conn.execute(
            "INSERT INTO transcriptions(meeting_id,engine,model,status,is_active,created_at) "
            "VALUES(?,'test','test','completed',1,'2026-01-01')",
            (meeting["id"],),
        ).lastrowid
    response = client.post(
        f"/api/transcriptions/{transcription_id}/summaries", json={"template_id": mine["id"]}
    )
    assert response.status_code == 202, response.text
    job = container.jobs.get(response.json()["job"]["uuid"])
    assert job.payload["summary_template"]["system_prompt"] == FORMAT["system_prompt"]
    assert "summary_template" not in response.json()["job"]["payload"]
    # Simulate the options captured at the start of a transcription pipeline.
    snapshot = {"id": mine["id"], **job.payload["summary_template"]}
    options = _postprocess_options({"summary_template_snapshot": snapshot})
    assert options["summary_template_snapshot"] == snapshot
    assert (
        client.put(
            f"/api/summary-templates/{mine['id']}", json={**FORMAT, "system_prompt": "Changed"}
        ).status_code
        == 200
    )
    assert client.delete(f"/api/summary-templates/{mine['id']}").status_code == 204
    summary, queued = asyncio.run(
        container.summary_service.start(
            transcription_id,
            template_snapshot=options["summary_template_snapshot"],
        )
    )
    assert summary.template_id is None
    assert queued.payload["summary_template"]["system_prompt"] == FORMAT["system_prompt"]
    register(client, "bob")
    assert FORMAT["system_prompt"] not in client.get("/api/jobs").text
    assert FORMAT["system_prompt"] not in client.get(f"/api/jobs/{job.uuid}").text


def test_desktop_keeps_shared_formats_and_global_default(client):
    result = client.post("/api/summary-templates", json=FORMAT)
    assert result.status_code == 201
    item = result.json()
    assert item["scope"] == "shared" and item["owner_id"] is None
    assert client.post(f"/api/summary-templates/{item['id']}/default").status_code == 200
    assert client.get("/note-formats").status_code == 200


def test_transcription_api_freezes_personal_default_and_rejects_forged_snapshots(
    hosted, monkeypatch
):
    client = hosted
    register(client, "alice")
    mine = client.post("/api/summary-templates", json=FORMAT).json()
    client.post(f"/api/summary-templates/{mine['id']}/personal-default")
    meeting = client.post("/api/meetings", json={"title": "Pipeline default"}).json()
    captured = {}

    async def capture(meeting_id, **values):
        captured.update(values)
        raise HTTPException(409, "Captured without starting inference")

    monkeypatch.setattr(client.app.state.container.transcription_service, "start", capture)
    response = client.post(
        f"/api/meetings/{meeting['id']}/transcriptions",
        json={
            "postprocess": True,
        },
    )
    assert response.status_code == 409
    options = captured["postprocess_options"]
    assert options["summary_template_id"] == mine["id"]
    assert options["summary_template_snapshot"]["system_prompt"] == FORMAT["system_prompt"]
    forged = client.post(
        f"/api/meetings/{meeting['id']}/transcriptions",
        json={
            "postprocess": True,
            "postprocess_options": {"summary_template_snapshot": {"system_prompt": "Forged"}},
        },
    )
    assert forged.status_code == 422
