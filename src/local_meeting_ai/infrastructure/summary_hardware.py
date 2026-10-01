"""Conservative installer recommendations; never import or load an AI model."""
from __future__ import annotations

import csv
import ctypes
import os
import platform
import subprocess
from dataclasses import dataclass

LIGHT_PROFILE = "lfm2.5-1.2b-q4"


@dataclass(frozen=True)
class NvidiaGpu:
    index: int
    memory_mib: int
    compute: float
    driver_major: int


@dataclass(frozen=True)
class Hardware:
    system: str
    machine: str
    ram_bytes: int
    gpus: tuple[NvidiaGpu, ...] = ()


@dataclass(frozen=True)
class Recommendation:
    profile: str
    reason: str
    main_gpu: int = 0


def _ram_bytes() -> int:
    try:
        if platform.system() == "Windows":
            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                    *[(name, ctypes.c_ulonglong) for name in (
                        "total", "available", "page", "free_page", "virtual",
                        "free_virtual", "extended",
                    )],
                ]
            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            loader_name = "windll"
            loader = getattr(ctypes, loader_name)
            if loader.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.total)
        elif platform.system() == "Darwin":
            return int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], timeout=5))
        else:
            function_name = "sysconf"
            sysconf = getattr(os, function_name)
            return int(sysconf("SC_PHYS_PAGES")) * int(sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError, subprocess.SubprocessError):
        pass
    return 0


def detect_hardware() -> Hardware:
    gpus = []
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,memory.total,compute_cap,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for row in csv.reader(result.stdout.splitlines()):
            try:
                gpus.append(NvidiaGpu(int(row[0]), int(row[1]), float(row[2]),
                                      int(row[3].strip().split(".")[0])))
            except (ValueError, IndexError):
                continue
    except (OSError, subprocess.SubprocessError):
        pass
    return Hardware(platform.system(), platform.machine().lower(), _ram_bytes(), tuple(gpus))


def recommend(hardware: Hardware, backend: str = "auto") -> Recommendation:
    if backend == "cpu":
        return Recommendation(LIGHT_PROFILE, "CPU mode was requested.")
    if hardware.system not in {"Windows", "Linux"} or hardware.machine not in {
        "amd64", "x86_64",
    }:
        return Recommendation(LIGHT_PROFILE, "No validated dedicated CUDA GPU configuration.")
    if hardware.ram_bytes < 15 * 1024**3:
        return Recommendation(LIGHT_PROFILE, "Bonsai automatic setup requires at least 16 GB RAM.")
    # The pinned CUDA 12.4 binaries target Turing through Hopper. Do not assume
    # forward compatibility for a newer architecture without an inference test.
    eligible = [gpu for gpu in hardware.gpus if 7.5 <= gpu.compute <= 9.0 and gpu.driver_major >= (
        552 if hardware.system == "Windows" else 550
    )]
    if not eligible:
        return Recommendation(LIGHT_PROFILE, "No compatible NVIDIA GPU/driver was detected.")
    gpu = max(eligible, key=lambda item: item.memory_mib)
    # Do not add multiple GPUs together; allow the small firmware reservation on 16 GB cards.
    if gpu.memory_mib >= 15800:
        return Recommendation(
            "bonsai-27b-ternary", "NVIDIA GPU with at least 16 GB VRAM.", gpu.index,
        )
    if gpu.memory_mib >= 7800:
        return Recommendation("bonsai-27b-1bit", "NVIDIA GPU with at least 8 GB VRAM.", gpu.index)
    if gpu.memory_mib >= 2800:
        return Recommendation(
            "bonsai-8b-1bit", "Compatible NVIDIA GPU with 3-7 GB VRAM.", gpu.index,
        )
    return Recommendation(LIGHT_PROFILE, "Less than 3 GB VRAM; keep the lightweight model.")
