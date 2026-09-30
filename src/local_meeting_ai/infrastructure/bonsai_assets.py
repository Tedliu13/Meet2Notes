"""Pinned Bonsai GGUFs and Prism runtime. Installation only downloads/verifies files."""
from __future__ import annotations

import hashlib
import logging
import platform
import shutil
import tarfile
import tempfile
import threading
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from local_meeting_ai.domain.errors import CapabilityUnavailableError

logger = logging.getLogger(__name__)
RELEASE = "prism-b10743-adfffbe"
BASE_URL = f"https://github.com/PrismML-Eng/llama.cpp/releases/download/{RELEASE}"
PROFILES: dict[str, dict[str, Any]] = {
    "bonsai-27b-1bit": {
        "id": "bonsai-27b-1bit", "display_name": "Bonsai 27B · 1-bit",
        "description": "For compatible NVIDIA GPUs with 8+ GB VRAM. Managed Prism runtime.",
        "repository": "prism-ml/Bonsai-27B-gguf",
        "revision": "f10afb355f104535e3e3e98cf7ab7795c72bd292",
        "model_file": "Bonsai-27B-Q1_0.gguf", "download_size": "3.80 GB + runtime",
        "size": 3803452480,
        "sha256": "17ef842e47450caeb8eaa3ebfbbab5d2f2278b62b79be107985fb69a2f819aa0",
        "quantization": "Q1_0", "context_length": 8192, "preload_on_start": False,
        "native_runtime": True,
    },
    "bonsai-27b-ternary": {
        "id": "bonsai-27b-ternary", "display_name": "Bonsai 27B · Ternary",
        "description": "For compatible NVIDIA GPUs with 16+ GB VRAM. Managed Prism runtime.",
        "repository": "prism-ml/Ternary-Bonsai-27B-gguf",
        "revision": "86e89f34c93201c3dfd5e5880fedb0022fc7e34d",
        "model_file": "Ternary-Bonsai-27B-PQ2_0.gguf", "download_size": "7.17 GB + runtime",
        "size": 7165121600,
        "sha256": "e4781999f1997ef97ce0c58d05750835acc999d18d83ee6489ba7ac7b14cb5f6",
        "quantization": "PQ2_0", "context_length": 16384, "preload_on_start": False,
        "native_runtime": True,
    },
}
# filename, bytes, GitHub release SHA-256
ASSETS = {
    "Windows": [
        (f"llama-{RELEASE}-bin-win-cuda-12.4-x64.zip", 257322810,
         "1b849f713bee42fda258de83770cd422e8f48dd631ce370eb0641f6458c69d87"),
        ("cudart-llama-bin-win-cuda-12.4-x64.zip", 391443627,
         "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6"),
    ],
    "Linux": [
        (f"llama-{RELEASE}-bin-linux-cuda-12.4-x64.tar.gz", 268256336,
         "fef4c7e8d83ff261d89809c1b302fe5f826adc1a74e13654b67ec056fbc1c639"),
    ],
    "Darwin": [
        (f"llama-{RELEASE}-bin-macos-arm64.tar.gz", 11517506,
         "596d257973080ca5011a4be50477c5f93ed1d231fcccfc2afb44bb573bb9629a"),
    ],
}
_install_lock = threading.Lock()
# NVIDIA's wheels are extracted privately, without changing Python or system CUDA.
LINUX_CUDA = (
    ("https://files.pythonhosted.org/packages/ae/71/"
     "1c91302526c45ab494c23f61c7a84aa568b8c1f9d196efa5993957faf906/"
     "nvidia_cublas_cu12-12.4.5.8-py3-none-manylinux2014_x86_64.whl",
     363438805, "2fc8da60df463fdefa81e323eef2e36489e1c94335b5358bcb38360adf75ac9b"),
    ("https://files.pythonhosted.org/packages/ea/27/"
     "1795d86fe88ef397885f2e580ac37628ed058a92ed2c39dc8eac3adf0619/"
     "nvidia_cuda_runtime_cu12-12.4.127-py3-none-manylinux2014_x86_64.whl",
     883737, "64403288fa2136ee8e467cdc9c9427e0434110899d07c779f25b5c068934faa5"),
)


def supported_platform() -> bool:
    system, machine = platform.system(), platform.machine().lower()
    return (system in {"Windows", "Linux"} and machine in {"amd64", "x86_64"}) or (
        system == "Darwin" and machine in {"arm64", "aarch64"}
    )


def runtime_directory(models_dir: Path) -> Path:
    return models_dir / "runtimes" / f"{RELEASE}-{platform.system()}-{platform.machine().lower()}"


def runtime_executable(models_dir: Path) -> Path | None:
    root = runtime_directory(models_dir)
    marker = root / ".verified"
    if not marker.is_file() or marker.read_text(encoding="utf-8") != RELEASE:
        return None
    name = "llama-server.exe" if platform.system() == "Windows" else "llama-server"
    return next(root.rglob(name), None)


