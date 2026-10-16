from __future__ import annotations

import asyncio
import os
import sys
import threading
import wave
from concurrent.futures import ThreadPoolExecutor

import pytest

from local_meeting_ai.adapters.diarization.sherpa_onnx import SherpaOnnxDiarizationEngine
from local_meeting_ai.domain.entities import DiarizationSegment
from local_meeting_ai.domain.errors import CapabilityUnavailableError, JobCancelledError
from local_meeting_ai.infrastructure.json_process import JsonProcessWorker

WORKER = """
import ctypes, json, os, sys
for line in sys.stdin:
    request = json.loads(line)
    if request.get('action') == 'crash':
        os._exit(23)
    print(json.dumps({'progress': 0.2, 'message': 'native work started'}), flush=True)
    if request.get('action') == 'hold-gil':
        if os.name == 'nt':
            ctypes.PyDLL('kernel32').Sleep(2500)
        else:
            ctypes.PyDLL(None).usleep(2500000)
    if request.get('action') == 'error':
        print(json.dumps({'ok': False, 'error_type': 'ValueError'}), flush=True)
    else:
        print(json.dumps({'ok': True, 'pid': os.getpid()}), flush=True)
"""


def process(tmp_path):
    return JsonProcessWorker([sys.executable, "-u", "-c", WORKER], tmp_path / "worker.log")


def test_resident_process_reuse_and_native_failure_are_isolated(tmp_path):
    worker = process(tmp_path)
    try:
        first = worker.request({})
        second = worker.request({})
        assert first["pid"] == second["pid"] != os.getpid()
        with pytest.raises(CapabilityUnavailableError, match="code=23"):
            worker.request({"action": "crash"})
        assert worker.pid is None
        # Only this explicit new request starts a new child, never a replay.
        assert worker.request({})["pid"] != first["pid"]
    finally:
        worker.stop()
    assert worker.pid is None


def test_cancellation_terminates_a_busy_native_child(tmp_path):
    worker = process(tmp_path)
    entered = threading.Event()
    try:
        with pytest.raises(JobCancelledError):
            worker.request(
                {"action": "hold-gil"}, progress=lambda *args: entered.set(),
                is_cancelled=entered.is_set,
            )
        assert entered.is_set()
        assert worker.pid is None
        assert worker.request({})["ok"] is True
    finally:
        worker.stop()


def test_worker_error_is_not_returned_as_success(tmp_path):
    worker = process(tmp_path)
    try:
        with pytest.raises(CapabilityUnavailableError, match="ValueError"):
            worker.request({"action": "error"})
        assert worker.pid is None
    finally:
        worker.stop()


def test_stopping_a_busy_child_unblocks_its_dispatcher(tmp_path):
    worker = process(tmp_path)
    entered = threading.Event()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            work = pool.submit(worker.request, {"action": "hold-gil"},
                               progress=lambda *args: entered.set())
            assert entered.wait(3)
            worker.stop()
            with pytest.raises(CapabilityUnavailableError):
                work.result(timeout=3)
        assert worker.pid is None
    finally:
        worker.stop()


def test_parent_event_loop_runs_while_child_holds_gil(tmp_path):
    worker = process(tmp_path)
    entered = threading.Event()

    async def scenario():
        task = asyncio.create_task(asyncio.to_thread(
            worker.request, {"action": "hold-gil"}, progress=lambda *args: entered.set(),
        ))
        assert await asyncio.to_thread(entered.wait, 3)
        # This coroutine and native child run concurrently in separate GILs.
        await asyncio.sleep(0.05)
        assert not task.done()
        assert (await task)["pid"] != os.getpid()

    try:
        asyncio.run(scenario())
    finally:
        worker.stop()


FAKE_SHERPA = """
from types import SimpleNamespace
from local_meeting_ai.adapters.diarization.sherpa_onnx import SherpaOnnxDiarizationEngine
class Model:
    sample_rate = 16000
    def process(self, samples, callback):
        callback(1, 1)
        return SimpleNamespace(sort_by_start_time=lambda: [
            SimpleNamespace(start=0.0, end=1.0, speaker=4)])
def load(self, config):
    assert config['num_threads'] == 2
    return Model()
SherpaOnnxDiarizationEngine._get_diarizer = load
from local_meeting_ai.adapters.diarization.sherpa_worker import main
raise SystemExit(main())
"""


def test_sherpa_protocol_preload_progress_result_and_thread_cap(tmp_path, monkeypatch):
    engine = SherpaOnnxDiarizationEngine(
        tmp_path / "models", isolate_process=True, logs_dir=tmp_path / "logs", cpu_thread_limit=2,
    )
    worker = engine._process_worker
    assert worker is not None
    worker.command = [sys.executable, "-u", "-c", FAKE_SHERPA,
                      "--models-dir", str(tmp_path / "models")]
    monkeypatch.setattr(
        engine, "_get_diarizer", lambda config: pytest.fail("Native code in parent"),
    )
    path = tmp_path / "meeting.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 16000)
    progress = []
    try:
        asyncio.run(engine.prepare({"num_threads": 16}, allow_model_download=False))
        pid = worker.pid
        turns = asyncio.run(engine.diarize(
            path, {"num_threads": 16},
            lambda value, message: progress.append(message), lambda: False,
        ))
        assert turns == [DiarizationSegment(0, 1000, 0)]
        assert worker.pid == pid != os.getpid()
        assert progress
        assert engine.capability()["worker"]["process_isolated"] is True
        assert engine.capability()["worker"]["model_resident"] is True
    finally:
        engine.shutdown()
    assert worker.pid is None
    assert "Sherpa isolated worker started" in worker.log_path.read_text(encoding="utf-8")


def test_http_liveness_responds_during_native_child_work(tmp_path, client):
    worker = process(tmp_path)
    entered = threading.Event()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            work = pool.submit(worker.request, {"action": "hold-gil"},
                               progress=lambda *args: entered.set())
            assert entered.wait(3)
            assert client.get("/api/health/live").status_code == 200
            assert not work.done()
            assert work.result(timeout=5)["ok"] is True
    finally:
        worker.stop()
