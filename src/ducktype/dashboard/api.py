"""In-process data/control API shared by the native window (pywebview bridge)
and the dev/preview Flask shim.

Every method returns a plain JSON-able object -- the same payloads the old HTTP
endpoints returned -- by delegating to ``analysis.stats``, ``storage.db``,
``updater`` and ``Config``. The native app exposes an ``Api`` instance directly
to JavaScript via pywebview's ``js_api`` (no HTTP, no port); ``server.py`` keeps
thin routes that call the same methods so browser-based development still works.

Read results are memoized by the database ``revision`` so repeated queries
between writes (tab switches, reopening the window) are served from cache.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Optional

from .. import autostart, updater
from ..analysis import stats
from ..analysis.report_jobs import ReportJob
from ..perf import timed
from .relocate import Relocator

log = logging.getLogger("ducktype")

# Read endpoints whose result depends only on the data revision (safe to cache).
# Excludes time-window queries and live status/progress.
_NO_CACHE = {"timeseries"}

READ_ENDPOINTS = (
    "overview",
    "top_chars",
    "top_words",
    "pos",
    "pos_words",
    "topics",
    "heatmap",
    "daily",
    "timeseries",
    "apps",
    "app_detail",
    "edits",
    "trend",
    "search",
    "fun",
    "gamify",
    "ticker",
    "report",
    "sequence",
    "board",
    "board_fast",
    "board_heavy",
    "report_fast",
    "report_heavy",
    "sequence_apps",
)
_READ_ENDPOINTS = frozenset(READ_ENDPOINTS)
_STALE_CACHE_TTL = {
    "board_fast": 6.0,
    "board_heavy": 15.0,
    "gamify": 10.0,
    "report": 30.0,
    "report_fast": 10.0,
    "report_heavy": 60.0,
    "topics": 15.0,
    "top_words": 15.0,
    "pos": 15.0,
}


class Api:
    """Bridge object exposed to the webview frontend (and reused by Flask)."""

    def __init__(self, db, config, status_fn=None, on_quit=None):
        self._db = db
        self._config = config
        self._status_fn = status_fn
        self._on_quit = on_quit
        self._relocator = Relocator(db)
        self._report_job = ReportJob(db, config)
        # Keep the native Window private. pywebview exposes public attributes
        # on js_api; exposing Window makes it recursively inspect WebView2/WinForms
        # objects and can freeze the app during startup.
        self._window = None
        self._window_maximized = False
        self._cache: dict = {}
        self._cache_rev = -1

    def _set_window(self, window) -> None:
        self._window = window

    # ---- helpers ---------------------------------------------------------
    def _bounds(self, p: dict):
        return stats.resolve_range(p.get("range", "7d"), p.get("start"), p.get("end"))

    def _sequence_bounds(self, p: dict):
        day = (p.get("day") or "").strip()
        if day:
            start = datetime.strptime(day, "%Y-%m-%d")
            return start.timestamp(), (start + timedelta(days=1)).timestamp()
        return self._bounds(p)

    def _cached(self, endpoint: str, params: dict, fn):
        now = time.monotonic()
        if endpoint in _NO_CACHE:
            return fn()
        rev = self._db.revision
        if rev != self._cache_rev:
            ttl_keys = set(_STALE_CACHE_TTL)
            self._cache = {
                k: v for k, v in self._cache.items()
                if k.split("|", 1)[0] in ttl_keys and now - v[1] <= _STALE_CACHE_TTL[k.split("|", 1)[0]]
            }
            self._cache_rev = rev
        key = endpoint + "|" + json.dumps(params, sort_keys=True, ensure_ascii=False)
        if key in self._cache:
            cached_rev, cached_at, cached_val = self._cache[key]
            ttl = _STALE_CACHE_TTL.get(endpoint, 0.0)
            if cached_rev == rev or (ttl and now - cached_at <= ttl):
                return cached_val
        with timed(f"api.{endpoint}"):
            val = fn()
        self._cache[key] = (rev, now, val)
        return val

    def _cached_custom(self, endpoint: str, params: dict, fn, ttl: float):
        now = time.monotonic()
        key = endpoint + "|" + json.dumps(params, sort_keys=True, ensure_ascii=False)
        if key in self._cache:
            _rev, cached_at, cached_val = self._cache[key]
            if now - cached_at <= ttl:
                return cached_val
        with timed(f"api.{endpoint}"):
            val = fn()
        self._cache[key] = (self._db.revision, now, val)
        return val

    # ---- read dispatch ---------------------------------------------------
    def get(self, endpoint: str, params: Optional[dict] = None):
        """Single entry point for all read endpoints used by the frontend."""
        p = params or {}
        if endpoint not in _READ_ENDPOINTS:
            return {"error": f"unknown endpoint {endpoint}"}
        fn = getattr(self, "_r_" + endpoint, None)
        if fn is None:
            return {"error": f"unknown endpoint {endpoint}"}
        start = time.perf_counter()
        try:
            return self._cached(endpoint, p, lambda: fn(p))
        finally:
            elapsed = time.perf_counter() - start
            if elapsed > 1.0:
                log.info("Dashboard endpoint %s took %.2fs", endpoint, elapsed)

    # Individual read endpoints (mirror the old HTTP routes 1:1).
    def _r_overview(self, p):
        since, until = self._bounds(p)
        return stats.overview(self._db, since, self._config.run_gap_seconds,
                              self._config.session_gap_seconds, until)

    def _r_top_chars(self, p):
        since, until = self._bounds(p)
        n = int(p.get("n", 50))
        return [{"ch": c, "count": k} for c, k in stats.top_chars(self._db, since, n, until)]

    def _r_top_words(self, p):
        since, until = self._bounds(p)
        n = int(p.get("n", 50))
        rows = stats.top_words(self._db, since, n, self._config.run_gap_seconds, until)
        return [{"word": w, "count": c} for w, c in rows]

    def _r_pos(self, p):
        since, until = self._bounds(p)
        rows = stats.pos_distribution(self._db, since, self._config.run_gap_seconds, until)
        return [{"pos": ps, "label": lbl, "count": c} for ps, lbl, c in rows]

    def _r_pos_words(self, p):
        since, until = self._bounds(p)
        return stats.pos_word_distribution(
            self._db, p.get("pos", ""), since, self._config.run_gap_seconds, until,
            int(p.get("n", 12)), int(p.get("min_len", 2)))

    def _r_topics(self, p):
        since, until = self._bounds(p)
        rows = stats.topics(self._db, since, int(p.get("n", 30)), until)
        return [{"word": w, "weight": round(float(wt), 4)} for w, wt in rows]

    def _r_heatmap(self, p):
        since, until = self._bounds(p)
        return {"grid": stats.heatmap(self._db, since, until)}

    def _r_daily(self, p):
        since, until = self._bounds(p)
        return [{"date": d, "count": c} for d, c in stats.daily(self._db, since, until)]

    def _r_timeseries(self, p):
        hours = p.get("hours")
        if hours:
            since, until = time.time() - float(hours) * 3600, None
        else:
            since, until = self._bounds(p)
        return stats.timeseries(self._db, since, until, p.get("bucket", "hour"))

    def _r_apps(self, p):
        since, until = self._bounds(p)
        n = int(p.get("n", 20))
        return [{"app": a, "count": c} for a, c in stats.per_app(self._db, since, n, until)]

    def _r_app_detail(self, p):
        since, until = self._bounds(p)
        n = int(p.get("n", 20))
        return stats.app_detail(self._db, p.get("app", ""), since,
                                self._config.run_gap_seconds, until, n)

    def _r_edits(self, p):
        since, until = self._bounds(p)
        return stats.edits(self._db, since, until, self._config.session_gap_seconds)

    def _r_trend(self, p):
        since, until = self._bounds(p)
        return stats.trend(self._db, since, until, self._config.run_gap_seconds,
                           self._config.session_gap_seconds) or {}

    def _r_search(self, p):
        since, until = self._bounds(p)
        return stats.search(self._db, p.get("q", ""), since,
                            self._config.run_gap_seconds, until)

    def _r_fun(self, p):
        since, until = self._bounds(p)
        return stats.fun_rankings(self._db, since, self._config.run_gap_seconds, until)

    def _r_gamify(self, p):
        return stats.gamify(self._db, self._config.daily_goal)

    def _r_ticker(self, p):
        return stats.ticker(self._db, self._config.run_gap_seconds,
                            self._config.session_gap_seconds, self._config.daily_goal)

    def _r_report(self, p):
        return self._report_data(p.get("period", "today"))

    def _r_report_fast(self, p):
        return stats.report_fast(self._db, p.get("period", "today"),
                                 self._config.run_gap_seconds,
                                 self._config.session_gap_seconds,
                                 p.get("start"), p.get("end"))

    def _r_report_heavy(self, p):
        return stats.report_heavy(self._db, p.get("period", "today"),
                                  self._config.run_gap_seconds)

    def _r_sequence(self, p):
        since, until = self._sequence_bounds(p)
        limit = int(p.get("limit", 200))
        return stats.sequence_recent(
            self._db, since, self._config.run_gap_seconds, limit, until,
            p.get("app", ""),
        )

    def _r_sequence_apps(self, p):
        since, until = self._sequence_bounds(p)
        return stats.sequence_apps(self._db, since, until)

    def _r_board(self, p):
        """Batched payload for the main board: one call instead of ~10."""
        out = self._r_board_fast(p)
        out.update(self._r_board_heavy(p))
        return out

    def _r_board_fast(self, p):
        """First-paint board payload; avoids live segmentation/TF-IDF work."""
        since, until = self._bounds(p)
        rg, sg = self._config.run_gap_seconds, self._config.session_gap_seconds
        char_n = int(p.get("charN", 30))
        return {
            "overview": stats.overview(self._db, since, rg, sg, until),
            "trend": stats.trend(self._db, since, until, rg, sg) or {},
            "daily": [{"date": d, "count": c} for d, c in stats.daily(self._db, since, until)],
            "top_chars": [{"ch": c, "count": k} for c, k in stats.top_chars(self._db, since, char_n, until)],
            "apps": [{"app": a, "count": c} for a, c in stats.per_app(self._db, since, 12, until)],
            "heatmap": {"grid": stats.heatmap(self._db, since, until)},
        }

    def _r_board_heavy(self, p):
        """Deferred board analytics. Reads the per-day word/POS rollups
        (materialized incrementally by segment.build_words) instead of running
        live jieba, so switching the range is pure SQL and never stalls the UI."""
        since, until = self._bounds(p)
        rg = self._config.run_gap_seconds
        word_n = int(p.get("wordN", 30))
        word_rows = stats.top_words_daily(self._db, since, until, word_n, rg)
        pos_rows = stats.pos_distribution_daily(self._db, since, until, rg)
        topic_rows = stats.topics_daily(self._db, since, until, 30, rg)
        return {
            "top_words": [{"word": w, "count": c} for w, c in word_rows],
            "pos": [{"pos": ps, "label": lbl, "count": c}
                    for ps, lbl, c in pos_rows],
            "topics": [{"word": w, "weight": round(float(wt), 4)}
                       for w, wt in topic_rows],
        }

    def _report_data(self, period: str):
        return self._cached_custom(
            "report_data", {"period": period},
            lambda: stats.report(self._db, period, self._config.run_gap_seconds,
                                 self._config.session_gap_seconds),
            ttl=30.0,
        )

    # ---- live status / updates (never cached) ---------------------------
    def status(self):
        return self._status_fn() if self._status_fn else {}

    def update_check(self):
        return updater.check()

    def update_apply(self):
        return updater.start_apply(self._on_quit)

    def update_progress(self):
        return updater.progress()

    # ---- on-demand full report (background word analytics) --------------
    def report_generate(self, params: Optional[dict] = None):
        return self._report_job.start(params or {})

    def report_progress(self):
        return self._report_job.progress()

    # ---- config ----------------------------------------------------------
    def config_get(self):
        data = {k: v for k, v in asdict(self._config).items() if not k.startswith("_")}
        data["editable"] = list(self._config.EDITABLE)
        data["restart_required"] = list(self._config.RESTART_REQUIRED)
        return data

    def config_set(self, updates: Optional[dict] = None):
        updates = updates or {}
        restart = self._config.apply(updates)
        if "autostart" in updates:
            try:
                autostart.set_enabled(self._config.autostart)
            except Exception:
                log.exception("Failed to toggle autostart from dashboard")
        return {"ok": True, "restart_required": restart}

    # ---- data management -------------------------------------------------
    def data_summary(self):
        from ..paths import data_dir, root_dir
        s = self._db.stats_summary()
        root = root_dir()
        s["db_path"] = str(root / "ducktype.db")
        s["data_dir"] = str(root)
        s["default_dir"] = str(data_dir())
        s["is_default"] = (str(root) == str(data_dir()))
        return s

    def data_pick_dir(self):
        try:
            from .. import firstrun
            from ..paths import root_dir
            chosen = firstrun.pick_folder(initial=root_dir())
        except Exception:
            log.exception("pick_dir failed")
            chosen = None
        return {"dir": chosen or ""}

    def data_relocate(self, target: Optional[str] = None):
        return self._relocator.start(target)

    def data_relocate_progress(self):
        return self._relocator.progress()

    def data_clear(self):
        return {"deleted": self._db.clear_all()}

    def data_delete(self, start=None, end=None):
        since, until = stats.resolve_range("custom", start, end)
        return {"deleted": self._db.delete_range(since, until)}

    def quote_seen(self, text=None):
        if isinstance(text, str) and text:
            try:
                self._db.record_quote_view(text)
            except Exception:
                pass
        return {"ok": True}

    # ---- binary: card image + native-save exports -----------------------
    def card_png(self, period="today"):
        from .. import cards
        rep = self._report_data(period)
        buf = io.BytesIO()
        cards.render_card(rep).save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return "data:image/png;base64," + b64

    def save_card(self, period="today"):
        from .. import cards
        rep = self._report_data(period)
        buf = io.BytesIO()
        cards.render_card(rep).save(buf, format="PNG")
        return self._save_dialog(f"ducktype_{period}.png", buf.getvalue(), binary=True)

    def export_sequence(self, fmt="txt", params: Optional[dict] = None):
        p = params or {}
        since, until = self._sequence_bounds(p)
        runs = list(reversed(stats.sequence_recent(
            self._db, since, self._config.run_gap_seconds, 10_000_000, until,
            p.get("app", ""))))
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if fmt == "txt":
            body = "\n".join(r["text"] for r in runs).encode("utf-8")
        elif fmt == "json":
            body = json.dumps(runs, ensure_ascii=False, indent=2).encode("utf-8")
        elif fmt == "csv":
            sbuf = io.StringIO()
            sbuf.write("﻿time,app,text\n")
            for r in runs:
                t = datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S") if r["ts"] else ""
                text = '"' + r["text"].replace('"', '""') + '"'
                sbuf.write(f"{t},{r['app'] or ''},{text}\n")
            body = sbuf.getvalue().encode("utf-8")
        else:
            return {"ok": False, "error": "unknown format"}
        return self._save_dialog(f"ducktype_sequence_{stamp}.{fmt}", body, binary=True)

    def _save_dialog(self, default_name, data, binary=False):
        """Native save dialog (webview) then write the file. Falls back to the
        data root if no window/dialog is available."""
        try:
            import webview
            win = self._window or (webview.windows[0] if webview.windows else None)
            target = None
            if win is not None:
                res = win.create_file_dialog(
                    webview.SAVE_DIALOG, save_filename=default_name)
                if not res:
                    return {"ok": False, "cancelled": True}
                target = res if isinstance(res, str) else res[0]
            if target is None:
                from ..paths import root_dir
                target = str(root_dir() / default_name)
            with open(target, "wb") as f:
                f.write(data if binary else data.encode("utf-8"))
            return {"ok": True, "path": target}
        except Exception as exc:
            log.exception("save dialog failed")
            return {"ok": False, "error": str(exc)}

    # ---- window controls (frameless titlebar) ---------------------------
    def window_minimize(self):
        try:
            if self._window:
                self._window.minimize()
        except Exception:
            pass
        return {"ok": True}

    def window_toggle_maximize(self):
        """Toggle the frameless native window between normal and maximized."""
        try:
            if self._window:
                if self._window_maximized:
                    self._window.restore()
                    self._window_maximized = False
                else:
                    self._window.maximize()
                    self._window_maximized = True
        except Exception:
            pass
        return {"ok": True, "maximized": self._window_maximized}

    def window_hide(self):
        """Hide to tray (capture keeps running)."""
        try:
            if self._window:
                self._window.hide()
                self._window_maximized = False
        except Exception:
            pass
        return {"ok": True}