def verify_file(path: Path, size: int, sha256: str) -> bool:
    if not path.is_file() or path.stat().st_size != size:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == sha256


def download_file(url: str, destination: Path, size: int, sha256: str) -> None:
    if verify_file(destination, size, sha256):
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(destination.parent).free < size + 256 * 1024**2:
        raise CapabilityUnavailableError(f"Not enough disk space to download {destination.name}.")
    logger.info("Downloading %s (%.2f GB)", destination.name, size / 1e9)
    # Unique partial files keep failed or simultaneous downloads away from usable files.
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, suffix=".part", delete=False,
    ) as output:
        partial = Path(output.name)
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
                response.raise_for_status()
                count = 0
                for chunk in response.iter_bytes(4 * 1024 * 1024):
                    count += len(chunk)
                    if count > size:
                        raise CapabilityUnavailableError("Download exceeds its pinned size.")
                    output.write(chunk)
            output.close()
            if not verify_file(partial, size, sha256):
                raise CapabilityUnavailableError(f"Integrity check failed for {destination.name}.")
            partial.replace(destination)
        finally:
            output.close()
            partial.unlink(missing_ok=True)


def _safe_target(root: Path, name: str) -> Path:
    relative = PurePosixPath(name.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts or ":" in name:
        raise CapabilityUnavailableError("Unsafe path in runtime archive.")
    target = root.joinpath(*relative.parts).resolve()
    if not target.is_relative_to(root.resolve()):
        raise CapabilityUnavailableError("Runtime archive escapes the installation directory.")
    return target


def extract_archive(archive: Path, root: Path) -> None:
    """Extract only files/directories; safely materialize internal tar links as copies."""
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zipped:
            for item in zipped.infolist():
                target = _safe_target(root, item.filename)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zipped.open(item) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
        return
    with tarfile.open(archive) as tar:
        links = []
        for member in tar.getmembers():
            target = _safe_target(root, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                tar_source = tar.extractfile(member)
                if tar_source is None:
                    raise CapabilityUnavailableError("Missing runtime archive member.")
                with tar_source, target.open("wb") as output:
                    shutil.copyfileobj(tar_source, output)
                target.chmod(member.mode & 0o755)
            elif member.issym() or member.islnk():
                link_name = str(PurePosixPath(member.name).parent / member.linkname) if (
                    member.issym()
                ) else member.linkname
                links.append((target, _safe_target(root, link_name)))
            else:
                raise CapabilityUnavailableError("Unsupported runtime archive member.")
        while links:
            pending = []
            for target, link_source in links:
                if link_source.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(link_source, target)
                else:
                    pending.append((target, link_source))
            if len(pending) == len(links):
                raise CapabilityUnavailableError("Invalid runtime archive links.")
            links = pending


def install_runtime(models_dir: Path) -> None:
    if not supported_platform():
        raise CapabilityUnavailableError("Bonsai native runtime is unavailable for this platform.")
    if runtime_executable(models_dir):
        return
    target = runtime_directory(models_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    assets = [(f"{BASE_URL}/{name}", size, digest)
              for name, size, digest in ASSETS[platform.system()]]
    if platform.system() == "Linux":
        assets.extend(LINUX_CUDA)
    if shutil.disk_usage(target.parent).free < sum(item[1] for item in assets) * 4:
        raise CapabilityUnavailableError("Not enough disk space to unpack the Bonsai runtime.")
    with tempfile.TemporaryDirectory(dir=target.parent, prefix="prism-install-") as temporary:
        staging = Path(temporary) / "runtime"
        staging.mkdir()
        for url, size, digest in assets:
            filename = url.rsplit("/", 1)[-1]
            archive = Path(temporary) / filename
            download_file(url, archive, size, digest)
            extract_archive(archive, staging)
        executable = "llama-server.exe" if platform.system() == "Windows" else "llama-server"
        if not list(staging.rglob(executable)):
            raise CapabilityUnavailableError("The runtime archive has no llama-server executable.")
        (staging / ".verified").write_text(RELEASE, encoding="utf-8")
        # A published runtime is immutable; interrupted installations remain in staging only.
        if target.exists():
            if runtime_executable(models_dir):
                return
            raise CapabilityUnavailableError(
                f"Incomplete runtime at {target}; remove it and retry."
            )
        staging.rename(target)


def install_profile(models_dir: Path, profile_id: str) -> Path:
    profile = PROFILES[profile_id]
    with _install_lock:
        install_runtime(models_dir)
        path = models_dir / str(profile["model_file"])
        url = (f"https://huggingface.co/{profile['repository']}/resolve/"
               f"{profile['revision']}/{profile['model_file']}")
        download_file(url, path, profile["size"], profile["sha256"])
        return path
