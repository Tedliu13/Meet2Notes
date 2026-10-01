"""Nemotron 3 in a private Transformers runtime; never upgrade the app's packages."""
from __future__ import annotations

import json
import logging
import os
import site
import subprocess
import venv
from pathlib import Path
from typing import Any

from local_meeting_ai.adapters.diarization.diarize_cpu import DiarizeCpuEngine
from local_meeting_ai.domain.entities import DiarizationSegment
from local_meeting_ai.domain.errors import CapabilityUnavailableError, JobCancelledError
from local_meeting_ai.domain.protocols import CancellationCheck, ProgressReporter

logger = logging.getLogger(__name__)
MODEL_ID = "nvidia/Nemotron-3-Diarization"
MODEL_REVISION = "f667ed73aee57d40cc39428eb768b4fd87a0a29e"
# Stable Transformers 5.17 does not yet implement this architecture.
TRANSFORMERS_REVISION = "d6c1e71bd717bf092f8293f0c3c9bd4a5ac5401a"


class Nemotron3DiarizationEngine(DiarizeCpuEngine):
    """Reuse the isolated diarizer's serialized, cancellable JSON transport."""

    name = "nvidia-nemotron-3-diarization"

    def __init__(self, models_dir: Path) -> None:
        super().__init__(models_dir)
        self.runtime_dir = models_dir / "runtimes" / self.name
        self.cache_dir = models_dir / "diarization" / self.name

    def capability(self) -> dict[str, Any]:
        result = super().capability()
        result.update({
            "display_name": "NVIDIA Nemotron 3 Diarization",
            "repository": MODEL_ID,
            "supported_providers": ["cpu", "cuda"],
            "max_speakers": 8,
            "supports_speaker_count": False,
            "download_size": "~400 MB model + private runtime dependencies",
            "compatibility_note": (
                "Up to 8 speakers, detected automatically. CPU or NVIDIA CUDA. "
                "Pinned preview runtime, isolated from other engines."
            ),
            "install_command": "meet2notes-models --models nvidia-nemotron-3-diarization",
        })
        result["worker"]["thread_prefix"] = self.name
        return result

    def _installed(self) -> bool:
        try:
            marker = json.loads(self._marker_path().read_text(encoding="utf-8"))
            return (
                self._runtime_python().is_file()
                and (self.cache_dir / "model.safetensors").is_file()
                and marker == [TRANSFORMERS_REVISION, MODEL_REVISION]
            )
        except (OSError, ValueError):
            return False

    def _prepare_sync(self, config: dict[str, Any], allow_model_download: bool) -> None:
        self._request_started("loading")
        failure = None
        try:
            if allow_model_download and not self._installed():
                self._install_runtime()
            if not self._installed():
                raise CapabilityUnavailableError(
                    "Install NVIDIA Nemotron 3 Diarization in Settings."
                )
            # Installation downloads only. Explicit Load / startup preloading loads the model.
            if not allow_model_download:
                self._ensure_worker()
                response = self._request_worker({"action": "load", "config": config})
                self._check_response(response)
        except Exception as error:
            failure = error
            self.unload()
            raise
        finally:
            self._request_finished(failure)
            if failure is None and not self._worker_running():
                self._set_state("idle")

    def _diarize_sync(
        self, audio_path: Path, config: dict[str, Any],
        progress: ProgressReporter, is_cancelled: CancellationCheck,
    ) -> list[DiarizationSegment]:
        self._request_started("inferencing")
        failure = None
        try:
            if is_cancelled():
                raise JobCancelledError("Diarization was cancelled")
            if int(config.get("num_speakers", -1)) > 8:
                raise CapabilityUnavailableError(
                    "Nemotron 3 supports at most 8 speakers. Select another diarization engine "
                    "for meetings with more speakers."
                )
            self._ensure_worker()
            self._set_state("inferencing")
            progress(0.05, "Loading Nemotron 3 speaker analysis")
            response = self._request_worker(
                {"action": "diarize", "audio_path": str(audio_path), "config": config},
                progress, is_cancelled,
            )
            self._check_response(response)
            if is_cancelled():
                raise JobCancelledError("Diarization was cancelled")
            turns = [DiarizationSegment(**item) for item in response["segments"]]
            progress(0.98, f"Nemotron 3 detected {len({turn.speaker for turn in turns})} speakers")
            return turns
        except Exception as error:
            failure = error
            self.unload()
            raise
        finally:
            if not config.get("keep_model_loaded", True):
                self.unload()
            self._request_finished(failure)
            if failure is None and not self._worker_running():
                self._set_state("idle")

    @staticmethod
    def _check_response(response: dict[str, Any]) -> None:
        if not response.get("ok"):
            raise CapabilityUnavailableError(str(response.get("error", "Nemotron 3 failed")))

    def _install_runtime(self) -> None:
        self.unload()
        logger.info("Creating private Nemotron 3 runtime in %s", self.runtime_dir)
        venv.EnvBuilder(with_pip=True).create(self.runtime_dir)
        python = self._runtime_python()
        # Reuse compatible heavy packages (especially CUDA Torch) read-only. Pip can
        # shadow them inside this venv but cannot uninstall packages outside it.
        probe = subprocess.run(
            [str(python), "-c", "import site; print(site.getsitepackages()[-1])"],
            capture_output=True, text=True, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        private_site = Path(probe.stdout.strip())
        private_site.mkdir(parents=True, exist_ok=True)
        (private_site / "meet2notes-host.pth").write_text(
            "\n".join(site.getsitepackages()) + "\n", encoding="utf-8",
        )
        self._run_install([
            str(python), "-m", "pip", "install", "--disable-pip-version-check",
            f"https://github.com/huggingface/transformers/archive/{TRANSFORMERS_REVISION}.zip",
            "torch>=2.6,<3", "numpy>=1.26,<2.4", "soundfile>=0.12,<1", "scipy>=1.12,<2",
        ])
        self._run_install([
            str(python), "-u", str(Path(__file__).with_name("nemotron3_worker.py")),
            "--download", str(self.cache_dir), MODEL_REVISION,
        ])
        self._marker_path().write_text(
            json.dumps([TRANSFORMERS_REVISION, MODEL_REVISION]), encoding="utf-8",
        )

    def _run_install(self, command: list[str]) -> None:
        environment = dict(os.environ, PYTHONUTF8="1", PIP_PROGRESS_BAR="off")
        with subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ) as process:
            assert process.stdout is not None
            for line in process.stdout:
                if line.strip():
                    logger.info("Nemotron 3 install | %s", line.rstrip())
            return_code = process.wait()
        if return_code:
            raise CapabilityUnavailableError(
                "Nemotron 3 installation failed. See the installation log; retry Install."
            )

    def _ensure_worker(self) -> None:
        if self._worker_running():
            return
        if not self._installed():
            raise CapabilityUnavailableError("Install NVIDIA Nemotron 3 Diarization in Settings.")
        self._process = subprocess.Popen(
            [str(self._runtime_python()), "-u",
             str(Path(__file__).with_name("nemotron3_worker.py")), str(self.cache_dir)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env=dict(os.environ, PYTHONUTF8="1", HF_HUB_OFFLINE="1"),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
