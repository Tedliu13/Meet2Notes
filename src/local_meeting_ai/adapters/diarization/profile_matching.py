"""Reusable matching of saved Speaker WAV profiles to diarization turns.

This component deliberately has no dependency on the selected diarization
engine.  Sherpa-ONNX supplies the compact local embedding model, while Sherpa,
Pyannote and diarize may each produce the turns to be matched.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import importlib.util
import logging
import threading
import time
import wave
from array import array
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

from local_meeting_ai.domain.entities import DiarizationSegment
from local_meeting_ai.domain.errors import CapabilityUnavailableError, JobCancelledError
from local_meeting_ai.domain.protocols import CancellationCheck, ProgressReporter
from local_meeting_ai.infrastructure.voiceprint_cache import (
    VoiceprintCache,
    file_identity,
    signature,
)

from .sherpa_onnx import EMBEDDING_FILES, EMBEDDING_URLS, _configure_cuda_dlls, _download
from .voice_sampling import (
    INITIAL_FRAGMENTS,
    centroid,
    exclusive_ranges,
    identify,
    sample_ranges,
    unit_vector,
)

logger = logging.getLogger(__name__)


class SherpaOnnxSpeakerProfileMatcher:
    """Dedicated voiceprint matcher shared by every local diarization engine."""

    name = "sherpa-onnx-speaker-profiles"

    def __init__(self, models_dir: Path) -> None:
        # Keep using the original Sherpa model location so existing installs do
        # not redownload the 3D-Speaker embedding checkpoint.
        self.models_dir = models_dir / "diarization" / "sherpa-onnx"
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="speaker-profile-matcher",
        )
        self._state_lock = threading.Lock()
        self._state = "idle"
        self._active_requests = 0
        self._last_error: str | None = None
        self._shutdown = False
        self._last_match_stats: dict[str, Any] = {}

    def capability(self) -> dict[str, Any]:
        dependency = importlib.util.find_spec("sherpa_onnx") is not None
        with self._state_lock:
            state = self._state
            active = self._active_requests
            error = self._last_error
        return {
            "last_match": dict(self._last_match_stats),
            "engine": self.name,
            "display_name": "Saved speaker profile matcher",
            "available": dependency,
            "installed": self._model_path({}).is_file(),
            "models_directory": str(self.models_dir),
            "worker": {
                "dedicated": True,
                "thread_prefix": "speaker-profile-matcher",
                "dispatcher_threads": 1,
                "state": state,
                "active_requests": active,
                "model_resident": False,
                "last_error": error,
            },
        }

    async def prepare(
        self,
        config: dict[str, Any],
        *,
        allow_model_download: bool,
    ) -> None:
        await self._submit(self._prepare_sync, config, allow_model_download)

    async def match(
        self,
        audio_path: Path,
        turns: list[DiarizationSegment],
        profiles: list[Any],
        config: dict[str, Any],
        *,
        progress: ProgressReporter | None = None,
        is_cancelled: CancellationCheck | None = None,
    ) -> dict[int, Any]:
        return cast(
            dict[int, Any],
            await self._submit(
                self._match_sync, audio_path, turns, profiles, config, progress, is_cancelled,
            ),
        )

    def unload(self) -> None:
        # Extractors are created for one matching pass and released immediately.
        with self._state_lock:
            if not self._active_requests and not self._shutdown:
                self._state = "idle"

    def shutdown(self) -> None:
        with self._state_lock:
            if self._shutdown:
                return
            self._shutdown = True
            self._state = "stopping"
        self._executor.shutdown(wait=True, cancel_futures=True)
        with self._state_lock:
            self._state = "stopped"

    async def _submit(self, function: Any, *args: Any) -> Any:
        with self._state_lock:
            if self._shutdown:
                raise CapabilityUnavailableError("The saved speaker matcher is shutting down")
        return await asyncio.wrap_future(self._executor.submit(function, *args))

    def _prepare_sync(self, config: dict[str, Any], allow_model_download: bool) -> None:
        self._request_started("loading")
        failure: Exception | None = None
        try:
            if importlib.util.find_spec("sherpa_onnx") is None:
                raise CapabilityUnavailableError(
                    'sherpa-onnx is required for saved speaker matching. Run: '
                    'python -m pip install -e ".[diarization]"'
                )
            model = self._model_path(config)
            if allow_model_download and not model.is_file():
                logger.info("Downloading saved-speaker embedding model %s", model.name)
                _download(
                    EMBEDDING_URLS[self._embedding_name(config)],
                    model,
                )
            if not model.is_file():
                raise CapabilityUnavailableError(
                    "The saved-speaker embedding model is not installed. "
                    "Install the selected diarization engine in Settings."
                )
        except Exception as error:
            failure = error
            raise
        finally:
            self._request_finished(failure)

    def _match_sync(
        self, audio_path: Path, turns: list[DiarizationSegment], profiles: list[Any],
        config: dict[str, Any], progress: ProgressReporter | None = None,
        is_cancelled: CancellationCheck | None = None,
    ) -> dict[int, Any]:
        if not profiles or not turns:
            return {}
        self._request_started("matching voices")
        failure: Exception | None = None
        started = time.monotonic()
        stats: dict[str, Any] = {
            "cache_hits": 0, "computed_fragments": 0, "audio_seconds": 0.0,
            "model_load_seconds": 0.0, "comparison_seconds": 0.0,
            "saved_profiles_seconds": 0.0, "meeting_speakers_seconds": 0.0,
        }
        extractor: Any = None

        def check_cancelled() -> None:
            if self._shutdown or (is_cancelled is not None and is_cancelled()):
                raise JobCancelledError("Saved voice matching was cancelled")

        def report(value: float, message: str) -> None:
            check_cancelled()
            if progress is not None:
                progress(value, message)

        def get_extractor() -> Any:
            nonlocal extractor
            if extractor is None:
                before = time.monotonic()
                extractor = self._create_extractor(config)
                stats["model_load_seconds"] += time.monotonic() - before
            return extractor

        def vectors_for(
            audio: wave.Wave_read, cache: VoiceprintCache, key: str,
            ranges: list[tuple[int, int]], notice: Any,
        ) -> list[Any]:
            vectors = []
            rate = audio.getframerate()
            np = importlib.import_module("numpy")
            for index, (start, end) in enumerate(ranges):
                check_cancelled()
                entry = f"{key}:{start}:{end}"
                if entry in cache.entries:
                    stats["cache_hits"] += 1
                    vector = unit_vector(cache.entries[entry])
                else:
                    audio.setpos(round(start * rate / 1000))
                    samples = array("h")
                    samples.frombytes(audio.readframes(round((end - start) * rate / 1000)))
                    stats["audio_seconds"] += len(samples) / rate
                    values = np.asarray(samples, dtype=np.float32) / 32768.0
                    vector = None
                    # Do not create voice identities from silence or badly clipped audio.
                    if (values.size and float(np.sqrt(np.mean(values * values))) >= 0.0001
                            and float(np.mean(np.abs(values) >= 0.999)) < 0.1):
                        embedding = self._embedding(get_extractor(), samples, rate)
                        stats["computed_fragments"] += 1
                        if embedding is not None:
                            vector = unit_vector(embedding)
                    check_cancelled()
                    cache.entries[entry] = vector.tolist() if vector is not None else []
                    cache.dirty = True
                if vector is not None:
                    vectors.append(vector)
                notice(index + 1, len(ranges))
            cache.save()
            return vectors

        try:
            check_cancelled()
            model = self._model_path(config)
            model_key = signature([
                "distributed-voice-v2-interior", file_identity(model),
                importlib.metadata.version("sherpa-onnx"),
            ])
            known_profiles, known_vectors = [], []
            for index, profile in enumerate(profiles):
                report(0.3 * index / len(profiles),
                       f"Preparing saved voice {index + 1} of {len(profiles)}")
                try:
                    path = Path(profile.sample_path)
                    cache = VoiceprintCache(path, model_key)
                    with wave.open(str(path), "rb") as audio:
                        duration = _validate_wave(audio)
                        ranges = sample_ranges(
                            [(0, duration)], duration, turn_boundaries=False,
                        )[:INITIAL_FRAGMENTS]
                        vectors = vectors_for(audio, cache, "profile", ranges,
                            lambda done, count, index=index:
                            report(0.3 * (index + done / count) / len(profiles),
                                   f"Preparing saved voice {index + 1} of {len(profiles)} "
                                   f"· fragment {done}/{count}"))
                        vector = centroid(vectors)
                        if vector is not None:
                            known_profiles.append(profile)
                            known_vectors.append(vector)
                except JobCancelledError:
                    raise
                except Exception as error:
                    logger.warning("Skipping saved voice %s: %s", profile.id, error)
            profiles_finished = time.monotonic()
            stats["saved_profiles_seconds"] = profiles_finished - started
            if not known_profiles:
                report(1.0, "No usable saved voice samples; keeping speaker labels")
                return {}
            threshold = float(config.get("profile_match_threshold", 0.72))
            clean = exclusive_ranges(turns)
            speakers = sorted({turn.speaker for turn in turns})
            turns_key = signature(sorted((t.start_ms, t.end_ms, t.speaker) for t in turns))
            cache = VoiceprintCache(audio_path, model_key, turns_key)
            matches: dict[int, Any] = {}
            with wave.open(str(audio_path), "rb") as audio:
                duration = _validate_wave(audio)
                for index, speaker in enumerate(speakers):
                    def notice(done: int, count: int, index: int = index) -> None:
                        report(0.3 + 0.65 * (index + done / 10) / len(speakers),
                               f"Matching speaker {index + 1} of {len(speakers)} "
                               f"· fragment {done}/{count}")
                    report(0.3 + 0.65 * index / len(speakers),
                           f"Matching speaker {index + 1} of {len(speakers)}")
                    ranges = sample_ranges(clean.get(speaker, []), duration)
                    vectors = vectors_for(audio, cache, str(speaker),
                                          ranges[:INITIAL_FRAGMENTS], notice)
                    before = time.monotonic()
                    winner, ambiguous = identify(vectors, known_vectors, threshold)
                    stats["comparison_seconds"] += time.monotonic() - before
                    if ambiguous and len(ranges) > INITIAL_FRAGMENTS:
                        report(0.3 + 0.65 * (index + 0.5) / len(speakers),
                               f"Checking uncertain match · speaker {index + 1} "
                               f"of {len(speakers)} · analyzing more voice samples")
                        vectors += vectors_for(
                            audio, cache, str(speaker), ranges[INITIAL_FRAGMENTS:],
                            lambda done, count: notice(done + INITIAL_FRAGMENTS,
                                                       count + INITIAL_FRAGMENTS),
                        )
                        before = time.monotonic()
                        winner, _ = identify(vectors, known_vectors, threshold)
                        stats["comparison_seconds"] += time.monotonic() - before
                    if winner is not None:
                        matches[speaker] = known_profiles[winner]
            stats["meeting_speakers_seconds"] = time.monotonic() - profiles_finished
            report(1.0, f"Voice matching complete · {len(matches)}/{len(speakers)} identified")
            return matches
        except JobCancelledError as error:
            failure = error
            raise
        except Exception as error:
            failure = error
            logger.warning("Saved voice matching skipped: %s", error)
            return {}
        finally:
            stats["elapsed_seconds"] = round(time.monotonic() - started, 3)
            with self._state_lock:
                self._last_match_stats = stats
            logger.info("Voice matching: %.2fs total · %d cached fragments · %d computed · "
                        "%.1fs audio · %.2fs model load · %.3fs comparisons · "
                        "%.2fs saved voices · %.2fs meeting speakers",
                        stats["elapsed_seconds"], stats["cache_hits"], stats["computed_fragments"],
                        stats["audio_seconds"], stats["model_load_seconds"],
                        stats["comparison_seconds"], stats["saved_profiles_seconds"],
                        stats["meeting_speakers_seconds"])
            self._request_finished(failure)

    def _create_extractor(self, config: dict[str, Any]) -> Any:
        sherpa = importlib.import_module("sherpa_onnx")
        provider = str(config.get("provider", "cpu"))
        if config.get("engine") == "nvidia-nemotron-3-diarization":
            provider = "cpu"
        if provider == "cuda":
            _configure_cuda_dlls()
        return sherpa.SpeakerEmbeddingExtractor(sherpa.SpeakerEmbeddingExtractorConfig(
            model=str(self._model_path(config)), num_threads=int(config.get("num_threads", 2)),
            debug=bool(config.get("debug", False)), provider=provider,
        ))

    def _model_path(self, config: dict[str, Any]) -> Path:
        return self.models_dir / EMBEDDING_FILES[self._embedding_name(config)]

    @staticmethod
    def _embedding_name(config: dict[str, Any]) -> str:
        requested = str(config.get("embedding_model", "3d-speaker"))
        return requested if requested in EMBEDDING_FILES else "3d-speaker"

    @staticmethod
    def _embedding(
        extractor: Any, samples: array[int], sample_rate: int
    ) -> Any | None:
        stream = extractor.create_stream()
        numpy = importlib.import_module("numpy")
        stream.accept_waveform(
            sample_rate,
            numpy.asarray(samples, dtype=numpy.float32) / 32768.0,
        )
        stream.input_finished()
        if not extractor.is_ready(stream):
            return None
        return extractor.compute(stream)

    def _request_started(self, state: str) -> None:
        with self._state_lock:
            self._active_requests += 1
            self._state = state
            self._last_error = None

    def _request_finished(self, failure: Exception | None) -> None:
        with self._state_lock:
            self._active_requests = max(0, self._active_requests - 1)
            if failure is not None:
                self._last_error = str(failure)
            if not self._active_requests:
                self._state = "error" if failure is not None else "idle"


def _validate_wave(source: wave.Wave_read) -> int:
    if source.getsampwidth() != 2 or source.getnchannels() != 1:
        raise CapabilityUnavailableError("Voice matching requires 16-bit mono PCM WAV audio")
    return int(source.getnframes() * 1000 / source.getframerate())
