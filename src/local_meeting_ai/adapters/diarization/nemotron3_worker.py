"""Private-process Nemotron inference. Stdout is reserved for JSON responses."""
from __future__ import annotations

import contextlib
import importlib
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload), file=sys.__stdout__, flush=True)


def audio_chunks(processor: Any, total_samples: int) -> Iterator[tuple[int, int, bool, bool]]:
    """Use the official processor's overlapping analysis windows, including the tail."""
    frame = 0
    first = True
    while True:
        start = 0 if first else processor.audio_chunk_start(frame)
        size = (processor.num_samples_first_audio_chunk if first
                else processor.num_samples_per_audio_chunk)
        end = min(total_samples, start + size)
        last = end >= total_samples
        if end > start:
            yield start, end - start, first, last
        if last:
            break
        frame += processor.num_mel_frames_per_step
        first = False


def clip_segments(raw: list[dict[str, Any]], duration_ms: int) -> list[dict[str, int]]:
    """Discard padding past EOF without merging overlapping speakers."""
    segments = []
    for item in raw:
        start = max(0, round(float(item["Start"]) * 1000))
        end = min(duration_ms, round(float(item["End"]) * 1000))
        if end > start:
            segments.append({"start_ms": start, "end_ms": end, "speaker": int(item["Speaker"])})
    return sorted(segments, key=lambda item: (item["start_ms"], item["speaker"]))


class Session:
    def __init__(self, model_dir: Path) -> None:
        self.model_dir = model_dir
        self.model: Any = None
        self.processor: Any = None
        self.device = ""

    def load(self, config: dict[str, Any]) -> None:
        torch = importlib.import_module("torch")
        transformers = importlib.import_module("transformers")

        device = str(config.get("provider", "cpu"))
        if device not in {"cpu", "cuda"}:
            raise ValueError("Nemotron 3 supports CPU or CUDA. Select one in Settings.")
        if device == "cuda" and not torch.cuda.is_available():
            raise ValueError(
                "CUDA is unavailable in the Nemotron runtime. Select CPU in Settings, "
                "or install a CUDA-compatible PyTorch runtime and NVIDIA driver."
            )
        torch.set_num_threads(max(1, min(64, int(config.get("num_threads", 2)))))
        if self.model is not None and self.device == device:
            return
        self.model = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        self.processor = transformers.AutoProcessor.from_pretrained(
            self.model_dir, local_files_only=True,
        )
        self.model = transformers.AutoModelForAudioFrameClassification.from_pretrained(
            self.model_dir, local_files_only=True,
        ).to(device).eval()
        # Use the model's documented offline window while preserving the same
        # speaker cache across calls. Neither audio nor features accumulate in VRAM.
        self.processor.streaming_modes["meeting"] = (
            self.model.config.chunk_length, self.model.config.chunk_right_context,
        )
        self.processor.set_streaming_mode("meeting")
        self.model.config.streaming_config.fifo_length = self.model.config.fifo_length
        self.model.config.streaming_config.speaker_cache_update_period = (
            self.model.config.speaker_cache_update_period
        )
        self.device = device

    def diarize(self, path: Path, config: dict[str, Any]) -> list[dict[str, int]]:
        sf = importlib.import_module("soundfile")
        torch = importlib.import_module("torch")

        self.load(config)
        # This cache belongs to ONE recording. It must never carry voices from
        # the preceding meeting, even when the model remains resident.
        cache = None
        scores = []
        with sf.SoundFile(path) as audio, torch.inference_mode():
            if audio.samplerate != 16000 or audio.channels != 1:
                raise ValueError("Nemotron requires normalized 16 kHz mono audio.")
            total = len(audio)
            if total == 0:
                return []
            for start, size, first, last in audio_chunks(self.processor, total):
                audio.seek(start)
                chunk = audio.read(size, dtype="float32")
                inputs = self.processor(
                    chunk, sampling_rate=16000, is_streaming=True,
                    is_first_audio_chunk=first, is_last_audio_chunk=last,
                ).to(self.device)
                output = self.model(**inputs, speaker_cache=cache)
                cache = output.speaker_cache
                scores.append(output.logits.cpu())
                processed = min(total, start + size)
                emit({
                    "progress": 0.08 + 0.88 * processed / total,
                    "message": (f"Nemotron 3 · {processed / 16000:.0f}s / "
                                f"{total / 16000:.0f}s audio analyzed"),
                })
            raw = self.processor.extract_speaker_dict(torch.cat(scores, dim=1))[0]
            return clip_segments(raw, round(total / 16))


def main() -> int:
    # Third-party download/loading diagnostics must not corrupt the wire protocol.
    with contextlib.redirect_stdout(sys.stderr):
        if sys.argv[1] == "--download":
            hub = importlib.import_module("huggingface_hub")
            transformers = importlib.import_module("transformers")
            # Verify that the private runtime implements the new architecture.
            if not hasattr(transformers, "Nemotron3DiarizationForAudioFrameClassification"):
                raise RuntimeError("This Transformers runtime does not support Nemotron 3.")
            hub.snapshot_download(
                "nvidia/Nemotron-3-Diarization", revision=sys.argv[3],
                local_dir=sys.argv[2], allow_patterns=["*.json", "model.safetensors"],
            )
            return 0
        session = Session(Path(sys.argv[1]))
        for line in sys.stdin:
            try:
                request = json.loads(line)
                config = request.get("config", {})
                if request["action"] == "load":
                    session.load(config)
                    emit({"ok": True})
                elif request["action"] == "diarize":
                    segments = session.diarize(Path(request["audio_path"]), config)
                    emit({"ok": True, "segments": segments})
                else:
                    raise ValueError("Unsupported Nemotron request")
            except Exception as error:
                emit({"ok": False, "error": f"{type(error).__name__}: {error}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
