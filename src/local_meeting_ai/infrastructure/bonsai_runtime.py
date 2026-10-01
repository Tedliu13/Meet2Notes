"""Lazy, private llama-server client with the interface used by our summary adapter."""
from __future__ import annotations

import atexit
import json
import logging
import os
import secrets
import socket
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx

from local_meeting_ai.domain.errors import CapabilityUnavailableError
from local_meeting_ai.infrastructure.bonsai_assets import runtime_executable

logger = logging.getLogger(__name__)


class BonsaiMemoryError(CapabilityUnavailableError):
    """Native runtime explicitly reported a failed memory allocation."""


def host_cache_fits(context_length: int, *, bytes_per_token: int = 32768) -> bool:
    """Conservative headroom for a Q4 host cache plus recurrent state/buffers."""
    try:
        memory = __import__("psutil").virtual_memory()
        return bool(memory.available >= context_length * bytes_per_token + 2 * 1024**3)
    except (ImportError, AttributeError, OSError):
        return False


class BonsaiModel:
    def __init__(self, models_dir: Path, model_path: Path, config: dict[str, Any]) -> None:
        executable = runtime_executable(models_dir)
        if executable is None:
            raise CapabilityUnavailableError(
                "Install the Bonsai model/runtime from Settings first."
            )
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        token = secrets.token_urlsafe(32)
        self._client = httpx.Client(
            base_url=f"http://127.0.0.1:{port}", trust_env=False,
            headers={"Authorization": f"Bearer {token}"}, timeout=300,
        )
        self._log = tempfile.TemporaryFile()  # noqa: SIM115 - owned until close()/shutdown
        self._process: subprocess.Popen[bytes] | None = None
        environment = dict(os.environ)
        # Use exactly one card, matching the installer's per-card VRAM decision.
        environment["CUDA_VISIBLE_DEVICES"] = str(config.get("main_gpu", 0))
        environment["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        # Prism bundles Windows CUDA DLLs separately; make their directory discoverable.
        runtime_root = executable.parent
        while runtime_root != models_dir and not (runtime_root / ".verified").exists():
            runtime_root = runtime_root.parent
        library_dirs = {str(executable.parent)}
        library_dirs.update(str(path.parent) for path in runtime_root.rglob("*.so*"))
        environment["LD_LIBRARY_PATH"] = os.pathsep.join(sorted(library_dirs)) + (
            os.pathsep + environment["LD_LIBRARY_PATH"]
            if environment.get("LD_LIBRARY_PATH") else ""
        )
        dll_dirs = {str(path.parent) for path in runtime_root.rglob("*.dll")}
        environment["PATH"] = (
            os.pathsep.join(sorted(dll_dirs)) + os.pathsep + environment.get("PATH", "")
        )
        arguments = [
            str(executable), "-m", str(model_path), "--host", "127.0.0.1", "--port", str(port),
            "-c", str(config.get("context_length", 8192)), "-np", "1",
            "-ngl", str(config.get("gpu_layers", -1)), "--split-mode", "none",
            "-fa", "on" if config.get("flash_attention", True) else "off",
            "--no-webui",
            "-b", str(config.get("batch_size", 512)),
            "-ub", str(config.get("micro_batch_size", 128)),
            "-ctk", "q4_0", "-ctv", "q4_0",
            "--ctx-checkpoints", "8", "--checkpoint-min-step", "256",
            "--cache-ram", "0", "--no-context-shift",
        ]
        # Quantized V requires flash attention in the pinned Prism runtime.
        arguments[arguments.index("-fa") + 1] = "on"
        # Keep the credential out of process arguments and error messages.
        environment["LLAMA_API_KEY"] = token
        if int(config.get("threads", 0)) > 0:
            arguments.extend(["-t", str(config["threads"])])
        if int(config.get("batch_threads", 0)) > 0:
            arguments.extend(["-tb", str(config["batch_threads"])])
        if not config.get("use_mmap", True):
            arguments.append("--no-mmap")
        if config.get("use_mlock", False):
            arguments.append("--mlock")
        if not config.get("offload_kqv", True):
            arguments.append("--no-kv-offload")
        try:
            self._process = subprocess.Popen(
                arguments, cwd=executable.parent, env=environment,
                stdin=subprocess.DEVNULL, stdout=self._log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    self._log.seek(0, os.SEEK_END)
                    self._log.seek(max(0, self._log.tell() - 4000))
                    detail = self._log.read().decode("utf-8", errors="replace").replace(
                        token, "[redacted]",
                    )
                    logger.warning("Bonsai startup failed: %s", detail)
                    if any(marker in detail.lower() for marker in (
                        "out of memory", "failed to allocate", "unable to allocate",
                        "cudamalloc failed",
                    )):
                        raise BonsaiMemoryError(
                            "Bonsai could not allocate the requested context in GPU memory."
                        )
                    raise CapabilityUnavailableError(
                        "Bonsai could not start. Check the NVIDIA driver and free GPU memory; "
                        "reduce context/GPU layers in Settings or select LFM for CPU. "
                        "On Linux, check that the runtime supports your system libraries."
                    )
                try:
                    if self._client.get("/health", timeout=2).status_code == 200:
                        atexit.register(self.close)
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.25)
            raise CapabilityUnavailableError(
                "Bonsai model loading timed out. Try a smaller context."
            )
        except BaseException:
            self.close()
            raise

    def tokenize(self, text: bytes, **kwargs: Any) -> list[int]:
        response = self._client.post("/tokenize", json={
            "content": text.decode("utf-8"), "add_special": kwargs.get("add_bos", True),
        })
        response.raise_for_status()
        return list(response.json()["tokens"])

    def create_chat_completion(self, **kwargs: Any) -> Iterator[dict[str, Any]]:
        # A zero reasoning budget does not switch off the GGUF's thinking chat
        # template. Disable it per request so follow-up questions cannot spend
        # their entire output allowance in a hidden reasoning_content field.
        payload = {
            **kwargs,
            "cache_prompt": kwargs.get("cache_prompt", True),
            "stream_options": {"include_usage": True},
            "timings_per_token": True,
            "sse_ping_interval": 5,
            "return_progress": True,
            "chat_template_kwargs": {
                **(kwargs.get("chat_template_kwargs") or {}),
                "enable_thinking": False,
            },
            "reasoning_effort": "none",
        }
        try:
            with self._client.stream("POST", "/v1/chat/completions", json=payload) as response:
                if response.is_error:
                    response.read()
                    raise CapabilityUnavailableError(
                        f"Bonsai request failed (HTTP {response.status_code}). "
                        "Check the context size and available GPU memory in Settings."
                    )
                completed = False
                has_answer = False
                reasoning_chars = 0
                finish_reason = None
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    event_data = line[5:].strip()
                    if event_data == "[DONE]":
                        completed = True
                        break
                    chunk = json.loads(event_data)
                    timings = chunk.get("timings")
                    if timings and chunk.get("usage"):
                        logger.info(
                            "Bonsai KV cache: reused=%s evaluated=%s "
                            "prefill_ms=%s generation_ms=%s",
                            timings.get("cache_n"), timings.get("prompt_n"),
                            timings.get("prompt_ms"), timings.get("predicted_ms"),
                        )
                    if "error" in chunk:
                        raise CapabilityUnavailableError("Bonsai could not finish this response.")
                    for choice in chunk.get("choices", []):
                        delta = choice.get("delta") or {}
                        has_answer = has_answer or bool(str(delta.get("content") or "").strip())
                        reasoning_chars += len(
                            delta.get("reasoning_content") or delta.get("reasoning") or ""
                        )
                        finish_reason = choice.get("finish_reason") or finish_reason
                    yield chunk
                if not completed:
                    raise CapabilityUnavailableError(
                        "Bonsai disconnected before completing the response. Please retry."
                    )
                if not has_answer:
                    logger.warning(
                        "Bonsai returned no answer (finish_reason=%s, reasoning_chars=%d)",
                        finish_reason, reasoning_chars,
                    )
                    if reasoning_chars and finish_reason == "length":
                        raise CapabilityUnavailableError(
                            "Bonsai used its output limit for internal reasoning "
                            "without answering. "
                            "Reload the model to apply the non-thinking configuration and retry."
                        )
                    raise CapabilityUnavailableError(
                        "Bonsai finished without returning an answer. Please retry the question."
                    )
        except httpx.HTTPError as error:
            raise CapabilityUnavailableError(
                "Connection to the local Bonsai runtime failed. Reload the model from Settings."
            ) from error

    def is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def close(self) -> None:
        atexit.unregister(self.close)
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self._client.close()
        self._log.close()
