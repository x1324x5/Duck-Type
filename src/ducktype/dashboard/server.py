"""Flask + werkzeug dashboard server, runnable on a background thread.

Serves a single-page UI (static/index.html) plus a small JSON API. All numbers
are derived on demand from the SQLite database, so the page always reflects the
latest captured data when reloaded. The settings + data-management endpoints let
the page edit config.json and prune data without touching the filesystem.
"""
from __future__ import annotations

import io
import json
import logging
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.serving import make_server

from .. import autostart
from ..analysis import stats

log = logging.getLogger("ducktype")

_STATIC = Path(__file__).resolve().parent / "static"


def create_app(db, config, status_fn=None, on_quit=None) -> Flask:
    app = Flask(__name__, static_folder=None)

    def _bounds():
        return stats.resolve_range(
            request.args.get("range", "7d"),
            request.args.get("start"),
            request.args.get("end"),
        )

    @app.route("/")
    def index():
        return send_from_directory(_STATIC, "index.html")

    @app.route("/favicon.png")
    def favicon():
        from ..branding import app_image
        buf = io.BytesIO()
        app_image(64, active=True).save(buf, format="PNG")
        return Response(buf.getvalue(), mimetype="image/png")

    @app.route("/api/status")
    def api_status():
        return jsonify(status_fn() if status_fn else {})

    @app.route("/api/update/check")
    def api_update_check():
        from .. import updater
        return jsonify(updater.check())

    @app.route("/api/update/apply", methods=["POST"])
    def api_update_apply():
        from .. import updater
        res = updater.apply()
        if res.get("pending") and on_quit:
            # Respond first, then quit so the swap-on-exit script can run.
            threading.Timer(1.5, on_quit).start()
        return jsonify(res)

    # ---- read-only analytics -------------------------------------------
    @app.route("/api/overview")
    def api_overview():
        since, until = _bounds()
        return jsonify(stats.overview(
            db, since, config.run_gap_seconds, config.session_gap_seconds, until))

    @app.route("/api/top_chars")
    def api_top_chars():
        since, until = _bounds()
        n = int(request.args.get("n", 50))
        return jsonify([{"ch": c, "count": k}
                        for c, k in stats.top_chars(db, since, n, until)])

    @app.route("/api/top_words")
    def api_top_words():
        since, until = _bounds()
        n = int(request.args.get("n", 50))
        rows = stats.top_words(db, since, n, config.run_gap_seconds, until)
        return jsonify([{"word": w, "count": c} for w, c in rows])

    @app.route("/api/pos")
    def api_pos():
        since, until = _bounds()
        rows = stats.pos_distribution(db, since, config.run_gap_seconds, until)
        return jsonify([{"pos": p, "label": lbl, "count": c} for p, lbl, c in rows])

    @app.route("/api/topics")
    def api_topics():
        since, until = _bounds()
        rows = stats.topics(db, since, int(request.args.get("n", 30)), until)
        return jsonify([{"word": w, "weight": round(float(wt), 4)} for w, wt in rows])

    @app.route("/api/heatmap")
    def api_heatmap():
        since, until = _bounds()
        return jsonify({"grid": stats.heatmap(db, since, until)})

    @app.route("/api/daily")
    def api_daily():
        since, until = _bounds()
        return jsonify([{"date": d, "count": c} for d, c in stats.daily(db, since, until)])

    @app.route("/api/apps")
    def api_apps():
        since, until = _bounds()
        n = int(request.args.get("n", 20))
        return jsonify([{"app": a, "count": c} for a, c in stats.per_app(db, since, n, until)])

    @app.route("/api/edits")
    def api_edits():
        since, until = _bounds()
        return jsonify(stats.edits(db, since, until, config.session_gap_seconds))

    @app.route("/api/trend")
    def api_trend():
        since, until = _bounds()
        return jsonify(stats.trend(
            db, since, until, config.run_gap_seconds, config.session_gap_seconds) or {})

    @app.route("/api/fun")
    def api_fun():
        since, until = _bounds()
        return jsonify(stats.fun_rankings(db, since, config.run_gap_seconds, until))

    @app.route("/api/gamify")
    def api_gamify():
        return jsonify(stats.gamify(db, config.daily_goal))

    @app.route("/api/report")
    def api_report():
        period = request.args.get("period", "today")
        return jsonify(stats.report(
            db, period, config.run_gap_seconds, config.session_gap_seconds))

    @app.route("/api/card")
    def api_card():
        from .. import cards
        period = request.args.get("period", "today")
        rep = stats.report(
            db, period, config.run_gap_seconds, config.session_gap_seconds)
        buf = io.BytesIO()
        cards.render_card(rep).save(buf, format="PNG")
        return Response(
            buf.getvalue(), mimetype="image/png",
            headers={"Content-Disposition":
                     f'attachment; filename="ducktype_{period}.png"'},
        )

    # ---- committed-character sequence ----------------------------------
    @app.route("/api/sequence")
    def api_sequence():
        since, until = _bounds()
        limit = int(request.args.get("limit", 200))
        return jsonify(stats.sequence_recent(
            db, since, config.run_gap_seconds, limit, until))

    @app.route("/api/export/sequence.<fmt>")
    def api_export_sequence(fmt):
        since, until = _bounds()
        runs = stats.sequence_recent(db, since, config.run_gap_seconds, 10_000_000, until)
        runs = list(reversed(runs))  # chronological for export
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if fmt == "txt":
            body = "\n".join(r["text"] for r in runs)
            mime = "text/plain"
        elif fmt == "json":
            body = json.dumps(runs, ensure_ascii=False, indent=2)
            mime = "application/json"
        elif fmt == "csv":
            buf = io.StringIO()
            buf.write("﻿time,app,text\n")  # BOM so Excel reads UTF-8
            for r in runs:
                t = datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S") if r["ts"] else ""
                text = '"' + r["text"].replace('"', '""') + '"'
                buf.write(f"{t},{r['app'] or ''},{text}\n")
            body = buf.getvalue()
            mime = "text/csv"
        else:
            return jsonify({"error": "unknown format"}), 400
        return Response(
            body, mimetype=mime,
            headers={"Content-Disposition": f'attachment; filename="ducktype_sequence_{stamp}.{fmt}"'},
        )

    # ---- settings -------------------------------------------------------
    @app.route("/api/config", methods=["GET"])
    def api_config_get():
        data = {k: v for k, v in asdict(config).items() if not k.startswith("_")}
        data["editable"] = list(config.EDITABLE)
        data["restart_required"] = list(config.RESTART_REQUIRED)
        return jsonify(data)

    @app.route("/api/config", methods=["POST"])
    def api_config_set():
        updates = request.get_json(force=True, silent=True) or {}
        had_autostart = "autostart" in updates
        restart = config.apply(updates)
        if had_autostart:
            try:
                autostart.set_enabled(config.autostart)
            except Exception:
                log.exception("Failed to toggle autostart from dashboard")
        return jsonify({"ok": True, "restart_required": restart})

    # ---- data management ------------------------------------------------
    @app.route("/api/data/summary")
    def api_data_summary():
        return jsonify(db.stats_summary())

    @app.route("/api/data/clear", methods=["POST"])
    def api_data_clear():
        return jsonify({"deleted": db.clear_all()})

    @app.route("/api/data/delete", methods=["POST"])
    def api_data_delete():
        body = request.get_json(force=True, silent=True) or {}
        since, until = stats.resolve_range(
            "custom", body.get("start"), body.get("end"))
        return jsonify({"deleted": db.delete_range(since, until)})

    return app


