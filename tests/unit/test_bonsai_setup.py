from __future__ import annotations

import hashlib
import io
import tarfile
import zipfile
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from local_meeting_ai import model_setup
from local_meeting_ai.adapters.summary.llama_cpp import LlamaCppSummaryEngine
from local_meeting_ai.config import AppSettings
from local_meeting_ai.domain.errors import CapabilityUnavailableError
from local_meeting_ai.infrastructure import bonsai_assets as assets
from local_meeting_ai.infrastructure.database.connection import Database
from local_meeting_ai.infrastructure.database.migrations import MigrationRunner
from local_meeting_ai.infrastructure.database.repositories import SettingsRepository
from local_meeting_ai.infrastructure.summary_hardware import (
    LIGHT_PROFILE,
    Hardware,
    NvidiaGpu,
    Recommendation,
    recommend,
)
from local_meeting_ai.paths import AppPaths


@pytest.mark.parametrize(("memory", "expected"), [
    (4096, LIGHT_PROFILE), (6144, LIGHT_PROFILE), (7799, LIGHT_PROFILE),
    (8192, "bonsai-27b-1bit"), (12288, "bonsai-27b-1bit"),
    (15800, "bonsai-27b-ternary"), (16380, "bonsai-27b-ternary"),
])
def test_vram_thresholds(memory, expected):
    hardware = Hardware("Windows", "amd64", 32 * 1024**3, (NvidiaGpu(0, memory, 8.6, 560),))
    assert recommend(hardware).profile == expected


def test_selects_one_compatible_card_and_never_sums_memory():
    hardware = Hardware("Linux", "x86_64", 32 * 1024**3, (
        NvidiaGpu(0, 24576, 6.1, 560), NvidiaGpu(1, 8192, 8.6, 560),
        NvidiaGpu(2, 8192, 8.6, 560),
    ))
    assert recommend(hardware).profile == "bonsai-27b-1bit"
    assert recommend(hardware).main_gpu == 1
    assert recommend(hardware, "cpu").profile == LIGHT_PROFILE


@pytest.mark.parametrize("hardware", [
    Hardware("Windows", "amd64", 8 * 1024**3, (NvidiaGpu(0, 16384, 8.6, 560),)),
    Hardware("Linux", "x86_64", 32 * 1024**3, (NvidiaGpu(0, 16384, 8.6, 530),)),
    Hardware("Windows", "arm64", 32 * 1024**3, (NvidiaGpu(0, 16384, 8.6, 560),)),
    Hardware("Windows", "amd64", 32 * 1024**3, (NvidiaGpu(0, 32768, 12.0, 610),)),
    Hardware("Darwin", "arm64", 64 * 1024**3),
    Hardware("Linux", "x86_64", 32 * 1024**3),
])
def test_conservative_fallback(hardware):
    assert recommend(hardware).profile == LIGHT_PROFILE


@pytest.fixture
def setup_paths(tmp_path):
    paths = AppPaths.from_settings(AppSettings(data_dir=tmp_path, models_dir=tmp_path / "models"))
    paths.ensure()
    database = Database(paths.database)
    MigrationRunner(database).apply()
    return paths, SettingsRepository(database)


@pytest.mark.asyncio
async def test_download_does_not_load_or_generate(tmp_path, monkeypatch):
    engine = LlamaCppSummaryEngine(tmp_path)
    resolve = Mock(return_value=tmp_path / "model.gguf")
    load = Mock(side_effect=AssertionError("Installer must not load a model"))
    monkeypatch.setattr(engine, "_resolve_model_path", resolve)
    monkeypatch.setattr(engine, "_get_model", load)
    try:
        await engine.prepare({"profile_id": "bonsai-27b-ternary"}, allow_model_download=True)
        load.assert_not_called()
        resolve.assert_called_once()
    finally:
        engine.shutdown()


def setup_recommendation(monkeypatch):
    monkeypatch.setattr(model_setup, "detect_hardware", Mock(return_value=None))
    monkeypatch.setattr(model_setup, "recommend", Mock(return_value=Recommendation(
        "bonsai-27b-1bit", "Test GPU", 2,
    )))


