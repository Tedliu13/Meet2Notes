"""Resident Sherpa process. Stdout is reserved for JSON, stderr retains native faults."""
from __future__ import annotations

import argparse
import faulthandler
import json
import logging
import os
import sys
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-dir", type=Path, required=True)
    args = parser.parse_args()
    # Preserve a protocol descriptor before redirecting even native stdout.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    faulthandler.enable(file=sys.stderr, all_threads=True)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    logging.info("Sherpa isolated worker started pid=%d", os.getpid())
    from .sherpa_onnx import SherpaOnnxDiarizationEngine

    engine = SherpaOnnxDiarizationEngine(args.models_dir)

    def report(progress: float, message: str) -> None:
        protocol.write(json.dumps({"progress": progress, "message": message}) + "\n")

    try:
        for line in sys.stdin:
            response: dict[str, Any]
            try:
                request = json.loads(line)
                config = request.get("config") or {}
                action = request.get("action")
                if action == "prepare":
                    engine._prepare_sync(config, False)
                    response = {"ok": True, "pid": os.getpid()}
                elif action == "diarize":
                    turns = engine._diarize_sync(
                        Path(request["audio_path"]), config, report, lambda: False,
                    )
                    response = {"ok": True, "pid": os.getpid(),
                                "segments": [asdict(turn) for turn in turns]}
                else:
                    raise ValueError("Unsupported Sherpa worker action")
            except Exception as error:
                traceback.print_exc(file=sys.stderr)
                response = {"ok": False, "error_type": type(error).__name__}
            protocol.write(json.dumps(response) + "\n")
    finally:
        engine.shutdown()
        protocol.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
