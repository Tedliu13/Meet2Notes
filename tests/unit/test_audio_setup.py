from unittest.mock import AsyncMock, Mock

import pytest

from local_meeting_ai.application import audio_setup
from local_meeting_ai.infrastructure.audio_hardware import recommend_audio
from local_meeting_ai.infrastructure.summary_hardware import Hardware, NvidiaGpu


@pytest.mark.asyncio
async def test_default_diarization_downloads_engine_and_voice_embeddings_only(
    tmp_path, monkeypatch,
):
    engine = Mock(prepare=AsyncMock())
    matcher = Mock(prepare=AsyncMock())
    monkeypatch.setattr(audio_setup, "Nemotron3DiarizationEngine", lambda path: engine)
    monkeypatch.setattr(audio_setup, "SherpaOnnxSpeakerProfileMatcher", lambda path: matcher)
    await audio_setup.download_default_diarization(tmp_path)
    engine.prepare.assert_awaited_once_with({}, allow_model_download=True)
    matcher.prepare.assert_awaited_once_with(
        {"embedding_model": "3d-speaker"}, allow_model_download=True,
    )
    engine.shutdown.assert_called_once()
    matcher.shutdown.assert_called_once()


@pytest.mark.parametrize(("vram", "tier"), [
    (2048, "cpu-balanced"), (4096, "cuda-4gb"), (8192, "cuda-8gb"), (16384, "cuda-16gb"),
])
def test_recommendation_selects_bundle(vram, tier):
    hardware = Hardware("Windows", "amd64", 32 * 1024**3, (NvidiaGpu(0, vram, 8.6, 560),))
    plan = recommend_audio(hardware, cores=12, cuda_available=True)
    assert plan.tier == tier
    assert plan.preferences()["diarization"]["provider"] == plan.device
    assert plan.preferences()["diarization"]["engine"] == "nvidia-nemotron-3-diarization"
    assert plan.preferences()["automatic_model_memory"]
    if vram >= 4096:
        assert (plan.live_model, plan.final_model) == ("small", "turbo")
    assert recommend_audio(hardware, cores=12, cuda_available=False).device == "cpu"
    assert recommend_audio(hardware, cores=12, backend="cpu").device == "cpu"


def test_cpu_and_unified_memory_profiles():
    assert recommend_audio(Hardware("Linux", "x86_64", 4 * 1024**3), cores=2).live_model == "tiny"
    mac = recommend_audio(Hardware("Darwin", "arm64", 16 * 1024**3), cores=8)
    assert mac.device == "cpu" and mac.final_model == "small"


@pytest.mark.asyncio
async def test_existing_choices_are_never_replaced_by_installer(tmp_path, monkeypatch):
    preferences = Mock()
    preferences.get_all.return_value = {"diarization": {"engine": "diarize"}}
    download = Mock(side_effect=AssertionError("Do not download unused models"))
    monkeypatch.setattr(audio_setup, "download_whisper", download)
    result = await audio_setup.install_audio_bundle(tmp_path, preferences)
    assert result == {"preserved": True}
    preferences.update.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_bundle_commits_only_after_all_downloads(tmp_path, monkeypatch, fail):
    preferences = Mock()
    preferences.get_all.return_value = {}
    plan = recommend_audio(Hardware("Linux", "x86_64", 32 * 1024**3), cores=8)
    monkeypatch.setattr(audio_setup, "audio_recommendation", lambda backend: plan)
    downloaded = []
    monkeypatch.setattr(audio_setup, "download_whisper",
                        lambda path, model: downloaded.append(model))
    monkeypatch.setattr(audio_setup, "download_default_diarization", AsyncMock(
        side_effect=RuntimeError("Download failed") if fail else None))
    if fail:
        with pytest.raises(RuntimeError, match="Download failed"):
            await audio_setup.install_audio_bundle(tmp_path, preferences)
        preferences.update.assert_not_called()
    else:
        await audio_setup.install_audio_bundle(tmp_path, preferences)
        preferences.update.assert_called_once_with(plan.preferences())
    assert downloaded == ["base", "small"]
