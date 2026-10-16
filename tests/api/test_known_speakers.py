from __future__ import annotations


def test_roster_is_validated_atomically_persisted_and_survives_profile_deletion(client):
    container = client.app.state.container
    profile = container.speaker_profiles.create(name="Known person", sample_path="saved.wav")
    incomplete = container.speaker_profiles.create(name="No sample", sample_path=None)
    before = len(container.meetings.list())
    for ids in ([999999], [incomplete.id], [True], [-1], [profile.id] * 101):
        response = client.post(
            "/api/meetings",
            json={
                "title": "Invalid roster",
                "known_speaker_profile_ids": ids,
            },
        )
        assert response.status_code == 422, response.text
    assert len(container.meetings.list()) == before
    created = client.post(
        "/api/meetings",
        json={
            "title": "Known voices",
            "known_speaker_profile_ids": [profile.id, profile.id],
        },
    )
    assert created.status_code == 201
    mid = created.json()["id"]
    assert created.json()["known_speaker_profile_ids"] == [profile.id]
    assert container.meetings.get(mid).known_speaker_profile_ids == [profile.id]
    container.speaker_profiles.delete(profile.id)
    assert client.get(f"/api/meetings/{mid}").json()["known_speaker_profile_ids"] == [profile.id]
    assert (
        client.post("/api/meetings", json={"title": "Legacy behavior"}).json()[
            "known_speaker_profile_ids"
        ]
        == []
    )
