"""Pinokio bootstrap: select a Torch wheel without importing/loading a model."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from local_meeting_ai.infrastructure.summary_hardware import detect_hardware


def main() -> None:
    hardware = detect_hardware()
    cuda = hardware.system in {"Windows", "Linux"} and any(
        7.5 <= gpu.compute <= 9.0 and gpu.driver_major >= 560 for gpu in hardware.gpus)
    channel = "cu126" if cuda else "cpu"
    # macOS uses the standard wheel (CPU/MPS), not the Windows/Linux +cpu index.
    command = [sys.executable, "-m", "pip", "install", "--upgrade", "--progress-bar", "off"]
    if hardware.system == "Darwin":
        command += ["torch==2.13.0"]
    else:
        command += [f"torch==2.13.0+{channel}", "--index-url",
                    f"https://download.pytorch.org/whl/{channel}"]
    print(f"Installing PyTorch backend: {channel}", flush=True)
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
