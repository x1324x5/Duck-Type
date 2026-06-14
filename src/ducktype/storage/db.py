"""SQLite storage with a background batched writer.

Design notes:
  * Every committed Han character is stored as one row in ``char_events`` -- this
    *is* the typed sequence (ordered by ``ts``). Character frequency is just a
    GROUP BY over this table.
  * Control keys we care about (backspace/delete/enter) go to ``key_events`` and
    power the edit/deletion statistics.
  * Word- and POS-frequency are *materialized* incrementally from the character
    sequence by analysis.segment (so the dashboard stays fast). The cursor for
    that incremental build lives in ``meta``.
  * Writes are funneled through a single writer thread/queue, so the capture
    hooks never block on disk I/O and we sidestep cross-thread SQLite issues.
    Readers open their own short-lived connections (WAL mode allows concurrent
    reads during writes).
"""
from __future__ import annotations

import queue
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

_SCHEMA = """
CREATE TABLE IF NOT EXISTS char_events (
    id  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts  REAL NOT NULL,
    ch  TEXT NOT NULL,
    app TEXT
);
CREATE INDEX IF NOT EXISTS idx_char_ts ON char_events(ts);
CREATE INDEX IF NOT EXISTS idx_char_ch ON char_events(ch);

CREATE TABLE IF NOT EXISTS key_events (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts   REAL NOT NULL,
    kind TEXT NOT NULL,
    app  TEXT
);
CREATE INDEX IF NOT EXISTS idx_key_ts ON key_events(ts);

CREATE TABLE IF NOT EXISTS word_freq (
    word  TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0,
    pos   TEXT
);

CREATE TABLE IF NOT EXISTS pos_freq (
    pos   TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = str(path)
        self._q: "queue.Queue[Optional[Tuple]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._init_schema()

    # ---- setup -----------------------------------------------------------
    def _init_schema(self) -> None:
        con = self.connect()
        try:
            con.executescript(_SCHEMA)
            con.commit()
        finally:
            con.close()

    def connect(self) -> sqlite3.Connection:
        """Open a fresh connection (callers must close it). Safe for readers."""
        con = sqlite3.connect(self.path, timeout=30)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    # ---- writer thread ---------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._writer_loop, name="db-writer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._q.put(None)  # wake the writer
        if self._thread:
            self._thread.join(timeout=5)

    def _writer_loop(self) -> None:
        con = self.connect()
        chars: list = []
        keys: list = []
        last_flush = time.time()
        try:
            while not (self._stop.is_set() and self._q.empty()):
                try:
                    item = self._q.get(timeout=0.5)
                except queue.Empty:
                    item = None
                if item is not None:
                    kind = item[0]
                    if kind == "c":
                        chars.append(item[1:])
                    elif kind == "k":
                        keys.append(item[1:])
                now = time.time()
                if (chars or keys) and (len(chars) + len(keys) >= 64 or now - last_flush > 1.0):
                    self._flush(con, chars, keys)
                    chars, keys = [], []
                    last_flush = now
            self._flush(con, chars, keys)
        finally:
            con.close()

    @staticmethod
    def _flush(con: sqlite3.Connection, chars: list, keys: list) -> None:
        if chars:
            con.executemany("INSERT INTO char_events(ts, ch, app) VALUES (?,?,?)", chars)
        if keys:
            con.executemany("INSERT INTO key_events(ts, kind, app) VALUES (?,?,?)", keys)
        if chars or keys:
            con.commit()

    # ---- producer API (called from capture threads) ---------------------
    def record_char(self, ch: str, app: Optional[str], ts: Optional[float] = None) -> None:
        self._q.put(("c", ts if ts is not None else time.time(), ch, app))

    def record_key(self, kind: str, app: Optional[str], ts: Optional[float] = None) -> None:
        self._q.put(("k", ts if ts is not None else time.time(), kind, app))

    # ---- data management -------------------------------------------------
    def _reset_materialization(self, con: sqlite3.Connection) -> None:
        """Word/POS frequency tables are cumulative caches built from the event
        stream. After any deletion they would over-count, so we drop them and
        rewind the incremental cursor; they rebuild lazily on next read."""
        con.execute("DELETE FROM word_freq")
        con.execute("DELETE FROM pos_freq")
        con.execute(
            "INSERT INTO meta(key, value) VALUES ('word_cursor','0') "
            "ON CONFLICT(key) DO UPDATE SET value='0'"
        )

    def clear_all(self) -> int:
        """Delete every captured event. Returns the number of char rows removed."""
        con = self.connect()
        try:
            n = con.execute("SELECT COUNT(*) FROM char_events").fetchone()[0]
            con.execute("DELETE FROM char_events")
            con.execute("DELETE FROM key_events")
            self._reset_materialization(con)
            con.commit()
            con.execute("VACUUM")
            return n
        finally:
            con.close()

    def delete_range(self, start: Optional[float], end: Optional[float]) -> int:
        """Delete events with start <= ts < end (either bound may be None)."""
        clauses, params = [], []
        if start is not None:
            clauses.append("ts>=?"); params.append(start)
        if end is not None:
            clauses.append("ts<?"); params.append(end)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        con = self.connect()
        try:
            n = con.execute(
                f"SELECT COUNT(*) FROM char_events{where}", tuple(params)
            ).fetchone()[0]
            con.execute(f"DELETE FROM char_events{where}", tuple(params))
            con.execute(f"DELETE FROM key_events{where}", tuple(params))
            self._reset_materialization(con)
            con.commit()
            return n
        finally:
            con.close()

    def purge_retention(self, retention_days: int) -> int:
        """Delete events older than ``retention_days`` (0 disables purging)."""
        if not retention_days or retention_days <= 0:
            return 0
        cutoff = time.time() - retention_days * 86400
        return self.delete_range(None, cutoff)

    def stats_summary(self) -> dict:
        """Lightweight counts for the data-management UI."""
        con = self.connect()
        try:
            chars = con.execute("SELECT COUNT(*) FROM char_events").fetchone()[0]
            keys = con.execute("SELECT COUNT(*) FROM key_events").fetchone()[0]
            span = con.execute(
                "SELECT MIN(ts), MAX(ts) FROM char_events"
            ).fetchone()
        finally:
            con.close()
        return {"char_rows": chars, "key_rows": keys,
                "first_ts": span[0], "last_ts": span[1]}

    # ---- meta helpers ----------------------------------------------------
    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        con = self.connect()
        try:
            row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else default
        finally:
            con.close()

    def set_meta(self, key: str, value: str) -> None:
        con = self.connect()
        try:
            con.execute(
                "INSERT INTO meta(key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            con.commit()
        finally:
            con.close()
