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
import os
import tempfile
import time
import zipfile
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Optional

from .. import autostart, updater
from ..analysis import reporting, segment, stats
from ..analysis.report_jobs import ReportJob
from ..perf import timed
from .relocate import Relocator

log = logging.getLogger("ducktype")

# Read endpoints whose result depends only on the data revision (safe to cache).
# Excludes time-window queries, live status/progress, and config-driven reads
# (``tracked`` depends on Config.tracked_terms, which a write does not bump).
_NO_CACHE = {"timeseries", "tracked", "mini_stats"}

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
    "tracked",
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
    "lexicon_stats",
    "lexicon_report",
    "lexicon_words",
    "mini_stats",
    "richness",
    "contrib",
    "usage",
    "report_compare",
    "records",
    "day",
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
        self._real_db = db          # the live database; restored when demo ends
        self._demo_db = None        # lazily built throwaway sample database
        self._demo = False
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
        self._hotkeys = None        # HotkeyManager, wired by app.set_hotkeys
        self._cache: dict = {}
        self._cache_rev = -1
        self._lexicons = None       # lazily-built LexiconStore (词库 subsystem)
        # Teach jieba the tracked terms so they segment as whole words in the
        # word/POS/topic panels (cheap: just records the desired set; jieba is
        # touched lazily on the first segmentation).
        segment.set_user_terms(self._config.tracked_terms)
        # 生僻字 classification is the complement of the common-character filter;
        # feed the user's "extra common" list into that filter (see stats).
        stats.set_user_common(self._config.common_chars_extra)

    def _set_window(self, window) -> None:
        self._window = window

    def set_hotkeys(self, manager) -> None:
        """Wire the global-hotkey manager so config_set can re-register live."""
        self._hotkeys = manager

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

    def _r_richness(self, p):
        since, until = self._bounds(p)
        return stats.richness_trend(self._db, since, until)

    def _r_contrib(self, p):
        try:
            days = max(31, min(730, int(p.get("days", 364))))
        except (TypeError, ValueError):
            days = 364
        return stats.contrib_calendar(self._db, days)

    def _r_usage(self, p):
        return reporting.dashboard_usage(self._db, int(p.get("days", 30)))

    def _r_report_compare(self, p):
        # ``a``/``b`` arrive as dicts over the native bridge, or as JSON strings
        # over the dev HTTP shim (query params can't carry nested objects).
        def _spec(v):
            if isinstance(v, str):
                try:
                    return json.loads(v) or {}
                except ValueError:
                    return {}
            return v or {}
        return reporting.report_compare(
            self._db, _spec(p.get("a")), _spec(p.get("b")),
            self._config.run_gap_seconds, self._config.session_gap_seconds)

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

    def _r_tracked(self, p):
        since, until = self._bounds(p)
        terms = p.get("terms")
        groups = None
        if terms is None:
            terms = list(self._config.tracked_terms)
            groups = list(self._config.tracked_groups)
        elif isinstance(terms, str):
            terms = [t.strip() for t in terms.replace("\n", ",").split(",") if t.strip()]
        rg = self._config.run_gap_seconds
        rows = stats.tracked_terms(self._db, terms, since, rg, until)
        # 环比: compare each term against the immediately preceding window of equal
        # length (skipped for the unbounded "all" range, which has no baseline).
        prev_totals = {}
        if since is not None:
            import time as _time
            end = until if until is not None else _time.time()
            length = end - since
            if length > 0:
                for r in stats.tracked_terms(self._db, terms, since - length, rg, since):
                    prev_totals[r["term"]] = r["total"]
        # Attach the matching group label (by original term position) + delta.
        group_for = {}
        if groups is not None:
            for t, g in zip(self._config.tracked_terms, self._config.tracked_groups):
                group_for[t] = g
        for r in rows:
            r["group"] = group_for.get(r["term"], "")
            prev = prev_totals.get(r["term"])
            if since is None or prev is None:
                r["delta_pct"] = None      # no comparable baseline
            elif prev == 0:
                r["delta_pct"] = None if r["total"] == 0 else "new"
            else:
                r["delta_pct"] = round((r["total"] - prev) / prev * 100, 1)
        return {"terms": rows}

    def _r_fun(self, p):
        since, until = self._bounds(p)
        return stats.fun_rankings(self._db, since, self._config.run_gap_seconds, until)

    def _r_gamify(self, p):
        return stats.gamify(self._db, self._config.daily_goal,
                            self._config.weekly_goal, self._config.monthly_goal)

    def _r_records(self, p):
        return reporting.records(self._db, self._config.run_gap_seconds,
                                 self._config.session_gap_seconds)

    def _r_day(self, p):
        day = (p.get("day") or "").strip()
        if not day:
            day = datetime.now().strftime("%Y-%m-%d")
        return reporting.day_detail(self._db, day, self._config.run_gap_seconds,
                                    self._config.session_gap_seconds)

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
            p.get("apps", p.get("app", "")), p.get("q", ""),
        )

    def _r_sequence_apps(self, p):
        since, until = self._sequence_bounds(p)
        return stats.sequence_apps(self._db, since, until)

    def _r_lexicon_stats(self, p):
        """Per-word occurrence breakdown of one lexicon over the range (for the
        词库 tab's share pie). Additive only -- never affects the board counts."""
        from ..analysis import lexicon
        since, until = self._bounds(p)
        store = self._lexicon_store()
        lex_id = p.get("id") or lexicon.IDIOM_ID
        meta = store.meta(lex_id)
        if meta is None:
            return {"id": lex_id, "found": False, "name": "", "size": 0,
                    "total": 0, "distinct": 0, "words": []}
        matcher = store.matcher(lex_id, meta["builtin"])
        counts = lexicon.scan_counts(
            self._db, matcher, since, self._config.run_gap_seconds, until)
        items = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        n = int(p.get("n", 24))
        return {
            "id": lex_id, "found": True, "name": meta["name"],
            "size": matcher.size, "total": sum(counts.values()),
            "distinct": len(counts),
            "words": [{"word": w, "count": c} for w, c in items[:n]],
        }

    def _r_lexicon_words(self, p):
        """Paginated, searchable word list of one lexicon (for the 查看/编辑
        modal). Read-only browse; user lexicons are edited via lexicon_edit_words."""
        store = self._lexicon_store()
        lex_id = (p.get("id") or "").strip()
        meta = store.meta(lex_id)
        if meta is None:
            return {"id": lex_id, "found": False, "words": [], "total": 0,
                    "size": 0, "editable": False}
        q = (p.get("q") or "").strip()
        words = store.words(lex_id)
        if q:
            words = [w for w in words if q in w]
        total = len(words)
        try:
            offset = max(0, int(p.get("offset", 0)))
            limit = max(1, min(500, int(p.get("limit", 100))))
        except (TypeError, ValueError):
            offset, limit = 0, 100
        return {
            "id": lex_id, "found": True, "name": meta["name"],
            "builtin": meta["builtin"], "derived": meta.get("derived", False),
            "editable": store.is_editable(lex_id),
            "size": meta.get("count", total), "total": total,
            "offset": offset, "limit": limit,
            "words": words[offset:offset + limit],
        }

    def _r_lexicon_report(self, p):
        """For the 报告 tab: each enabled lexicon's usage over the period, sorted
        by total matches, each with its single most-used word."""
        from ..analysis import lexicon
        period = p.get("period")
        if period == "custom":
            since, until = stats.resolve_range("custom", p.get("start"), p.get("end"))
        elif period:
            try:
                since, until, _ps, _pe, _label = stats.report_bounds(period)
            except ValueError:
                since, until = self._bounds(p)
        else:
            since, until = self._bounds(p)
        store = self._lexicon_store()
        rg = self._config.run_gap_seconds
        out = []
        for meta in store.list():
            if not meta["enabled"]:
                continue
            matcher = store.matcher(meta["id"], meta["builtin"])
            counts = lexicon.scan_counts(self._db, matcher, since, rg, until)
            if not counts:
                continue
            ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
            top_word, top_count = ranked[0]
            out.append({
                "id": meta["id"], "name": meta["name"],
                "total": sum(counts.values()), "distinct": len(counts),
                "top_word": top_word, "top_count": top_count,
                "top_words": [{"word": w, "count": c} for w, c in ranked[:8]],
            })
        out.sort(key=lambda r: r["total"], reverse=True)
        return {"lexicons": out}

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
    # ---- demo / sample data ---------------------------------------------
    def demo_status(self):
        return {"on": self._demo}

    def demo_set(self, on=True):
        """Swap the dashboard between the live database and a throwaway sample
        one. The user's real records are never touched -- demo data lives in its
        own temp file. Reads are re-pointed and the cache is cleared so the board
        reflects the active database immediately."""
        want = bool(on)
        if want == self._demo:
            return {"on": self._demo}
        if want:
            if self._demo_db is None:
                from .. import demo_data
                self._demo_db = demo_data.build_demo_database()
            self._db = self._demo_db
        else:
            self._db = self._real_db
        self._demo = want
        # Reports/relocator captured the db at construction; re-point reports so
        # the report tab matches the active data set (relocation stays on real).
        self._report_job = ReportJob(self._db, self._config)
        self._cache = {}
        self._cache_rev = -1
        return {"on": self._demo}

    def config_get(self):
        data = {k: v for k, v in asdict(self._config).items() if not k.startswith("_")}
        data["editable"] = list(self._config.EDITABLE)
        data["restart_required"] = list(self._config.RESTART_REQUIRED)
        # The *actual* registry Run-key state, so the settings page can confirm
        # autostart really took effect (vs just the saved preference).
        try:
            data["autostart_effective"] = autostart.is_enabled()
        except Exception:
            data["autostart_effective"] = data.get("autostart", False)
        return data

    def config_set(self, updates: Optional[dict] = None):
        updates = updates or {}
        restart = self._config.apply(updates)
        if "autostart" in updates:
            try:
                autostart.set_enabled(self._config.autostart)
            except Exception:
                log.exception("Failed to toggle autostart from dashboard")
        if "tracked_terms" in updates:
            # Re-teach jieba and bump the data revision so cached word/POS/topic
            # reads recompute; the next segmentation pass rebuilds the rollups
            # under the new dictionary (see segment.effective_seg_version).
            segment.set_user_terms(self._config.tracked_terms)
            try:
                self._db.revision += 1
            except Exception:
                pass
        if "common_chars_extra" in updates:
            # Recompute the 生僻字 filter and bump the revision so the 生僻字
            # panel / 常用字 lexicon reflect the new exclusions immediately.
            stats.set_user_common(self._config.common_chars_extra)
            try:
                self._db.revision += 1
            except Exception:
                pass
        # These settings feed cached read endpoints (goal ring, efficiency,
        # segmentation windows). Bump the revision so the cache invalidates and
        # the change takes effect at once — no need to wait for the next commit.
        if any(k in updates for k in
               ("daily_goal", "weekly_goal", "monthly_goal",
                "run_gap_seconds", "session_gap_seconds", "retention_days")):
            try:
                self._db.revision += 1
            except Exception:
                pass
        result = {"ok": True, "restart_required": restart}
        if "mini_open_hotkey" in updates or "mini_close_hotkey" in updates:
            # Re-register the global hotkeys immediately. The per-binding result
            # tells the settings page whether the OS accepted each combo (False =
            # already held by another app -> a conflict to surface).
            if self._hotkeys is not None:
                try:
                    result["hotkeys"] = self._hotkeys.apply(
                        self._config.mini_open_hotkey,
                        self._config.mini_close_hotkey)
                except Exception:
                    log.exception("Re-registering hotkeys failed")
            result["mini_open_hotkey"] = self._config.mini_open_hotkey
            result["mini_close_hotkey"] = self._config.mini_close_hotkey
        return result

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

    # ---- full-data export / import (migration) ---------------------------
    _PACK_FORMAT = 1

    def _export_pack(self):
        """Build the ``.duckpack`` archive (db + config + manifest) for the real
        database. Returns (default_filename, bytes)."""
        from ..paths import config_path
        tmp = tempfile.mkdtemp(prefix="duckexport_")
        db_tmp = os.path.join(tmp, "data.db")
        try:
            self._real_db.backup_to(db_tmp)
            summary = self._real_db.stats_summary()
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(db_tmp, "data.db")
                cfg = config_path()
                if os.path.exists(cfg):
                    z.write(str(cfg), "config.json")
                manifest = {
                    "app": "DuckType",
                    "format": self._PACK_FORMAT,
                    "exported_at": datetime.now().isoformat(timespec="seconds"),
                    "char_rows": summary.get("char_rows", 0),
                }
                z.writestr("manifest.json",
                           json.dumps(manifest, ensure_ascii=False, indent=2))
        finally:
            try:
                os.remove(db_tmp)
                os.rmdir(tmp)
            except OSError:
                pass
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"ducktype_backup_{stamp}.duckpack", buf.getvalue()

    def data_export(self):
        """Bundle the user's entire data set -- the SQLite database plus the
        config -- into a single ``.duckpack`` archive and save it via the native
        dialog. Always exports the real database, even while showing demo data."""
        name, data = self._export_pack()
        return self._save_dialog(name, data, binary=True)

    def data_import(self, path: Optional[str] = None):
        """Replace all current data with the contents of a ``.duckpack`` archive
        (chosen via the native open dialog, or ``path`` for the dev shim).
        Overwrites the real database and applies the packaged config. The capture
        thread keeps appending to the real DB; importing is a point-in-time
        replace, so do it when not actively typing."""
        if not path:
            picked = self._open_dialog([
                "DuckType 备份 (*.duckpack;*.zip)", "所有文件 (*.*)"])
            path = picked.get("path") or ""
        if not path:
            return {"ok": False, "cancelled": True}
        try:
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
                if "data.db" not in names:
                    return {"ok": False,
                            "error": "不是有效的 DuckType 备份文件（缺少 data.db）。"}
                tmp = tempfile.mkdtemp(prefix="duckimport_")
                z.extract("data.db", tmp)
                cfg_data = z.read("config.json") if "config.json" in names else None
        except (zipfile.BadZipFile, OSError) as exc:
            return {"ok": False, "error": f"无法读取备份文件：{exc}"}

        db_tmp = os.path.join(tmp, "data.db")
        try:
            n = self._real_db.import_from(db_tmp)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            try:
                os.remove(db_tmp)
                os.rmdir(tmp)
            except OSError:
                pass

        restart = []
        if cfg_data:
            try:
                incoming = json.loads(cfg_data.decode("utf-8"))
                editable = {k: incoming[k] for k in self._config.EDITABLE
                            if k in incoming}
                restart = self.config_set(editable).get("restart_required", [])
            except Exception:
                log.exception("import: applying packaged config failed")
        # Surface the freshly imported data: drop demo mode if active, otherwise
        # just invalidate the read cache (import_from already bumped revision).
        if self._demo:
            self.demo_set(False)
        else:
            self._cache = {}
            self._cache_rev = -1
        return {"ok": True, "char_rows": n, "restart_required": restart}

    # ---- lexicons (词库 subsystem) ---------------------------------------
    def _lexicon_store(self):
        if self._lexicons is None:
            from ..analysis.lexicon import LexiconStore
            from ..paths import root_dir
            store = LexiconStore(root_dir() / "lexicons")
            # Plug the 关注词 / 生僻字 systems into 词库 as derived built-in
            # lexicons so they get the same share pie + click-to-search.
            store.register_provider(
                "tracked", "关注词", lambda: list(self._config.tracked_terms))
            store.register_provider("rare", "生僻字", self._rare_chars)
            # The built-in 常用字 filter table, viewable in the 查看/编辑 modal.
            # Default OFF: it is a reference/filter, not something to count by
            # default (it would otherwise match almost every committed character).
            store.register_provider(
                "common", "常用字", self._common_chars, default_enabled=False)
            self._lexicons = store
        return self._lexicons

    def _rare_chars(self):
        """All-time uncommon single characters present in the active data set --
        the word source for the derived 生僻字 lexicon. Follows the common-char
        filter (built-in table + supplement + user's extra-common list)."""
        counts = stats.top_chars(self._db, None, 5_000_000, None)
        return [c for c, _n in counts if stats._is_uncommon(c)]

    def _common_chars(self):
        """The common-character filter table (built-in 3,500 常用字 + modern
        supplement + the user's extra-common list), sorted for stable display."""
        from ..analysis.common_chars import COMMON_CHARS
        merged = (set(COMMON_CHARS) | set(stats._COMMON_SUPPLEMENT)
                  | set(self._config.common_chars_extra))
        return sorted(merged)

    def lexicon_list(self):
        return {"items": self._lexicon_store().list()}

    def lexicon_create(self, name=None, text=None, words=None):
        """Create a user lexicon from pasted text (``text``) or an explicit word
        list (``words``, one entry each). Returns {ok, id}."""
        from ..analysis import lexicon as lx
        try:
            if words is not None:
                parsed = lx.parse_items(words)
            else:
                parsed = lx.parse_words(text or "")
            lex_id = self._lexicon_store().create(name, parsed)
            return {"ok": True, "id": lex_id}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def lexicon_update(self, id=None, name=None, enabled=None):
        ok = self._lexicon_store().update(id, name=name, enabled=enabled)
        return {"ok": ok}

    def lexicon_delete(self, id=None):
        try:
            self._lexicon_store().delete(id or "")
            return {"ok": True}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def lexicon_edit_words(self, id=None, add=None, remove=None):
        """Add and/or remove words on a *user* lexicon from the 查看/编辑 modal.
        ``add`` may be a list or pasted/newline text; ``remove`` is a list of
        exact words. Returns {ok, count}. Bumps the data revision so the share
        pie and the modal's own re-fetch see the change immediately."""
        from ..analysis import lexicon as lx
        store = self._lexicon_store()
        lex_id = (id or "").strip()
        try:
            if not store.is_editable(lex_id):
                raise ValueError("内置词库不可编辑。")
            current = list(store.words(lex_id))
            to_remove = set(remove or [])
            if to_remove:
                current = [w for w in current if w not in to_remove]
            if add is not None:
                added = lx.parse_items(add) if not isinstance(add, str) else lx.parse_words(add)
                seen = set(current)
                for w in added:
                    if w not in seen:
                        seen.add(w)
                        current.append(w)
            count = store.set_words(lex_id, current)
            try:
                self._db.revision += 1
            except Exception:
                pass
            return {"ok": True, "count": count}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def lexicon_import_file(self, path=None):
        """Import a dictionary file as a new lexicon (native open dialog, or
        ``path`` for the dev shim). Keeps the first column of each line so common
        jieba / Rime / Sogou exports adapt automatically."""
        from ..analysis import lexicon as lx
        if not path:
            picked = self._open_dialog([
                "词库文件 (*.txt;*.csv;*.dic)", "所有文件 (*.*)"])
            path = picked.get("path") or ""
        if not path:
            return {"ok": False, "cancelled": True}
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except OSError as exc:
            return {"ok": False, "error": f"无法读取文件：{exc}"}
        words = lx.parse_file_lines(content)
        if not words:
            return {"ok": False, "error": "文件里没有找到可用的词。"}
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            lex_id = self._lexicon_store().create(name, words)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "id": lex_id, "count": len(words)}

    def quote_seen(self, text=None):
        if isinstance(text, str) and text:
            try:
                self._db.record_quote_view(text)
            except Exception:
                pass
        return {"ok": True}

    # ---- binary: card image + native-save exports -----------------------
    def _long_card_data(self, period):
        """Enrich the report payload with the extra series the long share image
        needs (daily trend, top words, per-application breakdown)."""
        rep = dict(self._report_data(period))
        rg = self._config.run_gap_seconds
        since, until, _ps, _pe, _label = stats.report_bounds(period)
        rep["daily"] = stats.daily(self._db, since, until)
        rep["apps"] = [(stats.pretty_app(a), c)
                       for a, c in stats.per_app(self._db, since, 6, until)]
        rep["top_words"] = stats.top_words_daily(self._db, since, until, 12, rg)
        return rep

    def _render_card_image(self, period, template):
        from .. import cards
        if template == "long":
            return cards.render_long_card(self._long_card_data(period))
        return cards.render_card(self._report_data(period))

    def card_png(self, period="today", template="card"):
        buf = io.BytesIO()
        self._render_card_image(period, template).save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return "data:image/png;base64," + b64

    def save_card(self, period="today", template="card"):
        buf = io.BytesIO()
        self._render_card_image(period, template).save(buf, format="PNG")
        suffix = "_long" if template == "long" else ""
        return self._save_dialog(f"ducktype_{period}{suffix}.png", buf.getvalue(), binary=True)

    def export_sequence(self, fmt="txt", params: Optional[dict] = None):
        p = params or {}
        since, until = self._sequence_bounds(p)
        runs = list(reversed(stats.sequence_recent(
            self._db, since, self._config.run_gap_seconds, 10_000_000, until,
            p.get("apps", p.get("app", "")), p.get("q", ""))))
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

    def export_report_md(self, period="week", start=None, end=None):
        """Export the current report as a Markdown file (for pasting into a blog,
        Notion, weekly notes, etc.) via the native save dialog."""
        try:
            md = self._build_report_md(period, start, end)
        except Exception as exc:
            log.exception("build report markdown failed")
            return {"ok": False, "error": str(exc)}
        stamp = datetime.now().strftime("%Y%m%d")
        return self._save_dialog(f"ducktype_report_{period}_{stamp}.md",
                                 md.encode("utf-8"), binary=True)

    def _build_report_md(self, period, start, end) -> str:
        rg, sg = self._config.run_gap_seconds, self._config.session_gap_seconds
        fast = stats.report_fast(self._db, period, rg, sg, start, end)
        if period == "custom":
            since, until = stats.resolve_range("custom", start, end)
        else:
            since, until, _ps, _pe, _lbl = stats.report_bounds(period)
        top_chars = stats.top_chars(self._db, since, 15, until)
        top_words = stats.top_words(self._db, since, 15, rg, until)
        L = ["# DuckType · " + (fast.get("label") or "码字报告"), ""]
        if fast.get("narrative"):
            L += ["> " + fast["narrative"], ""]
        L += ["## 概览", ""]
        rows = [("上屏汉字", f"{fast.get('chars', 0):,} 字")]
        if fast.get("distinct_chars"):
            rows.append(("不同汉字", f"{fast['distinct_chars']:,} 个"))
        if fast.get("delta_pct") is not None:
            d = fast["delta_pct"]
            rows.append(("较上一周期", f"{'+' if d >= 0 else ''}{d}%"))
        if fast.get("active_days"):
            rows.append(("活跃天数", f"{fast['active_days']} 天"))
        pw = fast.get("peak_window")
        if pw:
            rows.append(("高产时段", f"{pw[2]} {pw[0]:02d}:00–{pw[1]:02d}:00"))
        if fast.get("top_app"):
            rows.append(("主力应用", f"{fast['top_app']} · {fast.get('top_app_share', 0)}%"))
        if fast.get("best_day"):
            rows.append(("最高产日", f"{fast['best_day']} · {fast.get('best_day_count', 0)} 字"))
        if fast.get("longest_session_min"):
            rows.append(("最长连续输入", f"{fast['longest_session_min']} 分钟"))
        L += ["| 指标 | 数值 |", "| --- | --- |"]
        L += [f"| {k} | {v} |" for k, v in rows]
        L.append("")
        insights = fast.get("insights") or []
        if insights:
            L += ["## 行为洞察", ""]
            for it in insights:
                L.append(f"- **{it.get('title', '洞察')}**：{it.get('body', '')}")
            L.append("")
        if top_words:
            L += ["## 高频词", ""]
            L += [f"{i + 1}. {w} （{c}）" for i, (w, c) in enumerate(top_words)]
            L.append("")
        if top_chars:
            L += ["## 高频字", "", "".join(c for c, _k in top_chars), ""]
        L += ["---", "", "由 DuckType · 码字鸭 生成 · 仅统计输入法真正上屏的中文汉字。"]
        return "\n".join(L)

    def save_png(self, name="chart.png", dataurl=""):
        """Save a chart canvas (sent as a data: URL) via the native save dialog.

        The dashboard's ⬇ buttons used an in-page ``<a download>`` click, which
        WebView2 silently ignores for data: URLs -- so the buttons did nothing in
        the native window. Decoding here and writing through ``_save_dialog`` gives
        the same OS save dialog the data export already uses."""
        import base64
        try:
            b64 = dataurl.split(",", 1)[1] if "," in dataurl else dataurl
            data = base64.b64decode(b64)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not data:
            return {"ok": False, "error": "empty image"}
        return self._save_dialog(name or "ducktype_chart.png", data, binary=True)

    def reveal_path(self, path=""):
        """Open the OS file browser with ``path`` selected (or its folder open).

        Backs the "下载完成" toast: clicking it reveals the saved file. On Windows
        ``explorer /select,`` highlights the file; we fall back to opening the
        containing directory if selection fails."""
        import os
        import subprocess
        try:
            path = os.path.normpath(str(path or ""))
            if not path or not os.path.exists(path):
                folder = os.path.dirname(path)
                if folder and os.path.isdir(folder):
                    os.startfile(folder)  # type: ignore[attr-defined]
                    return {"ok": True}
                return {"ok": False, "error": "path not found"}
            if os.name == "nt":
                # /select highlights the file in a new Explorer window.
                subprocess.Popen(["explorer", "/select,", path])
            else:
                os.startfile(os.path.dirname(path) or path)  # type: ignore[attr-defined]
            return {"ok": True}
        except Exception as exc:
            log.exception("reveal_path failed")
            return {"ok": False, "error": str(exc)}

    def _open_dialog(self, file_types=None):
        """Native open-file dialog (webview). Returns {"path": <chosen or "">}."""
        try:
            import webview
            win = self._window or (webview.windows[0] if webview.windows else None)
            if win is None:
                return {"path": ""}
            kwargs = {}
            if file_types:
                kwargs["file_types"] = tuple(file_types)
            res = win.create_file_dialog(webview.OPEN_DIALOG, **kwargs)
            if not res:
                return {"path": ""}
            return {"path": res[0] if isinstance(res, (list, tuple)) else res}
        except Exception as exc:
            log.exception("open dialog failed")
            return {"path": "", "error": str(exc)}

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
        """Toggle the frameless native window between normal and maximized.

        Uses the work-area resize in ``desktop`` (pywebview's frameless
        maximize/restore is unreliable on the WebView2 backend)."""
        try:
            from .. import desktop
            self._window_maximized = desktop.toggle_maximize()
        except Exception:
            log.exception("window_toggle_maximize failed")
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

    # ---- mini counter (floating always-on-top window, item 4) -----------
    def _r_mini_stats(self, p):
        return stats.mini_stats(self._db, self._config.session_gap_seconds,
                                self._config.daily_goal)

    def open_mini(self):
        """Hide the main window and open the always-on-top mini counter."""
        try:
            from .. import desktop
            self._window_maximized = False
            desktop.show_mini()
            return {"ok": True}
        except Exception as exc:
            log.exception("open_mini failed")
            return {"ok": False, "error": str(exc)}

    def mini_resize(self, w, h):
        """Resize the mini window (driven by the in-page corner grip)."""
        try:
            from .. import desktop
            return desktop.resize_mini(w, h)
        except Exception as exc:
            log.exception("mini_resize failed")
            return {"ok": False, "error": str(exc)}

    def close_mini(self):
        """Close the mini counter and bring the dashboard back."""
        try:
            from .. import desktop
            desktop.close_mini()
            return {"ok": True}
        except Exception as exc:
            log.exception("close_mini failed")
            return {"ok": False, "error": str(exc)}
