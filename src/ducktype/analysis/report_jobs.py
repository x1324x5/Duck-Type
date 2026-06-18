"""Background worker for the on-demand 'full report' word analytics.

The base report numbers + narrative render instantly (``stats.report_fast``).
The heavier word-level analytics (新词 / 回归词 / 双字·三字词 / 长词榜 / 词性 /
主题) involve a TF-IDF pass and rollup comparisons, so they run on a background
thread driven by ``stats.report_words``; the dashboard polls ``progress()`` for a
determinate progress bar and the final result. Mirrors ``dashboard.relocate``.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from . import stats

log = logging.getLogger("ducktype")


class ReportJob:
    def __init__(self, db, config):
        self._db = db
        self._config = config
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._seq = 0
        self._state = {"phase": "idle", "pct": 0, "label": "",
                       "result": None, "error": "", "params": None}

    def progress(self) -> dict:
        with self._lock:
            return dict(self._state)

    def start(self, params: Optional[dict] = None) -> dict:
        p = params or {}
        period = p.get("period", "today")
        start, end = p.get("start"), p.get("end")
        with self._lock:
            self._seq += 1
            seq = self._seq
            self._state = {"phase": "running", "pct": 5, "label": "开始生成",
                           "result": None, "error": "",
                           "params": {"period": period, "start": start, "end": end}}
        t = threading.Thread(
            target=self._run, args=(seq, period, start, end),
            name="report-job", daemon=True)
        with self._lock:
            self._thread = t
        t.start()
        return {"ok": True, "started": True}

    def _run(self, seq, period, start, end) -> None:
        def _progress(pct, label):
            with self._lock:
                if seq != self._seq:
                    return  # superseded by a newer request
                self._state.update(pct=pct, label=label)

        try:
            result = stats.report_words(
                self._db, period, self._config.run_gap_seconds,
                start, end, progress=_progress)
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("report_words failed")
            with self._lock:
                if seq == self._seq:
                    self._state.update(phase="error", error=str(exc))
            return
        with self._lock:
            if seq != self._seq:
                return
            self._state.update(phase="done", pct=100, label="完成", result=result)
