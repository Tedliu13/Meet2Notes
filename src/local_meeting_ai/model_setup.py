from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from local_meeting_ai.adapters.diarization.diarize_cpu import DiarizeCpuEngine
from local_meeting_ai.adapters.diarization.nemotron3 import Nemotron3DiarizationEngine
from local_meeting_ai.adapters.diarization.pyannote_community import (
    PyannoteCommunityDiarizationEngine,
)
from local_meeting_ai.adapters.embeddings import FastEmbedBgeM3Provider
from local_meeting_ai.adapters.summary.llama_cpp import LOCAL_MODELS, LlamaCppSummaryEngine
from local_meeting_ai.adapters.transcription.nvidia_asr import (
    build_nemotron_engine,
    build_parakeet_engine,
)
from local_meeting_ai.adapters.transcription.vibevoice import VibeVoiceBitNetEngine
from local_meeting_ai.application.ai_services import (
    DIARIZATION_DEFAULTS,
    SUMMARY_DEFAULTS,
)
from local_meeting_ai.application.audio_setup import install_audio_bundle
from local_meeting_ai.application.live_assistant import LIVE_ASSISTANT_DEFAULTS
from local_meeting_ai.application.rag import RAG_DEFAULTS
from local_meeting_ai.application.transcription_config import FASTER_WHISPER_MODELS
from local_meeting_ai.config import AppSettings
from local_meeting_ai.domain.entities import ModelProfile
from local_meeting_ai.infrastructure.database.connection import Database
from local_meeting_ai.infrastructure.database.migrations import MigrationRunner
from local_meeting_ai.infrastructure.database.repositories import SettingsRepository
from local_meeting_ai.infrastructure.summary_hardware import (
    LIGHT_PROFILE,
    detect_hardware,
    recommend,
)
from local_meeting_ai.paths import AppPaths