class DashboardServer:
    # Try up to this many consecutive ports if the configured one is taken.
    _PORT_TRIES = 10

    def __init__(self, db, config, status_fn=None, on_quit=None):
        self.config = config
        self._app = create_app(db, config, status_fn, on_quit)
        self._server = None
        self._thread: Optional[threading.Thread] = None
        self._bound_port = config.dashboard_port

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.config.dashboard_host == "0.0.0.0" else self.config.dashboard_host
        return f"http://{host}:{self._bound_port}/"

    def start(self) -> None:
        last_err = None
        for offset in range(self._PORT_TRIES):
            port = self.config.dashboard_port + offset
            try:
                self._server = make_server(
                    self.config.dashboard_host, port, self._app, threaded=True
                )
                self._bound_port = port
                break
            except OSError as exc:  # port already in use
                last_err = exc
        if self._server is None:
            raise RuntimeError(
                f"Could not bind dashboard to any port near {self.config.dashboard_port}"
            ) from last_err
        if self._bound_port != self.config.dashboard_port:
            log.warning("Port %d busy; dashboard moved to %d",
                        self.config.dashboard_port, self._bound_port)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="dashboard", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if (
            self._thread is not None
            and self._thread.is_alive()
            and threading.current_thread() is not self._thread
        ):
            self._thread.join(timeout=5)
