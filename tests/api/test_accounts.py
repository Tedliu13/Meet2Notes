from __future__ import annotations

import hashlib
import time
from unittest.mock import AsyncMock, patch

import keyring
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from local_meeting_ai.api.app import create_app

PASSWORD = "a-test-password-123"


@pytest.fixture
def account_client(settings):
    previous = keyring.get_keyring()
    configured = settings.model_copy(
        update={
            "hosted": True,
            "allowed_hosts": "meet2notes.ncdrcc.com",
            "auth_username": "owner",
            "auth_password": PASSWORD,
            "secrets_key": Fernet.generate_key().decode(),
        }
    )
    try:
        with TestClient(create_app(configured), base_url="https://meet2notes.ncdrcc.com") as client:
            yield client
    finally:
        keyring.set_keyring(previous)


def register(client, username="alice", name="Alice"):
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "name": name,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def login(client, username="owner", password=PASSWORD):
    client.cookies.clear()
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def seed_transcript(client, meeting_id):
    with client.app.state.container.database.transaction() as conn:
        transcription = conn.execute(
            "INSERT INTO transcriptions(meeting_id,engine,model,status,is_active,created_at) "
            "VALUES(?,'test','test','completed',1,'2026-01-01')",
            (meeting_id,),
        ).lastrowid
        speaker = conn.execute(
            "INSERT INTO speakers(meeting_id,display_name,created_at) "
            "VALUES(?,'Speaker','2026-01-01')",
            (meeting_id,),
        ).lastrowid
        segment = conn.execute(
            "INSERT INTO transcript_segments"
            "(transcription_id,segment_index,start_ms,end_ms,text,speaker_id) "
            "VALUES(?,0,0,1000,'original',?)",
            (transcription, speaker),
        ).lastrowid
        summary = conn.execute(
            "INSERT INTO summaries"
            "(meeting_id,transcription_id,provider,model,status,created_at,content_markdown) "
            "VALUES(?,?,'test','test','completed','2026-01-01','old notes')",
            (meeting_id, transcription),
        ).lastrowid
        conn.execute(
            "INSERT INTO jobs(uuid,meeting_id,job_type,status,created_at) "
            "VALUES('held-test-job',?,'diarize','paused','2026-01-01')",
            (meeting_id,),
        )
    return transcription, speaker, segment, summary


def test_registration_session_logout_and_password_storage(account_client):
    client = account_client
    assert client.get("/api/auth/me").status_code == 401
    assert (
        client.get(
            "/api/auth/me", auth=("owner", PASSWORD), headers={"Sec-Fetch-Mode": "cors"}
        ).status_code
        == 401
    )
    user = register(client, name="<script>Alice</script>")
    assert user["active"] and not user["is_admin"]
    assert "password" not in str(user)
    assert client.get("/api/auth/me").json()["id"] == user["id"]
    token = client.cookies.get("__Host-m2n_session")
    with client.app.state.container.database.read() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        assert row["password_hash"].startswith("scrypt$")
        assert PASSWORD not in row["password_hash"]
        session = conn.execute("SELECT token_hash FROM user_sessions").fetchone()[0]
        assert session == hashlib.sha256(token.encode()).hexdigest()
    html = client.get("/").text
    assert "&lt;script&gt;Alice&lt;/script&gt;" in html
    assert 'href="/settings"' not in html
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.set("__Host-m2n_session", token)
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.clear()
    assert login(client, "ALICE")["id"] == user["id"]
    client.cookies.clear()
    response = client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    cookie = response.headers["set-cookie"]
    assert all(value in cookie for value in ("HttpOnly", "Secure", "SameSite=strict", "Path=/"))


