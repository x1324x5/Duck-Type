"""Lightweight performance timing helpers."""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator

log = logging.getLogger("ducktype.perf")


@contextmanager
def timed(label: str, threshold_ms: float = 250.0) -> Iterator[None]:
    """Log stages that cross ``threshold_ms``.

    The helper is intentionally tiny: no dependencies, no allocations on the hot
    path beyond a perf-counter read and a log call only for slow stages.
    """

    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if elapsed_ms >= threshold_ms:
            log.info("%s took %.1fms", label, elapsed_ms)
