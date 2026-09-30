import io
import subprocess
import sys
from unittest.mock import Mock

import pytest

from local_meeting_ai.adapters.diarization import diarize_cpu
from local_meeting_ai.domain.errors import JobCancelledError


def test_long_analysis_reports_elapsed_time_without_fake_percentage(tmp_path, monkeypatch):
    engine = diarize_cpu.DiarizeCpuEngine(tmp_path)
    engine._process = Mock(stdin=io.StringIO(), stdout=io.StringIO(
        'Library diagnostic\n{"ok": true, "segments": []}\n'))
    tick = iter([0, 0, 16])
    monkeypatch.setattr(diarize_cpu.time, "monotonic", lambda: next(tick))
    progress = Mock()
    try:
        assert engine._request_worker({"action": "diarize"}, progress)["ok"]
        assert progress.call_args.args[0] == 0.05
        assert "16s elapsed" in progress.call_args.args[1]
        assert "percentage unavailable" in progress.call_args.args[1]
    finally:
        engine._process = None
        engine.shutdown()


def test_cancellation_interrupts_a_worker_that_has_not_returned_output(tmp_path):
    engine = diarize_cpu.DiarizeCpuEngine(tmp_path)
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys,time; sys.stdin.readline(); time.sleep(60)"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    engine._process = process
    try:
        with pytest.raises(JobCancelledError):
            engine._request_worker({"action": "diarize"}, is_cancelled=lambda: True)
        assert process.poll() is not None
    finally:
        engine.shutdown()