def test_meeting_owner_managers_and_resource_alias_permissions(account_client):
    client = account_client
    alice = register(client)
    meeting = client.post("/api/meetings", json={"title": "Shared meeting"}).json()
    mid = meeting["id"]
    assert meeting["creator"]["id"] == alice["id"]
    tid, speaker, segment, summary = seed_transcript(client, mid)
    bob = register(client, "bob", "Bob")
    assert client.get(f"/api/meetings/{mid}").status_code == 200
    assert client.get(f"/api/transcriptions/{tid}").status_code == 200
    with patch.object(
        client.app.state.container.prompt_service,
        "ask",
        new=AsyncMock(return_value={"answer": "Allowed"}),
    ):
        assert (
            client.post(
                "/api/prompt", json={"question": "Read this meeting", "meeting_id": mid}
            ).status_code
            == 200
        )
    assert client.get(f"/api/meetings/{mid}").json()["can_manage"] is False
    assert client.get("/api/library").json()["items"][0]["creator"]["name"] == "Alice"
    writes = [
        ("PATCH", f"/api/meetings/{mid}", {"title": "stolen"}),
        ("DELETE", f"/api/meetings/{mid}", None),
        ("DELETE", f"/api/meetings/{mid}/audio", None),
        ("POST", f"/api/meetings/{mid}/import", None),
        ("POST", f"/api/meetings/{mid}/transcriptions", {}),
        ("PATCH", f"/api/transcriptions/{tid}", {"title": "stolen"}),
        ("PATCH", f"/api/transcript-segments/{segment}", {"text": "stolen"}),
        ("PATCH", f"/api/speakers/{speaker}", {"display_name": "stolen"}),
        ("PATCH", f"/api/summaries/{summary}", {"content_markdown": "stolen"}),
        ("POST", f"/api/transcriptions/{tid}/diarize", {}),
        ("POST", f"/api/transcriptions/{tid}/summaries", {}),
        ("POST", f"/api/transcriptions/{tid}/speakers/{speaker}/summary", {}),
        ("POST", f"/api/transcriptions/{tid}/speakers/{speaker}/remember", {}),
        ("POST", f"/api/transcriptions/{tid}/speakers/{speaker}/profile", {"profile_id": 1}),
        ("POST", f"/api/transcriptions/{tid}/activate", {}),
        ("POST", f"/api/transcriptions/{tid}/find-replace", {}),
        ("POST", "/api/jobs/held-test-job/cancel", {}),
        ("PUT", f"/api/meetings/{mid}/managers", {"user_ids": [bob["id"]]}),
    ]
    for method, path, body in writes:
        assert client.request(method, path, json=body).status_code == 403, path
    login(client, "alice")
    response = client.put(f"/api/meetings/{mid}/managers", json={"user_ids": [bob["id"]]})
    assert response.status_code == 200
    assert response.json()["managers"][0]["name"] == "Bob"
    login(client, "bob")
    assert client.get(f"/api/meetings/{mid}").json()["can_manage"] is True
    container = client.app.state.container
    job = container.jobs.get("held-test-job")
    with patch.object(container.diarization_service, "start", new=AsyncMock(return_value=job)):
        assert (
            client.post(f"/api/transcriptions/{tid}/diarize", json={"speaker_count": 3}).status_code
            == 202
        )
    with patch.object(
        container.summary_service,
        "start",
        new=AsyncMock(return_value=(container.summaries.get(summary), job)),
    ):
        assert client.post(f"/api/transcriptions/{tid}/summaries", json={}).status_code == 202
    assert (
        client.patch(
            f"/api/transcript-segments/{segment}", json={"text": "edited by Bob"}
        ).status_code
        == 200
    )
    assert client.patch(f"/api/speakers/{speaker}", json={"display_name": "Bob"}).status_code == 200
    assert client.put(f"/api/meetings/{mid}/managers", json={"user_ids": []}).status_code == 403
    login(client, "alice")
    assert client.put(f"/api/meetings/{mid}/managers", json={"user_ids": []}).status_code == 200
    login(client, "bob")
    assert (
        client.patch(f"/api/transcript-segments/{segment}", json={"text": "denied"}).status_code
        == 403
    )
    login(client)
    assert (
        client.patch(f"/api/transcript-segments/{segment}", json={"text": "admin edit"}).status_code
        == 200
    )


