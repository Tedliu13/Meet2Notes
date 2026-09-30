"""Discover and activate Linux CUDA libraries without changing the host system.

Native checks run in a child process: a broken driver/library must not abort the
web server. Validated libraries are then preloaded by absolute path, in dependency
order. Updating LD_LIBRARY_PATH inside an already running Python is insufficient.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import importlib.metadata
import json
import logging
import os
import platform
import shlex
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_status: dict[str, Any] | None = None
_handles: list[Any] = []
_install_lock = threading.Lock()
PACKAGES = ("nvidia-cublas-cu12>=12,<13", "nvidia-cudnn-cu12>=9,<10")
# cuDNN loads component libraries lazily by soname. Preload them too, rather
# than only loading libcudnn and discovering missing dependencies at inference.
LIBRARIES = (
    ("libnvrtc.so.12", False),
    ("libcudart.so.12", False),
    ("libcublasLt.so.12", True),
    ("libcublas.so.12", True),
    ("libcudnn.so.9", True),
    ("libcudnn_graph.so.9", True),
    ("libcudnn_ops.so.9", True),
    ("libcudnn_cnn.so.9", True),
    ("libcudnn_adv.so.9", False),
    ("libcudnn_engines_precompiled.so.9", True),
    ("libcudnn_engines_runtime_compiled.so.9", False),
    ("libcudnn_heuristic.so.9", True),
)


def library_directories() -> list[str]:
    """Search the active environment and standard CUDA locations, never cwd."""
    candidates: list[Path] = []
    for value in os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep):
        if value and Path(value).is_absolute():
            candidates.append(Path(value))
    for value in sys.path:
        root = Path(value)
        if value and root.is_absolute() and root.is_dir():
            candidates.extend(sorted((root / "nvidia").glob("*/lib")))
            candidates.append(root / "torch" / "lib")
    candidates.append(Path(sys.prefix) / "lib")  # conda / Pinokio
    roots = [Path("/usr/local/cuda"), Path("/opt/cuda")]
    roots.extend(sorted(Path("/usr/local").glob("cuda-12*"), reverse=True))
    for name in ("CUDA_HOME", "CUDA_PATH"):
        cuda_root = os.environ.get(name)
        if cuda_root and Path(cuda_root).is_absolute():
            roots.insert(0, Path(cuda_root))
    for root in roots:
        candidates.extend((root / "lib64", root / "lib"))
        candidates.extend(sorted((root / "targets").glob("*/lib")))
    return list(dict.fromkeys(str(p.resolve()) for p in candidates if p.is_dir()))


def _load(name: str, directories: list[str]) -> tuple[Any, str]:
    # Respect a working system linker configuration first. Match major versions
    # exactly; CUDA 13 or cuDNN 8 cannot substitute for CUDA 12 / cuDNN 9.
    candidates = [name]
    for directory in directories:
        base = Path(directory) / name
        candidates.append(str(base))
        candidates.extend(str(p) for p in sorted(base.parent.glob(name + ".*")))
    error = ""
    for candidate in candidates:
        try:
            return ctypes.CDLL(candidate, mode=ctypes.RTLD_GLOBAL), candidate
        except OSError as exc:
            # Prefer the error for a real file (e.g. a missing dependency).
            if not error or Path(candidate).is_file():
                error = str(exc)
    raise OSError(f"{name}: {error}")


def _nvidia_hardware_present() -> bool:
    for vendor in Path("/sys/bus/pci/devices").glob("*/vendor"):
        try:
            if vendor.read_text().strip().lower() == "0x10de":
                return True
        except OSError:
            continue
    return False


def _native_probe(directories: list[str]) -> dict[str, Any]:
    try:
        driver = ctypes.CDLL("libcuda.so.1")
        code = driver.cuInit(0)
        count = ctypes.c_int()
        if code or driver.cuDeviceGetCount(ctypes.byref(count)) or count.value == 0:
            return {
                "state": "driver_unavailable",
                "gpu_detected": False,
                "detail": f"NVIDIA driver initialization failed (CUDA status {code}).",
            }
    except (OSError, AttributeError) as exc:
        detected = _nvidia_hardware_present()
        return {
            "state": "driver_unavailable" if detected else "no_gpu",
            "gpu_detected": detected,
            "detail": str(exc),
        }
    loaded: dict[str, Any] = {}
    paths: list[str] = []
    pending = list(LIBRARIES)
    errors: dict[str, str] = {}
    # Different cuDNN builds arrange their dependencies differently.
    while pending:
        remaining = []
        for name, required in pending:
            try:
                handle, path = _load(name, directories)
                loaded[name] = handle
                paths.append(path)
            except OSError as exc:
                errors[name] = str(exc)
                remaining.append((name, required))
        if len(remaining) == len(pending):
            break
        pending = remaining
    missing = [name for name, required in pending if required]
    if missing:
        return {
            "state": "libraries_unavailable",
            "gpu_detected": True,
            "missing": missing,
            "detail": "\n".join(errors[n] for n in missing),
        }
    try:
        for library, create, destroy in (
            ("libcublas.so.12", "cublasCreate_v2", "cublasDestroy_v2"),
            ("libcudnn.so.9", "cudnnCreate", "cudnnDestroy"),
        ):
            handle = ctypes.c_void_p()
            code = getattr(loaded[library], create)(ctypes.byref(handle))
            if code:
                raise RuntimeError(f"{create} failed (status {code})")
            getattr(loaded[library], destroy)(handle)
    except (AttributeError, RuntimeError) as exc:
        return {"state": "runtime_error", "gpu_detected": True, "detail": str(exc)}
    return {"state": "ready", "gpu_detected": True, "loaded": paths}


def _run_probe() -> dict[str, Any]:
    try:
        process = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--probe"],
            input=json.dumps(library_directories()),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if process.returncode:
            return {
                "state": "runtime_error",
                "gpu_detected": False,
                "detail": f"CUDA check exited with {process.returncode}: " + process.stderr[-2000:],
            }
        result: dict[str, Any] = json.loads(process.stdout)
        return result
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return {"state": "runtime_error", "gpu_detected": False, "detail": str(exc)}


def prepare_linux_cuda() -> dict[str, Any]:
    """Cached, thread-safe readiness check and process-local configuration."""
    global _status
    if platform.system() != "Linux":
        return {"state": "not_applicable"}
    with _lock:
        if _status is not None:
            return dict(_status)
        result = _run_probe()
        if result["state"] == "ready":
            try:
                for path in result["loaded"]:
                    _handles.append(ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL))
            except OSError as exc:
                result.update(state="runtime_error", detail=str(exc))
        result["repair_command"] = shlex.join(
            [
                sys.executable,
                "-m",
                "local_meeting_ai.infrastructure.linux_cuda",
                "--install",
            ]
        )
        result["can_install"] = (
            result["state"] == "libraries_unavailable" and _private_environment()
        )
        if result["state"] not in {"ready", "no_gpu"}:
            result["message"] = (
                "Linux CUDA is not ready. Auto uses CPU/int8. "
                "Run the repair command in the Meet2Notes environment, then restart. "
                "If it still fails, check the NVIDIA driver with nvidia-smi; "
                "containers also require GPU access (--gpus all)."
            )
            logger.warning("%s %s", result["message"], result.get("detail", ""))
        _status = result
        return dict(result)


def _private_environment() -> bool:
    return sys.prefix != sys.base_prefix or (Path(sys.prefix) / "conda-meta").is_dir()


async def repair_linux_cuda() -> dict[str, Any]:
    return await asyncio.to_thread(_repair_sync)


def _repair_sync() -> dict[str, Any]:
    global _status
    from local_meeting_ai.domain.errors import CapabilityUnavailableError

    status = prepare_linux_cuda()
    if not status.get("can_install"):
        raise CapabilityUnavailableError(
            "Automatic repair requires missing CUDA libraries and a private Linux "
            "Python environment. "
            "Check the CUDA diagnostics in Settings."
        )
    if not _install_lock.acquire(blocking=False):
        raise CapabilityUnavailableError("A Linux CUDA repair is already running.")
    try:
        logger.info("Installing missing Linux CUDA libraries in %s", sys.prefix)
        try:
            process = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--install"],
                capture_output=True,
                text=True,
                timeout=1900,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CapabilityUnavailableError(f"Linux CUDA repair failed: {exc}") from exc
        if process.returncode:
            detail = (process.stdout + process.stderr)[-3000:]
            logger.warning("Linux CUDA repair failed: %s", detail)
            raise CapabilityUnavailableError(
                "Linux CUDA repair failed. Check the activity log and NVIDIA driver. " + detail
            )
        logger.info("Linux CUDA libraries repaired. Restart Meet2Notes to activate them.")
        with _lock:
            _status = {
                **status,
                "state": "restart_required",
                "can_install": False,
                "detail": "",
                "message": "CUDA libraries are ready. Restart Meet2Notes "
                "to activate them. Auto continues using CPU until restart.",
            }
        return {"state": "restart_required", "restart_required": True}
    finally:
        _install_lock.release()


def install_libraries() -> int:
    """Only install into the active private Python environment; never use sudo."""
    if not _private_environment():
        print("Activate the Meet2Notes virtual environment or Pinokio environment first.")
        return 1
    code = _pip_install(list(PACKAGES))
    if code:
        return code
    result = _run_probe()
    if result["state"] == "libraries_unavailable":
        # pip's "already satisfied" does not verify package files. Repair a
        # damaged wheel at its exact installed version, preserving Torch pins.
        damaged = set()
        for name in result.get("missing", []):
            if name.startswith("libcublas"):
                damaged.add("nvidia-cublas-cu12")
            elif name.startswith("libcudnn"):
                damaged.add("nvidia-cudnn-cu12")
        pinned = []
        for package in sorted(damaged):
            try:
                pinned.append(f"{package}=={importlib.metadata.version(package)}")
            except importlib.metadata.PackageNotFoundError:
                continue
        if pinned:
            code = _pip_install(pinned, repair=True)
            if code:
                return code
            result = _run_probe()
    print(json.dumps(result, indent=2))
    return 0 if result["state"] == "ready" else 1


def _pip_install(packages: list[str], *, repair: bool = False) -> int:
    command = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    if repair:
        command.extend(("--force-reinstall", "--no-deps"))
    try:
        return subprocess.run(command + packages, check=False, timeout=900).returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"CUDA library installation failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--install-if-needed", action="store_true")
    args = parser.parse_args()
    if platform.system() != "Linux":
        return 0
    if args.probe:
        print(json.dumps(_native_probe(json.load(sys.stdin))))
        return 0
    result = _run_probe()
    if args.install or args.install_if_needed:
        if result["state"] == "ready":
            print("Linux CUDA libraries are ready.")
            return 0
        if result["state"] == "libraries_unavailable":
            code = install_libraries()
        elif args.install_if_needed and result["state"] == "no_gpu":
            return 0
        else:
            print(json.dumps(result, indent=2))
            code = 1
        if code:
            print(
                "CUDA needs attention. Meet2Notes can still transcribe with CPU/int8. "
                "See docs/linux-cuda.md for troubleshooting."
            )
        return 0 if args.install_if_needed else code
    print(json.dumps(result, indent=2))
    return 0 if result["state"] in {"ready", "no_gpu"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
