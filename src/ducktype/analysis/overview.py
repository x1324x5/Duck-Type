"""The one-shot board overview header (totals, speed, edit ratio)."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from .statutil import _where
from .edit_stats import edits, efficiency


def overview(db, since: Optional[float], run_gap: float, session_gap: float,
             until: Optional[float] = None) -> Dict:
    e = edits(db, since, until, session_gap)
    eff = efficiency(db, since, session_gap, until)
    w, p = _where(since, until)
    con = db.connect()
    try:
        distinct = con.execute(
            f"SELECT COUNT(DISTINCT ch) FROM char_events{w}", p
        ).fetchone()[0]
        first_ts = con.execute("SELECT MIN(ts) FROM char_events").fetchone()[0]
    finally:
        con.close()
    return {
        "total_chars": e["chars"],
        "distinct_chars": distinct,
        "edits": e["edits"],
        "edit_ratio": e["edit_ratio"],
        "backspace": e["backspace"],
        "delete": e["delete"],
        "cpm": eff["cpm"],
        "peak_cpm": eff["peak_cpm"],
        "active_minutes": eff["active_minutes"],
        "sessions": eff["sessions"],
        "tracking_since": (
            datetime.fromtimestamp(first_ts).strftime("%Y-%m-%d") if first_ts else None
        ),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
