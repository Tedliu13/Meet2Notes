"""Shared, conservative audio installation policy. No inference during detection."""
from __future__ import annotations

import os
import subprocess
from dataclasses import asdict, dataclass
from typing import Any

from local_meeting_ai.infrastructure.summary_hardware import Hardware, detect_hardware


def available_vram_mib(index: int) -> int | None:
    """Sample current free memory before a cold GPU load; never sum devices."""
    try:
        result = subprocess.run(
            ["nvidia-smi", f"--id={index}", "--query-gpu=memory.free",
             "--format=csv,noheader,nounits"], capture_output=True, text=True,
            timeout=5, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return int(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


@dataclass(frozen=True)
class AudioRecommendation:
    tier: str
    live_model: str
    final_model: str
    device: str
    device_index: int
    threads: int
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def preferences(self) -> dict[str, Any]:
        profiles = {"tiny": "fast", "base": "whisper-base", "small": "balanced",
                    "turbo": "whisper-turbo"}
        return {
            "audio_setup": {**self.as_dict(), "mode": "auto", "version": 1},
            "automatic_model_memory": True,
            "live_transcription_engine": "faster-whisper",
            "final_transcription_engine": "faster-whisper",
            "live_transcription_profile": profiles[self.live_model],
            "final_transcription_profile": profiles[self.final_model],
            "faster_whisper": {
                "model": self.live_model, "device": self.device,
                "device_index": self.device_index,
                "compute_type": "int8" if self.device == "cpu" else "auto",
                "cpu_threads": self.threads, "num_workers": 1,
                "preload_on_start": False, "keep_model_loaded": True,
            },
            "diarization": {
                "engine": "sherpa-onnx", "provider": "cpu", "num_threads": self.threads,
                "segmentation_model": "pyannote-3.0", "embedding_model": "3d-speaker",
                "quantized_segmentation": True, "preload_on_start": False,
                "keep_model_loaded": False,
            },
        }


def recommend_audio(
    hardware: Hardware | None = None, *, backend: str = "auto", cores: int | None = None,
    cuda_available: bool | None = None,
) -> AudioRecommendation:
    hardware = hardware or detect_hardware()
    cores = cores if cores is not None else (os.cpu_count() or 2)
    threads = max(1, min(4, cores // 2))
    eligible = [gpu for gpu in hardware.gpus if gpu.compute >= 7.0 and gpu.driver_major >= 525]
    if (backend != "cpu" and cuda_available is not False and eligible
            and hardware.system in {"Windows", "Linux"}
            and hardware.machine.lower() in {"amd64", "x86_64"}
            and hardware.ram_bytes >= 8 * 1024**3):
        gpu = max(eligible, key=lambda item: item.memory_mib)
        if gpu.memory_mib >= 3800:
            tier = "cuda-16gb" if gpu.memory_mib >= 15800 else (
                "cuda-8gb" if gpu.memory_mib >= 7800 else "cuda-4gb")
            return AudioRecommendation(tier, "small", "turbo", "cuda", gpu.index, threads,
                                       "Whisper Small live, Turbo final; Sherpa on CPU.")
    modest = cores < 8 or hardware.ram_bytes < 15 * 1024**3
    tiny = cores <= 2 or hardware.ram_bytes < 6 * 1024**3
    return AudioRecommendation(
        "cpu-light" if modest else "cpu-balanced", "tiny" if tiny else "base",
        "base" if modest else "small", "cpu", 0, threads,
        "Portable INT8 transcription and CPU Sherpa; no validated CUDA runtime available.",
    )