def test_admin_account_management_revokes_sessions_and_preserves_meetings(account_client):
    client = account_client
    alice = register(client)
    meeting = client.post("/api/meetings", json={"title": "Alice's meeting"}).json()
    token = client.cookies.get("__Host-m2n_session")
    login(client)
    assert client.get("/api/users").status_code == 200
    assert (
        client.patch(
            f"/api/users/{alice['id']}",
            json={"name": "Renamed", "password": "changed-password-123"},
        ).status_code
        == 200
    )
    client.cookies.clear()
    client.cookies.set("__Host-m2n_session", token)
    assert client.get("/api/auth/me").status_code == 401
    login(client, "alice", "changed-password-123")
    token = client.cookies.get("__Host-m2n_session")
    login(client)
    assert client.patch(f"/api/users/{alice['id']}", json={"active": False}).status_code == 200
    assert alice["id"] not in [u["id"] for u in client.get("/api/users/directory").json()]
    client.cookies.clear()
    client.cookies.set("__Host-m2n_session", token)
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/meetings", auth=("alice", "changed-password-123")).status_code == 401
    login(client)
    assert client.patch(f"/api/users/{alice['id']}", json={"active": True}).status_code == 200
    login(client, "alice", "changed-password-123")
    login(client)
    assert client.delete(f"/api/users/{alice['id']}").status_code == 204
    preserved = client.get(f"/api/meetings/{meeting['id']}").json()
    assert preserved["creator"]["name"] == "Renamed"
    assert preserved["creator"]["deleted"]
    assert alice["id"] not in [u["id"] for u in client.get("/api/users").json()]
    assert (
        client.post(
            "/api/auth/register", json={"name": "new", "username": "ALICE", "password": PASSWORD}
        ).status_code
        == 409
    )
    admin = client.get("/api/auth/me").json()
    assert client.delete(f"/api/users/{admin['id']}").status_code == 409
    assert client.patch(f"/api/users/{admin['id']}", json={"active": False}).status_code == 409


def test_settings_blocked_and_no_privilege_or_creator_injection(account_client):
    client = account_client
    user = register(client)
    for path in (
        "/settings",
        "/api/settings",
        "/api/settings/summary-api-key",
        "/api/users",
        "/api/webhooks",
        "/api/diagnostics/report",
        "/api/mcp/configuration",
    ):
        assert client.get(path).status_code == 403, path
    for path in (
        "/api/engines/summary/unload",
        "/api/plugins/rescan",
        "/api/rag/index/jobs",
        "/api/assistant/actions",
    ):
        assert client.post(path, json={}).status_code == 403, path
    assert client.patch(f"/api/users/{user['id']}", json={"is_admin": True}).status_code == 403
    assert client.get("/api/workspace/preferences").status_code == 200
    assert "api_key_env" not in client.get("/api/workspace/preferences").text
    assert (
        client.post("/api/meetings", json={"title": "forged", "creator_id": 1}).status_code == 422
    )
    client.cookies.clear()
    response = client.post(
        "/api/auth/register",
        json={"username": "eve", "name": "Eve", "password": PASSWORD, "is_admin": True},
    )
    assert response.status_code == 422
    assert PASSWORD not in response.text
    assert (
        client.post(
            "/api/auth/login", json={"username": "owner", "password": "x" * 300}
        ).status_code
        == 422
    )


def test_csrf_expired_session_and_login_throttling(account_client):
    client = account_client
    assert (
        client.post(
            "/api/auth/register",
            headers={"Origin": "https://evil.example"},
            json={"name": "Eve", "username": "eve", "password": PASSWORD},
        ).status_code
        == 403
    )
    register(client)
    assert (
        client.post(
            "/api/meetings", headers={"Sec-Fetch-Site": "cross-site"}, json={"title": "bad"}
        ).status_code
        == 403
    )
    with client.app.state.container.database.transaction() as conn:
        conn.execute("UPDATE user_sessions SET expires_at=?", (int(time.time()) - 1,))
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.clear()
    for _ in range(15):
        assert (
            client.post(
                "/api/auth/login", json={"username": "missing", "password": PASSWORD}
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/auth/login", json={"username": "MISSING", "password": PASSWORD}
        ).status_code
        == 429
    )


def test_bootstrap_is_idempotent_and_claims_legacy_meetings(settings):
    from local_meeting_ai.domain.enums import SourceType
    from local_meeting_ai.infrastructure.accounts import Accounts
    from local_meeting_ai.infrastructure.database.connection import Database
    from local_meeting_ai.infrastructure.database.migrations import MigrationRunner
    from local_meeting_ai.infrastructure.database.repositories import MeetingRepository

    settings.data_dir.mkdir(parents=True)
    db = Database(settings.data_dir / "legacy.db")
    MigrationRunner(db).apply()
    meeting = MeetingRepository(db).create(
        title="Legacy", description=None, source_type=SourceType.MANUAL, language=None
    )
    store = Accounts(db)
    store.bootstrap("owner", PASSWORD)
    store.bootstrap("different", "different-password-123")
    admin = store.authenticate("owner", PASSWORD)
    assert admin and admin["is_admin"]
    assert len(store.list_users()) == 1
    assert store.meeting_access(meeting.id, admin)["creator"]["id"] == admin["id"]


