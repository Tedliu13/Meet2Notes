def test_storage_overview_and_meeting_usage_are_read_only(client):
    container = client.app.state.container
    meeting = client.post("/api/meetings", json={"title": "Storage"}).json()
    entity = container.meetings.get(meeting["id"])
    folder = container.storage.ensure_meeting(entity.uuid)
    (folder / "original" / "recording.mp4").write_bytes(b"x" * 100)
    (folder / "exports" / "notes.doc").write_bytes(b"x" * 50)
    unrelated = container.paths.root / "shared-file"
    unrelated.write_bytes(b"x" * 200)
    model = container.paths.models / "model.gguf"
    model.write_bytes(b"x" * 300)
    response = client.get("/api/library/storage-usage", params={"meeting_ids": meeting["id"]})
    assert response.status_code == 200
    item = response.json()["meetings"][0]
    assert item["bytes"] == 150
    assert item["complete"]
    assert "path" not in item
    assert client.get("/api/library/storage-usage").json()["meetings"] == []
    assert client.get("/api/library/storage-usage?meeting_ids=99999").json()["meetings"] == []
    oversized = client.get("/api/library/storage-usage", params={"meeting_ids": [1] * 101})
    assert oversized.status_code == 422
    overview = client.get("/api/settings/storage-usage")
    assert overview.status_code == 200
    snapshot = overview.json()
    assert {item["name"] for item in snapshot["directories"]} == {"data", "models", "cache"}
    models = next(item for item in snapshot["directories"] if item["name"] == "models")
    assert models["bytes"] >= 300
    assert models["filesystem"]["total"] > 0
    assert snapshot["system"]["free"] > 0
    assert (folder / "original" / "recording.mp4").stat().st_size == 100
