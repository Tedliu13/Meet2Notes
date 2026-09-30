from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from local_meeting_ai.adapters.transcription import faster_whisper as fw
from local_meeting_ai.domain.errors import CapabilityUnavailableError
from local_meeting_ai.infrastructure import linux_cuda as cuda


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch: Any) -> None:
    monkeypatch.setattr(cuda, "_status", None)
    monkeypatch.setattr(cuda, "_handles", [])
    monkeypatch.setattr(cuda.platform, "system", lambda: "Linux")


def test_non_linux_does_not_probe_or_load(monkeypatch: Any) -> None:
    monkeypatch.setattr(cuda.platform, "system", lambda: "Windows")
    monkeypatch.setattr(cuda, "_run_probe", lambda: pytest.fail("Native probe on Windows"))
    assert cuda.prepare_linux_cuda() == {"state": "not_applicable"}


def test_discovery_finds_active_pip_and_conda_libraries(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    site = tmp_path / "site-packages"
    pip_lib = site / "nvidia" / "cublas" / "lib"
    conda_lib = tmp_path / "lib"
    pip_lib.mkdir(parents=True)
    conda_lib.mkdir()
    monkeypatch.setattr(cuda.sys, "path", ["", ".", str(site)])
    monkeypatch.setattr(cuda.sys, "prefix", str(tmp_path))
    monkeypatch.setenv("LD_LIBRARY_PATH", f".:{pip_lib}:{pip_lib}")
    directories = cuda.library_directories()
    assert str(pip_lib.resolve()) in directories
    assert str(conda_lib.resolve()) in directories
    assert len(directories) == len(set(directories))
    assert str(Path.cwd()) not in directories


def test_loader_does_not_substitute_wrong_major(tmp_path: Path, monkeypatch: Any) -> None:
    (tmp_path / "libcublas.so.13").touch()
    correct = tmp_path / "libcublas.so.12.9.2"
    correct.touch()
    attempts = []

    def load(path: str, **_: Any) -> object:
        attempts.append(path)
        if path == str(correct):
            return object()
        raise OSError("missing")

    monkeypatch.setattr(cuda.ctypes, "CDLL", load)
    _, selected = cuda._load("libcublas.so.12", [str(tmp_path)])
    assert selected == str(correct)
    assert not any(".13" in p for p in attempts)


def test_missing_driver_never_loads_compute_libraries(monkeypatch: Any) -> None:
    def fail(*_: Any, **__: Any) -> None:
        raise OSError("no driver")

    monkeypatch.setattr(cuda.ctypes, "CDLL", fail)
    monkeypatch.setattr(cuda, "_nvidia_hardware_present", lambda: False)
    monkeypatch.setattr(cuda, "_load", lambda *args: pytest.fail("No GPU"))
    assert cuda._native_probe([])["state"] == "no_gpu"


def test_nvidia_hardware_without_driver_reports_warning(monkeypatch: Any) -> None:
    def fail(*_: Any, **__: Any) -> None:
        raise OSError("libcuda.so.1 missing")

    monkeypatch.setattr(cuda.ctypes, "CDLL", fail)
    monkeypatch.setattr(cuda, "_nvidia_hardware_present", lambda: True)
    result = cuda._native_probe([])
    assert result["state"] == "driver_unavailable"
    assert result["gpu_detected"] is True


@pytest.mark.parametrize("failure", ["crash", "timeout", "invalid_json"])
def test_native_probe_failure_is_contained(failure: str, monkeypatch: Any) -> None:
    def run(*args: Any, **kwargs: Any) -> Any:
        if failure == "timeout":
            raise subprocess.TimeoutExpired("probe", 30)
        return subprocess.CompletedProcess([], -6 if failure == "crash" else 0, "invalid", "abort")

    monkeypatch.setattr(cuda.subprocess, "run", run)
    assert cuda._run_probe()["state"] == "runtime_error"


def test_ready_libraries_are_preloaded_once_in_validated_order(monkeypatch: Any) -> None:
    loaded = []
    monkeypatch.setattr(
        cuda,
        "_run_probe",
        lambda: {
            "state": "ready",
            "loaded": ["/runtime/libcublasLt.so.12", "/runtime/libcublas.so.12"],
        },
    )
    monkeypatch.setattr(cuda.ctypes, "CDLL", lambda path, **kw: loaded.append(path))
    assert cuda.prepare_linux_cuda()["state"] == "ready"
    assert cuda.prepare_linux_cuda()["state"] == "ready"
    assert loaded == ["/runtime/libcublasLt.so.12", "/runtime/libcublas.so.12"]


def test_parent_load_failure_disables_cuda(monkeypatch: Any) -> None:
    monkeypatch.setattr(cuda, "_run_probe", lambda: {"state": "ready", "loaded": ["lib"]})

    def fail(*_: Any, **__: Any) -> None:
        raise OSError("dependency unavailable in parent")

    monkeypatch.setattr(cuda.ctypes, "CDLL", fail)
    result = cuda.prepare_linux_cuda()
    assert result["state"] == "runtime_error"
    assert "CPU/int8" in result["message"]


def test_repair_refuses_system_python(monkeypatch: Any) -> None:
    monkeypatch.setattr(cuda, "_private_environment", lambda: False)
    monkeypatch.setattr(cuda.subprocess, "run", lambda *a, **k: pytest.fail("System pip called"))
    assert cuda.install_libraries() == 1


def test_repair_preserves_existing_packages_and_verifies_result(monkeypatch: Any) -> None:
    commands = []
    monkeypatch.setattr(cuda, "_private_environment", lambda: True)
    monkeypatch.setattr(
        cuda.subprocess,
        "run",
        lambda command, **kw: commands.append(command) or subprocess.CompletedProcess(command, 0),
    )
    monkeypatch.setattr(cuda, "_run_probe", lambda: {"state": "libraries_unavailable"})
    assert cuda.install_libraries() == 1  # pip success alone is not readiness
    assert "--force-reinstall" not in commands[0]
    assert "--upgrade" not in commands[0]
    assert commands[0][0] == cuda.sys.executable


def test_automatic_installer_skips_cpu_machine(monkeypatch: Any) -> None:
    monkeypatch.setattr(cuda.sys, "argv", ["linux_cuda", "--install-if-needed"])
    monkeypatch.setattr(cuda, "_run_probe", lambda: {"state": "no_gpu"})
    monkeypatch.setattr(cuda, "install_libraries", lambda: pytest.fail("Unneeded download"))
    assert cuda.main() == 0


def test_damaged_wheel_repair_preserves_exact_version(monkeypatch: Any) -> None:
    calls = []
    results = iter(
        [
            {"state": "libraries_unavailable", "missing": ["libcudnn_ops.so.9"]},
            {"state": "ready"},
        ]
    )
    monkeypatch.setattr(cuda, "_private_environment", lambda: True)
    monkeypatch.setattr(cuda, "_run_probe", lambda: next(results))
    monkeypatch.setattr(cuda.importlib.metadata, "version", lambda package: "9.10.2.21")
    monkeypatch.setattr(
        cuda, "_pip_install", lambda packages, **kw: calls.append((packages, kw)) or 0
    )
    assert cuda.install_libraries() == 0
    assert calls[1] == (["nvidia-cudnn-cu12==9.10.2.21"], {"repair": True})


def test_api_repair_reports_restart_only_after_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(cuda, "prepare_linux_cuda", lambda: {"can_install": True})
    monkeypatch.setattr(
        cuda.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess([], 1, "offline", "")
    )
    with pytest.raises(CapabilityUnavailableError, match="offline"):
        asyncio.run(cuda.repair_linux_cuda())
    monkeypatch.setattr(
        cuda.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess([], 0, json.dumps({"state": "ready"}), ""),
    )
    assert asyncio.run(cuda.repair_linux_cuda())["restart_required"] is True


def test_engine_auto_falls_back_but_explicit_cuda_fails_early(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        fw,
        "_detect_runtime_capability",
        lambda: {
            "available": True,
            "cuda_devices": 1,
            "cuda_available": False,
            "ctranslate2_version": "4.8.1",
            "supported_compute_types": {"cpu": ["int8"]},
            "cuda_runtime": {"state": "libraries_unavailable", "message": "Missing cuBLAS"},
        },
    )
    engine = fw.FasterWhisperEngine(tmp_path)
    constructed = []

    def model(*args: Any, **kwargs: Any) -> object:
        constructed.append(kwargs)
        return object()

    options = dict(
        model="small",
        device_index=0,
        compute_type="float16",
        cpu_threads=4,
        num_workers=1,
        allow_model_download=False,
    )
    try:
        engine._get_model(model, device="auto", **options)
        assert constructed[0]["device"] == "cpu"
        assert constructed[0]["compute_type"] == "int8"
        with pytest.raises(CapabilityUnavailableError, match="Missing cuBLAS"):
            engine._get_model(model, device="cuda", **options)
        assert len(constructed) == 1
        capability = engine.capability()
        assert capability["cuda_devices"] == 1
        assert capability["cuda_available"] is False
        assert capability["recommended_device"] == "cpu"
    finally:
        engine.shutdown()
