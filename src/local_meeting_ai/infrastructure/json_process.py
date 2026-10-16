"""Serialized JSON requests to a resident, independently cancellable Python worker."""
from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from local_meeting_ai.domain.errors import CapabilityUnavailableError, JobCancelledError
from local_meeting_ai.domain.protocols import CancellationCheck, ProgressReporter

logger = logging.getLogger(__name__)


class JsonProcessWorker:
    """A caller's dispatcher serializes requests; this class never replays one."""

    def __init__(self, command: list[str], log_path: Path) -> None:
        self.command = command
        self.log_path = log_path
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.RLock()

    @property
    def pid(self) -> int | None:
        with self._lock:
            process = self._process
        return process.pid if process is not None and process.poll() is None else None

    def stop(self) -> None:
        with self._lock:
            process, self._process = self._process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        for pipe in (process.stdin, process.stdout):
            if pipe is not None:
                pipe.close()

    def _start(self) -> subprocess.Popen[str]:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return self._process
            self.stop()
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if self.log_path.exists() and self.log_path.stat().st_size >= 5 * 1024 * 1024:
                self.log_path.replace(self.log_path.with_suffix(".log.1"))
            with self.log_path.open("ab") as output:
                self._process = subprocess.Popen(
                    self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=output,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    env={**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"},
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            logger.info("Isolated model worker started: pid=%d log=%s",
                        self._process.pid, self.log_path)
            return self._process

    def request(
        self, payload: dict[str, Any], *, progress: ProgressReporter | None = None,
        is_cancelled: CancellationCheck = lambda: False,
    ) -> dict[str, Any]:
        if is_cancelled():
            raise JobCancelledError("Model work was cancelled")
        process = self._start()
        assert process.stdin is not None and process.stdout is not None
        events: queue.Queue[dict[str, Any] | None] = queue.Queue()

        def read() -> None:
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and ("progress" in event or "ok" in event):
                        events.put(event)
                        if "ok" in event:
                            return
            except (OSError, ValueError):
                pass  # The owner may close the pipe while cancelling the worker.
            finally:
                events.put(None)

        reader = threading.Thread(target=read, name="isolated-model-response", daemon=True)
        reader.start()
        started = last_notice = time.monotonic()
        last_progress = 0.0
        completed = False
        try:
            process.stdin.write(json.dumps(payload) + "\n")
            process.stdin.flush()
            while time.monotonic() - started < 24 * 60 * 60:
                if is_cancelled():
                    raise JobCancelledError("Model work was cancelled")
                try:
                    event = events.get(timeout=0.2)
                except queue.Empty:
                    if progress is not None and time.monotonic() - last_notice >= 15:
                        elapsed = int(time.monotonic() - started)
                        progress(
                            last_progress,
                            f"Isolated speaker analysis running · {elapsed}s elapsed",
                        )
                        last_notice = time.monotonic()
                    continue
                if event is None:
                    try:
                        code = process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        code = None
                    raise CapabilityUnavailableError(
                        f"The isolated speaker worker exited (code={code}). "
                        f"See {self.log_path}; the request was not replayed."
                    )
                if "ok" in event:
                    if not event["ok"]:
                        raise CapabilityUnavailableError(
                            f"Isolated speaker work failed ({event.get('error_type', 'Error')}). "
                            f"See {self.log_path}; the request was not replayed."
                        )
                    completed = True
                    return event
                last_progress = max(last_progress, min(0.98, float(event["progress"])))
                if progress is not None:
                    progress(last_progress, str(event.get("message", "Analyzing speakers")))
                last_notice = time.monotonic()
            raise CapabilityUnavailableError("The isolated speaker worker timed out after 24 hours")
        finally:
            if not completed:
                self.stop()
            reader.join(timeout=2)
