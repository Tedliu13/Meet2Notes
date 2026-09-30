"""Coordinate resident engines without unloading work in flight.

Opt-in through the automatic audio bundle. A lease spans the entire asynchronous
inference, including executor completion when the caller disconnects.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from local_meeting_ai.domain.errors import CapabilityUnavailableError
from local_meeting_ai.infrastructure.audio_hardware import available_vram_mib

logger = logging.getLogger(__name__)


class ModelMemory:
    def __init__(self, enabled: Callable[[], bool]) -> None:
        self.enabled = enabled
        self.condition = asyncio.Condition()
        self.exclusive = False
        self.state = threading.RLock()
        self.active: int = 0
        self.engines: dict[str, Any] = {}
        self.resident: set[str] = set()
        self.pending_unloads: set[str] = set()
        self.audio_model: str | None = None

    def wrap(self, name: str, engine: Any) -> Any:
        self.engines[name] = engine
        return ManagedEngine(name, engine, self)

    async def run(self, name: str, method: str, *args: Any, **kwargs: Any) -> Any:
        async with self.condition:
            automatic = self.enabled()
            await self.condition.wait_for(
                lambda: not self.exclusive and (not automatic or self.active == 0))
            with self.state:
                self.active += 1
                self.exclusive = automatic
        try:
            if automatic:
                for other in tuple(self.resident - {name}):
                    logger.info("Releasing idle %s model before %s", other, name)
                    await asyncio.to_thread(self.engines[other].unload)
                    self.resident.discard(other)
            cold = name not in self.resident
            self.resident.add(name)
            if automatic and method == "transcribe" and args and args[0].engine == "faster-whisper":
                request, progress, cancelled, segment_ready = args
                if request.device != "cpu" and (cold or request.model != self.audio_model):
                    free = await asyncio.to_thread(available_vram_mib, request.device_index)
                    # Conservative headroom, not a claim about exact peak usage.
                    minimum = 2560 if request.model in {"turbo", "large-v3"} else 1024
                    if free is not None and free < minimum:
                        progress(0.05, "Insufficient free GPU memory; using CPU INT8")
                        logger.warning("Only %s MiB VRAM free; using CPU INT8 for %s",
                                       free, request.model)
                        request = replace(request, device="cpu", compute_type="int8")
                self.audio_model = request.model
                emitted = False

                def emit(segment: Any) -> None:
                    nonlocal emitted
                    emitted = True
                    segment_ready(segment)

                try:
                    return await self.engines[name].transcribe(request, progress, cancelled, emit)
                except (RuntimeError, CapabilityUnavailableError) as error:
                    message = str(error).lower()
                    recoverable = any(part in message for part in (
                        "out of memory", "cuda", "cublas", "cudnn"))
                    if emitted or cancelled() or request.device == "cpu" or not recoverable:
                        raise
                    await asyncio.to_thread(self.engines[name].unload)
                    progress(0.05, "GPU unavailable or insufficient memory; retrying on CPU INT8")
                    logger.warning("Automatic transcription fallback to CPU INT8: %s", error)
                    return await self.engines[name].transcribe(
                        replace(request, device="cpu", compute_type="int8"),
                        progress, cancelled, segment_ready)
            return await getattr(self.engines[name], method)(*args, **kwargs)
        finally:
            pending = set()
            async with self.condition:
                with self.state:
                    self.active -= 1
                    if automatic:
                        self.exclusive = False
                    if not self.active and self.pending_unloads:
                        pending = set(self.pending_unloads)
                        self.pending_unloads.clear()
                        self.exclusive = True
                self.condition.notify_all()
            if pending:
                try:
                    for key in pending:
                        try:
                            await asyncio.to_thread(self.engines[key].unload)
                            self.resident.discard(key)
                        except Exception:
                            logger.exception("Could not release idle %s model", key)
                finally:
                    async with self.condition:
                        with self.state:
                            self.exclusive = False
                        self.condition.notify_all()



class ManagedEngine:
    def __init__(self, key: str, engine: Any, memory: ModelMemory) -> None:
        self.key, self.engine, self.memory = key, engine, memory
        self.name = engine.name

    def __getattr__(self, name: str) -> Any:
        return getattr(self.engine, name)

    async def _run(self, method: str, *args: Any, **kwargs: Any) -> Any:
        task = asyncio.create_task(self.memory.run(self.key, method, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Executor cancellation does not stop native inference. Retain the
            # lease until it actually finishes, so another engine cannot evict it.
            try:
                await asyncio.shield(task)
            finally:
                raise

    async def prepare(self, *args: Any, **kwargs: Any) -> Any:
        return await self._run("prepare", *args, **kwargs)

    async def transcribe(self, *args: Any, **kwargs: Any) -> Any:
        return await self._run("transcribe", *args, **kwargs)

    async def diarize(self, *args: Any, **kwargs: Any) -> Any:
        return await self._run("diarize", *args, **kwargs)

    async def summarize(self, *args: Any, **kwargs: Any) -> Any:
        return await self._run("summarize", *args, **kwargs)

    async def uninstall(self, *args: Any, **kwargs: Any) -> Any:
        return await self._run("uninstall", *args, **kwargs)

    def unload(self) -> None:
        with self.memory.state:
            if self.memory.active or self.memory.exclusive:
                self.memory.pending_unloads.add(self.key)
                logger.info("Deferring %s unload until active AI processing finishes", self.key)
                return
            self.engine.unload()
            self.memory.resident.discard(self.key)
