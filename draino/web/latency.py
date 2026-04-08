"""Rolling latency summaries for key web and background operations."""
from __future__ import annotations

import threading
import time
from collections import deque
from contextlib import contextmanager

_MAX_SAMPLES = 100
_latency_lock = threading.Lock()
_latency_samples: dict[str, deque[float]] = {}


def record_latency(operation: str, duration_ms: float) -> None:
    with _latency_lock:
        samples = _latency_samples.setdefault(operation, deque(maxlen=_MAX_SAMPLES))
        samples.append(float(duration_ms))


@contextmanager
def measure_latency(operation: str):
    started = time.perf_counter()
    try:
        yield
    finally:
        record_latency(operation, (time.perf_counter() - started) * 1000.0)


def get_latency_summary() -> dict[str, dict]:
    with _latency_lock:
        snapshot = {operation: list(samples) for operation, samples in _latency_samples.items()}

    summary: dict[str, dict] = {}
    for operation, samples in snapshot.items():
        if not samples:
            continue
        ordered = sorted(samples)
        p95_index = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))
        summary[operation] = {
            "count": len(samples),
            "last_ms": round(samples[-1], 1),
            "avg_ms": round(sum(samples) / len(samples), 1),
            "p95_ms": round(ordered[p95_index], 1),
        }
    return summary
