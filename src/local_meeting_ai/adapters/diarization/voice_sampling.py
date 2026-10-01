"""Bounded, distributed sampling and conservative cosine voice matching."""
from __future__ import annotations

import importlib
import math
from collections import defaultdict
from typing import Any

from local_meeting_ai.domain.entities import DiarizationSegment

INITIAL_FRAGMENTS = 5
MAX_FRAGMENTS = 10
FRAGMENT_MS = 6000
MIN_FRAGMENT_MS = 1500


def exclusive_ranges(turns: list[DiarizationSegment]) -> dict[int, list[tuple[int, int]]]:
    """Sweep once; overlapping different voices never enter an identity embedding."""
    events: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for turn in turns:
        start, end = max(0, turn.start_ms), turn.end_ms
        if end > start:
            events[start].append((turn.speaker, 1))
            events[end].append((turn.speaker, -1))
    active: dict[int, int] = {}
    result: dict[int, list[tuple[int, int]]] = defaultdict(list)
    previous = 0
    for instant, changes in sorted(events.items()):
        if instant > previous and len(active) == 1:
            speaker = next(iter(active))
            ranges = result[speaker]
            if ranges and ranges[-1][1] == previous:
                ranges[-1] = (ranges[-1][0], instant)
            else:
                ranges.append((previous, instant))
        for speaker, delta in changes:
            count = active.get(speaker, 0) + delta
            if count:
                active[speaker] = count
            else:
                active.pop(speaker, None)
        previous = instant
    return dict(result)


def sample_ranges(
    ranges: list[tuple[int, int]], duration_ms: int, *, turn_boundaries: bool = True,
) -> list[tuple[int, int]]:
    candidates: list[tuple[int, int]] = []
    extra: list[tuple[int, int]] = []
    short = []
    for start, end in ranges:
        start, end = max(0, start), min(duration_ms, end)
        if turn_boundaries:
            if end - start < 9200:
                length = min(FRAGMENT_MS, end - start - 200)
                if length >= MIN_FRAGMENT_MS:
                    middle = (start + end) // 2
                    short.append((middle - length // 2, middle - length // 2 + length))
                continue
            start, end = start + 3000, end - 200
        first = True
        for offset in range(start, end, FRAGMENT_MS):
            stop = min(end, offset + FRAGMENT_MS)
            if stop - offset >= MIN_FRAGMENT_MS:
                (candidates if first or not turn_boundaries else extra).append((offset, stop))
                first = False
    # Prefer distinct long interventions. Add interior windows if there are too
    # few; short centered turns are the last resort, never shifted past their end.
    if len(candidates) < INITIAL_FRAGMENTS:
        candidates.extend(extra)
    if len(candidates) < INITIAL_FRAGMENTS:
        candidates.extend(short)
    candidates.sort()
    if not candidates:
        return []
    # Five evenly distributed windows first; expansion fills the largest gaps.
    selected = sorted({round(i * (len(candidates) - 1) / 4) for i in range(5)})
    remaining = set(range(len(candidates))) - set(selected)
    while remaining and len(selected) < MAX_FRAGMENTS:
        index = max(sorted(remaining), key=lambda i: min(abs(i - j) for j in selected))
        selected.append(index)
        remaining.remove(index)
    return [candidates[index] for index in selected]


def unit_vector(value: Any) -> Any | None:
    np = importlib.import_module("numpy")
    vector = np.asarray(value, dtype=np.float32)
    if vector.ndim != 1 or not vector.size or not np.isfinite(vector).all():
        return None
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 1e-8 else None


def centroid(vectors: list[Any]) -> Any | None:
    np = importlib.import_module("numpy")
    return unit_vector(np.mean(vectors, axis=0)) if vectors else None


def identify(vectors: list[Any], profiles: list[Any], threshold: float) -> tuple[int | None, bool]:
    """Require cosine threshold, separation from runner-up and fragment agreement."""
    np = importlib.import_module("numpy")
    query = centroid(vectors)
    if query is None or not profiles:
        return None, False
    matrix = np.stack(profiles)
    scores = matrix @ query
    winner = int(np.argmax(scores))
    best = float(scores[winner])
    gap = best - float(np.partition(scores, -2)[-2]) if len(profiles) > 1 else 1.0
    per_fragment = np.stack(vectors) @ matrix.T
    votes = sum(int(np.argmax(row)) == winner and float(row[winner]) >= threshold
                for row in per_fragment)
    accepted = best >= threshold and gap >= 0.04 and votes >= math.ceil(len(vectors) * 0.6)
    return (winner if accepted else None), (not accepted and best >= threshold - 0.1)
