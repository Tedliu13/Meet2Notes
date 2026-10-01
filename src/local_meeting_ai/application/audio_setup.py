"""Download the selected audio bundle before committing its preferences."""
from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

from local_meeting_ai.adapters.diarization.nemotron3 import Nemotron3DiarizationEngine
from local_meeting_ai.adapters.diarization.profile_matching import SherpaOnnxSpeakerProfileMatcher
from local_meeting_ai.adapters.diarization.sherpa_onnx import SherpaOnnxDiarizationEngine
from local_meeting_ai.adapters.transcription.faster_whisper import _detect_runtime_capability
from local_meeting_ai.application.transcription_config import FASTER_WHISPER_MODEL_REPOSITORIES
from local_meeting_ai.domain.errors import ValidationError
from local_meeting_ai.infrastructure.audio_hardware import AudioRecommendation, recommend_audio
from local_meeting_ai.infrastructure.database.repositories import SettingsRepository

logger = logging.getLogger(__name__)
_installation_lock = threading.Lock()


def audio_recommendation(backend: str = "auto") -> AudioRecommendation:
    runtime = _detect_runtime_capability()
    return recommend_audio(backend=backend, cuda_available=bool(runtime.get("cuda_available")))


def download_whisper(models_dir: Path, model: str) -> None:
    from huggingface_hub import snapshot_download

    logger.info("Downloading audio model: Whisper %s", model)
    folder = Path(snapshot_download(
        repo_id=FASTER_WHISPER_MODEL_REPOSITORIES[model], cache_dir=str(models_dir),
        allow_patterns=["config.json", "preprocessor_config.json", "model.bin",
                        "tokenizer.json", "vocabulary.*"],
    ))
    required = ("config.json", "model.bin", "tokenizer.json")
    if not all((folder / name).is_file() for name in required):
        raise RuntimeError(f"Whisper {model} download is incomplete")


def download_sherpa(models_dir: Path) -> None:
    engine = SherpaOnnxDiarizationEngine(models_dir)
    try:
        engine._download_models({"quantized_segmentation": True, "embedding_model": "3d-speaker"})
        if not engine._models_installed():
            raise RuntimeError("Sherpa download is incomplete")
    finally:
        engine.shutdown()


async def install_audio_bundle(
    models_dir: Path, preferences: SettingsRepository, *, backend: str = "auto",
    replace_existing: bool = False,
) -> dict[str, Any]:
    if not _installation_lock.acquire(blocking=False):
        raise ValidationError("An audio model installation is already running")
    try:
        task = asyncio.create_task(_install_audio_bundle(
            models_dir, preferences, backend=backend, replace_existing=replace_existing))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(task)
            finally:
                raise
    finally:
        _installation_lock.release()


async def download_default_diarization(models_dir: Path) -> None:
    engine = Nemotron3DiarizationEngine(models_dir)
    matcher = SherpaOnnxSpeakerProfileMatcher(models_dir)
    try:
        await engine.prepare({}, allow_model_download=True)
        await matcher.prepare({"embedding_model": "3d-speaker"}, allow_model_download=True)
    finally:
        engine.shutdown()
        matcher.shutdown()


async def _install_audio_bundle(
    models_dir: Path, preferences: SettingsRepository, *, backend: str,
    replace_existing: bool,
) -> dict[str, Any]:
    before = preferences.get_all()
    keys = ("faster_whisper", "diarization", "live_transcription_profile",
            "final_transcription_profile", "audio_setup", "live_transcription_engine",
            "final_transcription_engine")
    if not replace_existing and any(key in before for key in keys):
        logger.info("Preserving existing audio models and settings")
        return {"preserved": True}
    recommendation = await asyncio.to_thread(audio_recommendation, backend)
    values = recommendation.preferences()
    logger.info("Automatic audio profile: %s · %s", recommendation.tier, recommendation.reason)
    for model in dict.fromkeys((recommendation.live_model, recommendation.final_model)):
        await asyncio.to_thread(download_whisper, models_dir, model)
    await download_default_diarization(models_dir)
    # Do not overwrite choices changed while the download was in progress.
    current = preferences.get_all()
    if any(current.get(key) != before.get(key) for key in (*keys, "automatic_model_memory")):
        raise ValidationError(
            "Audio settings changed during installation; downloaded models are kept")
    preferences.update(values)
    return {"preserved": False, **recommendation.as_dict()}