def test_admin_transfers_creator_preserves_managers_and_rechecks_permissions(account_client):
    from fastapi import HTTPException

    client = account_client
    alice = register(client)
    meeting = client.post("/api/meetings", json={"title": "Transfer me"}).json()
    other = client.post("/api/meetings", json={"title": "Keep owner"}).json()
    mid = meeting["id"]
    seed_transcript(client, mid)  # The library excludes empty draft meetings.
    bob = register(client, "bob", "Bob")
    charlie = register(client, "charlie", "Charlie")
    login(client)
    assert (
        client.put(
            f"/api/meetings/{mid}/managers", json={"user_ids": [bob["id"], charlie["id"]]}
        ).status_code
        == 200
    )
    response = client.put(f"/api/meetings/{mid}/creator", json={"user_id": bob["id"]})
    assert response.status_code == 200
    assert response.json()["creator"]["id"] == bob["id"]
    assert response.json()["can_transfer"] is True
    assert [u["id"] for u in response.json()["managers"]] == [charlie["id"]]
    assert client.get(f"/api/meetings/{other['id']}").json()["creator"]["id"] == alice["id"]
    assert f"meeting={mid}&amp;manage=creator" in client.get("/meetings").text
    assert client.get("/api/library").json()["items"][0]["can_transfer"] is True

    # A request authorized before transfer must recheck ownership in its transaction.
    with pytest.raises(HTTPException) as error:
        client.app.state.accounts.set_managers(mid, [], alice)
    assert error.value.status_code == 403
    login(client, "alice")
    assert client.patch(f"/api/meetings/{mid}", json={"title": "not allowed"}).status_code == 403
    assert (
        client.put(f"/api/meetings/{mid}/creator", json={"user_id": alice["id"]}).status_code == 403
    )
    login(client, "bob")
    assert client.get(f"/api/meetings/{mid}/access").json()["can_share"] is True
    assert client.get(f"/api/meetings/{mid}/access").json()["can_transfer"] is False
    assert client.patch(f"/api/meetings/{mid}", json={"title": "New owner"}).status_code == 200
    assert (
        client.put(f"/api/meetings/{mid}/creator", json={"user_id": alice["id"]}).status_code == 403
    )
    assert (
        client.put(
            f"/api/meetings/{mid}/managers", json={"user_ids": [bob["id"], charlie["id"]]}
        ).status_code
        == 200
    )
    login(client, "charlie")
    assert (
        client.patch(f"/api/meetings/{mid}", json={"title": "Co-manager edit"}).status_code == 200
    )
    assert (
        client.put(f"/api/meetings/{mid}/creator", json={"user_id": charlie["id"]}).status_code
        == 403
    )
    login(client)
    for invalid in ({"user_id": 999999}, {"user_id": True}, {"user_id": None}):
        assert client.put(f"/api/meetings/{mid}/creator", json=invalid).status_code == 422
    assert (
        client.put("/api/meetings/999999/creator", json={"user_id": bob["id"]}).status_code == 404
    )
    assert client.patch(f"/api/users/{alice['id']}", json={"active": False}).status_code == 200
    assert (
        client.put(f"/api/meetings/{mid}/creator", json={"user_id": alice["id"]}).status_code == 422
    )
    assert client.delete(f"/api/users/{alice['id']}").status_code == 204
    assert (
        client.put(f"/api/meetings/{mid}/creator", json={"user_id": alice["id"]}).status_code == 422
    )
    final = client.get(f"/api/meetings/{mid}/access").json()
    assert final["creator"]["id"] == bob["id"]
    assert [u["id"] for u in final["managers"]] == [charlie["id"]]
    with client.app.state.container.database.transaction() as conn:
        conn.executemany(
            "INSERT INTO meetings(uuid,title,status,source_type,created_at,updated_at) "
            "VALUES(?,?,'draft','manual','2099-01-01','2099-01-01')",
            [(f"newer-{i}", f"Newer {i}") for i in range(100)],
        )
    html = client.get(f"/?meeting={mid}&manage=creator")
    assert html.status_code == 200
    assert f'data-meeting-id="{mid}"' in html.text
    assert client.get("/?meeting=999999&manage=creator").status_code == 404