@pytest.mark.asyncio
async def test_fresh_install_persists_after_download_without_preloading(setup_paths, monkeypatch):
    paths, preferences = setup_paths
    setup_recommendation(monkeypatch)
    prepare = AsyncMock()
    monkeypatch.setattr(LlamaCppSummaryEngine, "prepare", prepare)
    await model_setup._install_summary(paths, preferences)
    config = preferences.get_all()["summary_engine"]
    assert config["profile_id"] == "bonsai-27b-1bit"
    assert config["context_length"] == 8192
    assert config["main_gpu"] == 2
    assert config["preload_on_start"] is False
    assert prepare.call_args.kwargs == {"allow_model_download": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("config", [
    {"provider": "litellm", "profile_id": "ollama", "model": "ollama_chat/qwen3:8b"},
    {"provider": "local", "profile_id": "custom-gguf", "model_path": "my-model.gguf"},
    {"provider": "local", "profile_id": LIGHT_PROFILE, "context_length": 4096},
])
async def test_update_preserves_user_choices(config, setup_paths, monkeypatch):
    paths, preferences = setup_paths
    preferences.update({"summary_engine": config})
    prepare = AsyncMock()
    monkeypatch.setattr(LlamaCppSummaryEngine, "prepare", prepare)
    monkeypatch.setattr(model_setup, "detect_hardware", Mock(side_effect=AssertionError))
    await model_setup._install_summary(paths, preferences)
    assert preferences.get_all()["summary_engine"] == config
    assert prepare.call_count == (1 if config["profile_id"] == LIGHT_PROFILE else 0)


@pytest.mark.asyncio
async def test_failed_auto_download_falls_back_but_explicit_choice_fails(setup_paths, monkeypatch):
    paths, preferences = setup_paths
    setup_recommendation(monkeypatch)
    prepare = AsyncMock(side_effect=[OSError("No space"), None])
    monkeypatch.setattr(LlamaCppSummaryEngine, "prepare", prepare)
    await model_setup._install_summary(paths, preferences)
    assert preferences.get_all()["summary_engine"]["profile_id"] == LIGHT_PROFILE
    prepare.side_effect = OSError("No space")
    with pytest.raises(OSError):
        await model_setup._install_summary(paths, preferences, "bonsai-ternary")
    assert preferences.get_all()["summary_engine"]["profile_id"] == LIGHT_PROFILE


@pytest.mark.asyncio
async def test_concurrent_user_selection_is_not_overwritten(setup_paths, monkeypatch):
    paths, preferences = setup_paths
    setup_recommendation(monkeypatch)
    user_choice = {"provider": "litellm", "profile_id": "ollama"}

    async def download(*args, **kwargs):
        preferences.update({"summary_engine": user_choice})

    monkeypatch.setattr(LlamaCppSummaryEngine, "prepare", download)
    await model_setup._install_summary(paths, preferences)
    assert preferences.get_all()["summary_engine"] == user_choice


@pytest.mark.parametrize("bad_name", ["../outside", "/outside", "C:/outside", "..\\outside"])
def test_archive_paths_cannot_escape(tmp_path, bad_name):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(bad_name, b"bad")
    with pytest.raises(CapabilityUnavailableError):
        assets.extract_archive(archive, tmp_path / "destination")


def test_tar_links_are_safe_copies(tmp_path):
    archive = tmp_path / "runtime.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        file = tarfile.TarInfo("lib/libggml.so.1")
        file.size = 3
        tar.addfile(file, io.BytesIO(b"dll"))
        link = tarfile.TarInfo("lib/libggml.so")
        link.type = tarfile.SYMTYPE
        link.linkname = "libggml.so.1"
        tar.addfile(link)
    root = tmp_path / "extracted"
    assets.extract_archive(archive, root)
    assert (root / "lib/libggml.so").read_bytes() == b"dll"
    assert not (root / "lib/libggml.so").is_symlink()


def test_download_rejects_corruption_and_keeps_original(tmp_path, monkeypatch):
    destination = tmp_path / "model.gguf"
    destination.write_bytes(b"old")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"bad"))
    with httpx.Client(transport=transport) as client:
        monkeypatch.setattr(assets.httpx, "stream", client.stream)
        with pytest.raises(CapabilityUnavailableError, match="Integrity"):
            assets.download_file("https://example.test/model", destination, 3,
                                 hashlib.sha256(b"new").hexdigest())
    assert destination.read_bytes() == b"old"
    assert not list(tmp_path.glob("*.part"))


