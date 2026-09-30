from unittest.mock import AsyncMock

from local_meeting_ai.application import audio_setup
from local_meeting_ai.domain.enums import JobType
from local_meeting_ai.infrastructure.audio_hardware import AudioRecommendation


def test_preview_is_read_only_and_install_rejects_active_jobs(client, monkeypatch):
    container = client.app.state.container
    before = container.preferences.get_all()
    plan = AudioRecommendation("cuda-8gb", "small", "turbo", "cuda", 0, 4, "Test")
    monkeypatch.setattr(audio_setup, "audio_recommendation", lambda: plan)
    install = AsyncMock()
    monkeypatch.setattr(audio_setup, "install_audio_bundle", install)
    response = client.get("/api/engines/audio/recommendation")
    assert response.status_code == 200
    assert response.json()["recommendation"]["final_model"] == "turbo"
    assert container.preferences.get_all() == before
    container.jobs.create(meeting_id=None, job_type=JobType.DIARIZE, payload={})
    response = client.post("/api/engines/audio/automatic")
    assert response.status_code == 422
    install.assert_not_called()


def test_install_and_custom_controls(client, monkeypatch):
    install = AsyncMock(return_value={"tier": "cpu-balanced"})
    monkeypatch.setattr(audio_setup, "install_audio_bundle", install)
    assert client.post("/api/engines/audio/automatic").status_code == 200
    assert install.call_args.kwargs["replace_existing"] is True
    container = client.app.state.container
    container.preferences.update({"audio_setup": {"mode": "auto", "tier": "cpu-balanced"}})
    assert client.post("/api/engines/audio/custom").json() == {"mode": "custom"}
    assert container.preferences.get_all()["audio_setup"]["tier"] == "cpu-balanced"
    response = client.put("/api/settings", json={"automatic_model_memory": True})
    assert response.status_code == 200 and response.json()["automatic_model_memory"]
