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
import threading
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from .. import autostart, updater
from ..analysis import segment, stats

log = logging.getLogger("ducktype")

# Read endpoints whose result depends only on the data revision (safe to cache).
# Excludes time-window queries and live status/progress.
_NO_CACHE = {"timeseries"}


class Relocator:
    """Moves the whole data root to a new folder on a background thread,
    reporting byte progress. copy -> verify -> mark old root for cleanup ->
    switch the pointer; the actual deletion of the old root happens on the next
    startup (so it survives a crash mid-move)."""

    def __init__(self, db):
        self.db = db
        self._lock = threading.Lock()
        self._state = {"phase": "idle", "done": 0, "total": 0,
                       "error": "", "db_path": ""}
        self._thread: Optional[threading.Thread] = None

    def progress(self) -> dict:
        with self._lock:
            return dict(self._state)

    def _set(self, **kw):
        with self._lock:
            self._state.update(kw)

    def start(self, target: Optional[str]) -> dict:
        from ..paths import root_dir
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return {"ok": False, "error": "正在移动中，请稍候。"}
        target = (target or "").strip()
        if not target:
            return {"ok": False, "error": "请选择目标文件夹。"}
        src = root_dir()
        dst = Path(target).expanduser()
        try:
            if dst.resolve() == src.resolve():
                return {"ok": False, "error": "目标位置与当前相同。"}
        except OSError:
            pass
        self._set(phase="copying", done=0, total=0, error="",
                  db_path=str(dst / "ducktype.db"))
        self._thread = threading.Thread(
            target=self._run, args=(src, dst), name="relocate", daemon=True)
        self._thread.start()
        return {"ok": True, "started": True, "restart_required": True,
                "db_path": str(dst / "ducktype.db")}

    def _run(self, src: Path, dst: Path):
        from .. import firstrun, paths
        try:
            dst.mkdir(parents=True, exist_ok=True)
            plan = firstrun.plan_files(src, dst, include_db=False, include_log=False)
            other_total = sum(s.stat().st_size for s, _ in plan if s.exists())
            db_size = (src / "ducktype.db").stat().st_size if (src / "ducktype.db").exists() else 0
            total = other_total + db_size
            self._set(total=total, done=0)

            self.db.backup_to(dst / "ducktype.db")
            self._set(done=db_size)

            firstrun.copy_files(
                plan, on_progress=lambda d, t: self._set(done=db_size + d))

            ok = firstrun.verify_files(plan) and (dst / "ducktype.db").stat().st_size > 0
            if not ok:
                self._set(phase="error", error="校验失败，已保留原数据，未切换位置。")
                return
            (dst / firstrun.CLEANUP_MARKER).write_text(str(src), encoding="utf-8")
            paths.write_pointer(str(dst))
            self._set(phase="done", done=total)
            log.info("Relocated data root %s -> %s (restart to apply)", src, dst)
        except Exception as exc:
            log.exception("Relocate failed")
            self._set(phase="error", error=str(exc))


class Api:
    """Bridge object exposed to the webview frontend (and reused by Flask)."""

    def __init__(self, db, config, status_fn=None, on_quit=None):
        self._db = db
        self._config = config
        self._status_fn = status_fn
        self._on_quit = on_quit
        self._relocator = Relocator(db)
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

    def _cached(self, endpoint: str, params: dict, fn):
        if endpoint in _NO_CACHE:
            return fn()
        rev = self._db.revision
        if rev != self._cache_rev:
            self._cache.clear()
            self._cache_rev = rev
        key = endpoint + "|" + json.dumps(params, sort_keys=True, ensure_ascii=False)
        if key in self._cache:
            return self._cache[key]
        val = fn()
        self._cache[key] = val
        return val

    # ---- read dispatch ---------------------------------------------------
    def get(self, endpoint: str, params: Optional[dict] = None):
        """Single entry point for all read endpoints used by the frontend."""
        p = params or {}
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
        return stats.report(self._db, p.get("period", "today"),
                            self._config.run_gap_seconds, self._config.session_gap_seconds)

    def _r_sequence(self, p):
        since, until = self._bounds(p)
        limit = int(p.get("limit", 200))
        return stats.sequence_recent(self._db, since, self._config.run_gap_seconds, limit, until)

    def _r_board(self, p):
        """Batched payload for the main board: one call instead of ~10."""
        since, until = self._bounds(p)
        rg, sg = self._config.run_gap_seconds, self._config.session_gap_seconds
        char_n, word_n = int(p.get("charN", 30)), int(p.get("wordN", 30))
        if since is not None or until is not None:
            wc, _wp, pc = segment.segment_range(self._db, since, rg, until)
            all_word_rows = sorted(
                ((w, c) for w, c in wc.items() if len(w) >= 2),
                key=lambda kv: kv[1], reverse=True
            )
            word_rows = all_word_rows[:word_n]
            topic_rows = all_word_rows[:30]
            coarse = {}
            for pos, cnt in pc.items():
                cid = stats.coarse_pos(pos)
                coarse[cid] = coarse.get(cid, 0) + cnt
            pos_rows = [(cid, stats.COARSE_LABELS.get(cid, cid), cnt)
                        for cid, cnt in coarse.items()]
            pos_rows.sort(key=lambda x: x[2], reverse=True)
        else:
            word_rows = stats.top_words(self._db, since, word_n, rg, until)
            pos_rows = stats.pos_distribution(self._db, since, rg, until)
            topic_rows = stats.topics(self._db, since, 30, until)
        return {
            "overview": stats.overview(self._db, since, rg, sg, until),
            "trend": stats.trend(self._db, since, until, rg, sg) or {},
            "daily": [{"date": d, "count": c} for d, c in stats.daily(self._db, since, until)],
            "top_chars": [{"ch": c, "count": k} for c, k in stats.top_chars(self._db, since, char_n, until)],
            "top_words": [{"word": w, "count": c} for w, c in word_rows],
            "pos": [{"pos": ps, "label": lbl, "count": c}
                    for ps, lbl, c in pos_rows],
            "apps": [{"app": a, "count": c} for a, c in stats.per_app(self._db, since, 12, until)],
            "heatmap": {"grid": stats.heatmap(self._db, since, until)},
            "topics": [{"word": w, "weight": round(float(wt), 4)}
                       for w, wt in topic_rows],
            "gamify": stats.gamify(self._db, self._config.daily_goal),
        }

    # ---- live status / updates (never cached) ---------------------------
    def status(self):
        return self._status_fn() if self._status_fn else {}

    def update_check(self):
        return updater.check()

    def update_apply(self):
        return updater.start_apply(self._on_quit)

    def update_progress(self):
        return updater.progress()

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
        rep = stats.report(self._db, period, self._config.run_gap_seconds,
                           self._config.session_gap_seconds)
        buf = io.BytesIO()
        cards.render_card(rep).save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return "data:image/png;base64," + b64

    def save_card(self, period="today"):
        from .. import cards
        rep = stats.report(self._db, period, self._config.run_gap_seconds,
                           self._config.session_gap_seconds)
        buf = io.BytesIO()
        cards.render_card(rep).save(buf, format="PNG")
        return self._save_dialog(f"ducktype_{period}.png", buf.getvalue(), binary=True)

    def export_sequence(self, fmt="txt", params: Optional[dict] = None):
        p = params or {}
        since, until = self._bounds(p)
        runs = list(reversed(stats.sequence_recent(
            self._db, since, self._config.run_gap_seconds, 10_000_000, until)))
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