MODEL_CHOICES = (
    "all",
    "whisper",
    "diarization",
    "diarize",
    "pyannote-community-1",
    "nvidia-nemotron-3-diarization",
    "summary",
    "embeddings",
    "vibevoice-bitnet",
    "nvidia-parakeet",
    "nvidia-nemotron",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meet2notes-models",
        description=(
            "Download and verify Meet2Notes local AI models. Downloads are "
            "stored in the installation models directory by default."
        ),
    )
    parser.add_argument(
        "--models",
        nargs="+",
        choices=MODEL_CHOICES,
        default=["all"],
        help="Model groups to install (default: all)",
    )
    parser.add_argument(
        "--whisper-model",
        choices=("auto", *FASTER_WHISPER_MODELS),
        default="auto",
        help="Audio hardware profile, preserving existing preferences (default: auto)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Private application data directory",
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        help="Model directory (default: <Meet2Notes installation>/models)",
    )
    parser.add_argument(
        "--llm-profile",
        choices=("auto", "light", "bonsai-8b", "bonsai-1bit", "bonsai-ternary", "none"),
        default="auto", help="Automatic hardware recommendation; preserves existing AI settings",
    )
    parser.add_argument("--llm-backend", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument(
        "--repair-existing-summary", action="store_true",
        help="Updater mode: verify the saved managed LLM without migrations or settings changes",
    )
    return parser


async def install_models(
    selections: set[str],
    *,
    whisper_model: str,
    data_dir: Path | None,
    models_dir: Path | None,
    llm_profile: str = "auto",
    llm_backend: str = "auto",
    repair_existing_summary: bool = False,
) -> None:
    install_defaults = "all" in selections
    requested = (
        {"whisper", "diarization", "summary", "embeddings"}
        if install_defaults
        else selections
    )
    overrides: dict[str, Any] = {}
    if data_dir is not None:
        overrides["data_dir"] = data_dir
    if models_dir is not None:
        overrides["models_dir"] = models_dir
    settings = AppSettings(**overrides)
    paths = AppPaths.from_settings(settings)
    if repair_existing_summary:
        if paths.database.is_file():
            preferences = SettingsRepository(Database(paths.database))
            saved = preferences.get_all()
            if saved.get("summary_engine"):
                if settings.models_dir is None and saved.get("models_directory"):
                    paths = paths.with_models_directory(Path(saved["models_directory"]))
                await _install_summary(paths, preferences)
        return
    paths.ensure()
    database = Database(paths.database)
    MigrationRunner(database).apply()
    preferences = SettingsRepository(database)
    saved = preferences.get_all()
    if settings.models_dir is None and saved.get("models_directory"):
        paths = paths.with_models_directory(Path(saved["models_directory"]))
        paths.ensure()
    if models_dir is not None:
        preferences.update({"models_directory": str(paths.models)})
        print("Saved this model directory as the Meet2Notes runtime default.")
    print(f"Meet2Notes model directory: {paths.models}")

    if whisper_model == "auto" and "whisper" in requested:
        await install_audio_bundle(paths.models, preferences, backend=llm_backend)
        requested = requested - {"whisper", "diarization"}

    if "whisper" in requested:
        print(f"[1/4] Downloading and verifying Faster Whisper '{whisper_model}'...")
        await _install_whisper(paths, whisper_model)
        print("      Faster Whisper is ready.")

    if "diarization" in requested:
        print("[2/4] Downloading Nemotron 3 diarization and voice matching models...")
        await _install_diarization(paths)
        print("      Speaker diarization is ready.")

    if "diarize" in requested:
        print("Creating the isolated CPU runtime for diarize...")
        await _install_diarize(paths)
        print("      diarize is ready.")

    if "pyannote-community-1" in requested:
        print("Downloading and verifying Pyannote Community-1...")
        await _install_pyannote_community(paths, settings.pyannote_token)
        print("      Pyannote Community-1 is ready.")

    if "summary" in requested:
        await _install_summary(paths, preferences, llm_profile, llm_backend)
        if install_defaults and llm_profile != "none":
            await _install_live_summary(paths, preferences)

    if "embeddings" in requested:
        print("[4/4] Downloading and verifying BGE-M3 through FastEmbed...")
        await _install_embeddings(paths)
        print("      BGE-M3 embeddings are ready for CPU inference.")

    if "vibevoice-bitnet" in requested:
        print("Downloading Microsoft VibeVoice ASR BitNet (1.58 GB)...")
        await _install_vibevoice_bitnet(paths)
        print("      BitNet weights are ready; VibeASR.cpp is required for inference.")

    if "nvidia-parakeet" in requested:
        print("Downloading NVIDIA Parakeet TDT 0.6B v3 (~2.6 GB)...")
        await _install_nvidia_engine(paths, "parakeet")
        print("      NVIDIA Parakeet is ready for final transcription.")

    if "nvidia-nemotron" in requested:
        print("Downloading NVIDIA Nemotron 3.5 ASR Streaming 0.6B (~2.6 GB)...")
        await _install_nvidia_engine(paths, "nemotron")
        print("      NVIDIA Nemotron is ready for live and final transcription.")

    if "nvidia-nemotron-3-diarization" in requested:
        engine = Nemotron3DiarizationEngine(paths.models)
        try:
            await engine.prepare(DIARIZATION_DEFAULTS, allow_model_download=True)
            print("NVIDIA Nemotron 3 Diarization installed (up to 8 speakers).")
        finally:
            engine.shutdown()

    print("Meet2Notes model setup completed successfully.")


async def _install_whisper(paths: AppPaths, model: str) -> None:
    from local_meeting_ai.application.audio_setup import download_whisper

    await asyncio.to_thread(download_whisper, paths.models, model)


async def _install_diarization(paths: AppPaths) -> None:
    from local_meeting_ai.application.audio_setup import download_default_diarization

    await download_default_diarization(paths.models)


async def _install_summary(
    paths: AppPaths,
    preferences: SettingsRepository,
    selection: str = "auto",
    backend: str = "auto",
) -> None:
    if selection == "none":
        print("LLM installation skipped; existing AI settings are unchanged.")
        return
    previous = preferences.get_all().get("summary_engine")
    preserve = selection == "auto" and isinstance(previous, dict) and bool(previous)
    if preserve:
        assert isinstance(previous, dict)
        config = {**SUMMARY_DEFAULTS, **previous}
        if config.get("provider") != "local" or config.get("profile_id") not in LOCAL_MODELS:
            print("Keeping the existing external/custom AI model; no LLM download is needed.")
            return
        print(f"Keeping the existing AI model: {config['profile_id']}.")
    else:
        recommendation = recommend(detect_hardware(), backend)
        profile_id = {
            "light": LIGHT_PROFILE,
            "bonsai-8b": "bonsai-8b-1bit",
            "bonsai-1bit": "bonsai-27b-1bit",
            "bonsai-ternary": "bonsai-27b-ternary",
        }.get(selection, recommendation.profile)
        if backend == "cpu" and profile_id != LIGHT_PROFILE:
            raise ValueError("Choose --llm-profile light for forced CPU installation.")
        profile = LOCAL_MODELS[profile_id]
        config = {
            **SUMMARY_DEFAULTS, "provider": "local", "engine": "llama-cpp",
            "profile_id": profile_id, "model": profile["repository"],
            "model_file": profile["model_file"],
            "context_length": profile.get("context_length", SUMMARY_DEFAULTS["context_length"]),
            "main_gpu": recommendation.main_gpu,
            "gpu_layers": 0 if backend == "cpu" else -1,
            "preload_on_start": False,
        }
        print(f"LLM selection: {profile['display_name']}. " + (
            recommendation.reason if selection == "auto" else "Explicit installer selection."
        ))
    engine = LlamaCppSummaryEngine(paths.models)
    try:
        print("Downloading/verifying LLM files only. No model loading or test response.")
        try:
            await engine.prepare(config, allow_model_download=True)
        except Exception as error:
            if preserve or selection != "auto" or config["profile_id"] == LIGHT_PROFILE:
                raise
            print(f"Bonsai installation unavailable: {error}. Installing lightweight LFM.")
            profile = LOCAL_MODELS[LIGHT_PROFILE]
            config = {
                **SUMMARY_DEFAULTS, "profile_id": LIGHT_PROFILE,
                "model": profile["repository"], "model_file": profile["model_file"],
                "gpu_layers": 0, "preload_on_start": False,
            }
            await engine.prepare(config, allow_model_download=True)
        if not preserve:
            # Do not overwrite settings changed by the user while a large download was running.
            if preferences.get_all().get("summary_engine") == previous:
                preferences.update({"summary_engine": config})
            else:
                print("AI settings changed during installation; keeping the user's selection.")
        print("LLM files are ready. The model will load when requested by the user.")
    finally:
        engine.shutdown()


async def _install_live_summary(paths: AppPaths, preferences: SettingsRepository) -> None:
    saved = preferences.get_all().get("live_assistant")
    config = {**LIVE_ASSISTANT_DEFAULTS, **(saved if isinstance(saved, dict) else {})}
    if config.get("provider") != "local" or config.get("profile_id") not in LOCAL_MODELS:
        return
    # Live Assistant is independent: keep its lightweight default available
    # instead of silently replacing it with a second resident 27B model.
    engine = LlamaCppSummaryEngine(paths.models)
    try:
        print(f"Preparing the separate Live Assistant model: {config['profile_id']} (files only).")
        await engine.prepare(config, allow_model_download=True)
    finally:
        engine.shutdown()


async def _install_vibevoice_bitnet(paths: AppPaths) -> None:
    engine = VibeVoiceBitNetEngine(paths.models)
    profile = _vibevoice_profile(
        engine.name,
        "microsoft/VibeVoice-ASR-BitNet",
    )
    try:
        await engine.prepare(profile, allow_model_download=True)
    finally:
        engine.shutdown()


async def _install_embeddings(paths: AppPaths) -> None:
    provider = FastEmbedBgeM3Provider(paths.models)
    config: dict[str, Any] = {
        **RAG_DEFAULTS,
        "keep_model_loaded": False,
    }
    try:
        await provider.prepare(config, allow_model_download=True)
        await provider.unload("bge-m3")
    finally:
        provider.shutdown()


async def _install_diarize(paths: AppPaths) -> None:
    engine = DiarizeCpuEngine(paths.models)
    try:
        await engine.prepare(dict(DIARIZATION_DEFAULTS), allow_model_download=True)
        engine.unload()
    finally:
        engine.shutdown()


async def _install_pyannote_community(
    paths: AppPaths,
    access_token: str | None,
) -> None:
    engine = PyannoteCommunityDiarizationEngine(paths.models, access_token=access_token)
    config: dict[str, Any] = {
        **DIARIZATION_DEFAULTS,
        "engine": engine.name,
        "provider": "cpu",
        "keep_model_loaded": False,
    }
    try:
        await engine.prepare(config, allow_model_download=True)
        engine.unload()
    finally:
        engine.shutdown()


async def _install_nvidia_engine(paths: AppPaths, variant: str) -> None:
    engine = (
        build_parakeet_engine(paths.models)
        if variant == "parakeet"
        else build_nemotron_engine(paths.models)
    )
    profile = ModelProfile(
        id="setup",
        display_name="Installer",
        description="Installer profile",
        engine=engine.name,
        model=engine.repository,
        device="auto",
        compute_type="auto",
        beam_size=1,
        vad_filter=False,
        keep_model_loaded=False,
    )
    try:
        await engine.prepare(profile, allow_model_download=True)
    finally:
        engine.shutdown()


def _vibevoice_profile(engine: str, model: str) -> ModelProfile:
    return ModelProfile(
        id="setup",
        display_name="Installer",
        description="Installer profile",
        engine=engine,
        model=model,
        device="cpu",
        compute_type="auto",
        beam_size=1,
        vad_filter=False,
        keep_model_loaded=False,
    )


def main(argv: Sequence[str] | None = None) -> None:
    arguments = build_parser().parse_args(argv)
    try:
        asyncio.run(
            install_models(
                set(arguments.models),
                whisper_model=arguments.whisper_model,
                data_dir=arguments.data_dir,
                models_dir=arguments.models_dir,
                llm_profile=arguments.llm_profile,
                llm_backend=arguments.llm_backend,
                repair_existing_summary=arguments.repair_existing_summary,
            )
        )
    except KeyboardInterrupt as error:
        raise SystemExit("Model setup cancelled.") from error
    except Exception as error:
        raise SystemExit(f"Model setup failed: {error}") from error


if __name__ == "__main__":
    main()
