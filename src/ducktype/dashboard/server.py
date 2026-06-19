"""Dev/preview Flask shim around the in-process :class:`~ducktype.dashboard.api.Api`.

The packaged app does NOT start this server -- it talks to ``Api`` directly over
the pywebview bridge (no port). This module exists only so the dashboard can be
opened in a regular browser during development (``_preview_server.py``) and so
the existing HTTP-based tests keep working. Every route is a thin delegate to an
``Api`` method, which is the single source of truth.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.serving import make_server

from ..analysis import stats
from .api import Api

log = logging.getLogger("ducktype")

_STATIC = Path(__file__).resolve().parent / "static"


def create_app(db, config, status_fn=None, on_quit=None) -> Flask:
    app = Flask(__name__, static_folder=None)
    api = Api(db, config, status_fn, on_quit)

    @app.route("/")
    def index():
        return send_from_directory(_STATIC, "index.html")

    # ---- live status / updates -----------------------------------------
    @app.route("/api/status")
    def api_status():
        return jsonify(api.status())

    @app.route("/api/update/check")
    def api_update_check():
        return jsonify(api.update_check())

    @app.route("/api/update/apply", methods=["POST"])
    def api_update_apply():
        return jsonify(api.update_apply())

    @app.route("/api/update/progress")
    def api_update_progress():
        return jsonify(api.update_progress())

    # ---- on-demand full report -----------------------------------------
    @app.route("/api/report/generate", methods=["POST"])
    def api_report_generate():
        return jsonify(api.report_generate(request.get_json(force=True, silent=True) or {}))

    @app.route("/api/report/progress")
    def api_report_progress():
        return jsonify(api.report_progress())

    # ---- config ---------------------------------------------------------
    @app.route("/api/config", methods=["GET"])
    def api_config_get():
        return jsonify(api.config_get())

    @app.route("/api/config", methods=["POST"])
    def api_config_set():
        return jsonify(api.config_set(request.get_json(force=True, silent=True) or {}))

    # ---- data management ------------------------------------------------
    @app.route("/api/data/summary")
    def api_data_summary():
        return jsonify(api.data_summary())

    @app.route("/api/data/pick_dir", methods=["POST"])
    def api_data_pick_dir():
        return jsonify(api.data_pick_dir())

    @app.route("/api/data/relocate", methods=["POST"])
    def api_data_relocate():
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(api.data_relocate(body.get("dir")))

    @app.route("/api/data/relocate/progress")
    def api_data_relocate_progress():
        return jsonify(api.data_relocate_progress())

    @app.route("/api/data/clear", methods=["POST"])
    def api_data_clear():
        return jsonify(api.data_clear())

    @app.route("/api/data/delete", methods=["POST"])
    def api_data_delete():
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(api.data_delete(body.get("start"), body.get("end")))

    @app.route("/api/quote_seen", methods=["POST"])
    def api_quote_seen():
        data = request.get_json(silent=True) or {}
        return jsonify(api.quote_seen(data.get("text")))

    # ---- card image (browser variant returns a PNG response) -----------
    @app.route("/api/card")
    def api_card():
        period = request.args.get("period", "today")
        data_uri = api.card_png(period)
        raw = base64.b64decode(data_uri.split(",", 1)[1])
        return Response(raw, mimetype="image/png",
                        headers={"Content-Disposition":
                                 f'attachment; filename="ducktype_{period}.png"'})

    # ---- sequence export (browser variant streams the file) ------------
    @app.route("/api/export/sequence.<fmt>")
    def api_export_sequence(fmt):
        day = (request.args.get("day") or "").strip()
        if day:
            start = datetime.strptime(day, "%Y-%m-%d")
            since, until = start.timestamp(), (start + timedelta(days=1)).timestamp()
        else:
            since, until = stats.resolve_range(
                request.args.get("range", "7d"),
                request.args.get("start"), request.args.get("end"))
        runs = list(reversed(stats.sequence_recent(
            db, since, config.run_gap_seconds, 10_000_000, until,
            request.args.get("app", ""))))
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if fmt == "txt":
            body, mime = "\n".join(r["text"] for r in runs), "text/plain"
        elif fmt == "json":
            body, mime = json.dumps(runs, ensure_ascii=False, indent=2), "application/json"
        elif fmt == "csv":
            buf = io.StringIO(); buf.write("﻿time,app,text\n")
            for r in runs:
                t = datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S") if r["ts"] else ""
                text = '"' + r["text"].replace('"', '""') + '"'
                buf.write(f"{t},{r['app'] or ''},{text}\n")
            body, mime = buf.getvalue(), "text/csv"
        else:
            return jsonify({"error": "unknown format"}), 400
        return Response(body, mimetype=mime,
                        headers={"Content-Disposition":
                                 f'attachment; filename="ducktype_sequence_{stamp}.{fmt}"'})

    # ---- generic read passthrough (overview/top_chars/board/...) -------
    @app.route("/api/<endpoint>")
    def api_read(endpoint):
        return jsonify(api.get(endpoint, dict(request.args)))

    # ---- bundled static assets (chart.umd.min.js, duck.png, ducks/*) ---
    # Mirrors the native file:// layout so relative asset paths resolve in the
    # browser too. Registered last; /api/* and / win by specificity.
    @app.route("/<path:f>")
    def static_files(f):
        return send_from_directory(_STATIC, f)

    return app


class DashboardServer:
    """Browser-served dashboard for development only. The packaged app uses the
    native window (see ``desktop.py``) instead and never starts this."""
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
                    self.config.dashboard_host, port, self._app, threaded=True)
                self._bound_port = port
                break
            except OSError as exc:
                last_err = exc
        if self._server is None:
            raise RuntimeError(
                f"Could not bind dashboard to any port near {self.config.dashboard_port}"
            ) from last_err
        if self._bound_port != self.config.dashboard_port:
            log.warning("Port %d busy; dashboard moved to %d",
                        self.config.dashboard_port, self._bound_port)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="dashboard", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if (self._thread is not None and self._thread.is_alive()
                and threading.current_thread() is not self._thread):
            self._thread.join(timeout=5)