def test_runtime_installation_is_atomic_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "supported_platform", lambda: True)
    monkeypatch.setattr(assets.platform, "system", lambda: "Windows")
    monkeypatch.setattr(assets, "ASSETS", {"Windows": [("runtime.zip", 1, "digest")]})

    def download(url, destination, size, digest):
        with zipfile.ZipFile(destination, "w") as output:
            output.writestr("llama-server.exe", b"test executable; never run")

    fetch = Mock(side_effect=download)
    monkeypatch.setattr(assets, "download_file", fetch)
    assets.install_runtime(tmp_path)
    assets.install_runtime(tmp_path)
    assert assets.runtime_executable(tmp_path).is_file()
    assert fetch.call_count == 1


@pytest.mark.asyncio
async def test_models_directory_and_skip_are_respected(setup_paths, monkeypatch, tmp_path):
    paths, preferences = setup_paths
    custom = tmp_path / "user-models"
    preferences.update({"models_directory": str(custom)})
    prepare = AsyncMock()
    monkeypatch.setattr(LlamaCppSummaryEngine, "prepare", prepare)
    await model_setup.install_models(
        {"summary"}, whisper_model="small", data_dir=paths.root,
        models_dir=None, llm_profile="none",
    )
    assert custom.is_dir()
    prepare.assert_not_called()
    assert "summary_engine" not in preferences.get_all()


@pytest.mark.asyncio
async def test_stable_updater_does_not_migrate_or_change_settings(setup_paths, monkeypatch):
    paths, preferences = setup_paths
    config = {"profile_id": "bonsai-27b-ternary", "context_length": 4096}
    preferences.update({"summary_engine": config})
    prepare = AsyncMock()
    monkeypatch.setattr(LlamaCppSummaryEngine, "prepare", prepare)
    monkeypatch.setattr(MigrationRunner, "apply", Mock(side_effect=AssertionError("No migrations")))
    monkeypatch.setattr(SettingsRepository, "update", Mock(side_effect=AssertionError("No writes")))
    await model_setup.install_models(
        {"all"}, whisper_model="small", data_dir=paths.root,
        models_dir=None, repair_existing_summary=True,
    )
    prepare.assert_awaited_once()
    assert preferences.get_all()["summary_engine"] == config


@pytest.mark.asyncio
async def test_existing_bonsai_failure_keeps_profile(setup_paths, monkeypatch):
    paths, preferences = setup_paths
    config = {"profile_id": "bonsai-27b-1bit"}
    preferences.update({"summary_engine": config})
    prepare = AsyncMock(side_effect=OSError("Network unavailable"))
    monkeypatch.setattr(LlamaCppSummaryEngine, "prepare", prepare)
    with pytest.raises(OSError):
        await model_setup._install_summary(paths, preferences)
    assert preferences.get_all()["summary_engine"] == config
    assert prepare.call_count == 1


@pytest.mark.asyncio
async def test_environment_model_directory_overrides_saved_directory(setup_paths, monkeypatch):
    paths, preferences = setup_paths
    custom = paths.root / "environment-models"
    preferences.update({"models_directory": str(paths.root / "old-models")})
    monkeypatch.setenv("M2N_MODELS_DIR", str(custom))
    await model_setup.install_models(
        {"summary"}, whisper_model="small", data_dir=paths.root,
        models_dir=None, llm_profile="none",
    )
    assert custom.is_dir()
    assert not (paths.root / "old-models").exists()


@pytest.mark.asyncio
async def test_full_install_keeps_lightweight_live_model_available(setup_paths, monkeypatch):
    paths, preferences = setup_paths
    setup_recommendation(monkeypatch)
    for installer in ("_install_whisper", "_install_diarization", "_install_embeddings"):
        monkeypatch.setattr(model_setup, installer, AsyncMock())
    prepare = AsyncMock()
    monkeypatch.setattr(LlamaCppSummaryEngine, "prepare", prepare)
    await model_setup.install_models(
        {"all"}, whisper_model="small", data_dir=paths.root, models_dir=paths.models,
    )
    assert [call.args[0]["profile_id"] for call in prepare.call_args_list] == [
        "bonsai-27b-1bit", LIGHT_PROFILE,
    ]
    assert all(call.kwargs["allow_model_download"] for call in prepare.call_args_list)
    assert "live_assistant" not in preferences.get_all()
