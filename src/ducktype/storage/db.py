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

from datetime import datetime
import hashlib
import logging
import os
import queue
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger("ducktype")


def quote_hash(text: str) -> str:
    """Stable short hash of a ticker line, used to count quote views without
    storing the text itself."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS char_events (
    id  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts  REAL NOT NULL,
    ch  TEXT NOT NULL,
    app TEXT
);
CREATE INDEX IF NOT EXISTS idx_char_ts ON char_events(ts);
CREATE INDEX IF NOT EXISTS idx_char_ch ON char_events(ch);
CREATE INDEX IF NOT EXISTS idx_char_ts_ch ON char_events(ts, ch);
CREATE INDEX IF NOT EXISTS idx_char_ts_app ON char_events(ts, app);
CREATE INDEX IF NOT EXISTS idx_char_app_ts ON char_events(app, ts);

CREATE TABLE IF NOT EXISTS key_events (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts   REAL NOT NULL,
    kind TEXT NOT NULL,
    app  TEXT
);
CREATE INDEX IF NOT EXISTS idx_key_ts ON key_events(ts);
CREATE INDEX IF NOT EXISTS idx_key_ts_kind ON key_events(ts, kind);

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

CREATE TABLE IF NOT EXISTS quote_views (
    quote_hash TEXT PRIMARY KEY,
    count      INTEGER NOT NULL DEFAULT 0,
    first_ts   REAL,
    last_ts    REAL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = str(path)
        self._q: "queue.Queue[Optional[Tuple]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        # Set True if the database file vanishes *after* startup and connect()
        # has to rebuild it (i.e. the user deleted it). Creating the file on the
        # very first launch is normal and must NOT trip this flag, so we clear it
        # once the initial schema setup is done.
        self.recreated = False
        self._initialized = False
        # Monotonic data-version counter: bumped whenever captured rows change.
        # Read-side callers (the dashboard Api) use it as a cache key so repeated
        # queries between writes are served from cache. Exact value is irrelevant
        # -- only that it changes -- so a plain int (GIL-atomic +=) is fine.
        self.revision = 0
        self._init_schema()
        self._initialized = True
        self.recreated = False

    # ---- setup -----------------------------------------------------------
    def _init_schema(self) -> None:
        try:
            con = self.connect()
        except sqlite3.DatabaseError as exc:
            if not self._looks_corrupt(exc):
                raise
            self._quarantine_corrupt_files(exc)
            con = self.connect()
        try:
            con.executescript(_SCHEMA)
            con.commit()
        except sqlite3.DatabaseError as exc:
            con.close()
            if not self._looks_corrupt(exc):
                raise
            self._quarantine_corrupt_files(exc)
            con = self.connect()
            try:
                con.executescript(_SCHEMA)
                con.commit()
            finally:
                con.close()
            return
        finally:
            con.close()

    def connect(self) -> sqlite3.Connection:
        """Open a fresh connection (callers must close it). Safe for readers.

        Self-heals if the database file has gone missing: ``sqlite3.connect``
        would otherwise create an *empty* file with no tables, so every later
        query would fail with "no such table" and the dashboard would silently
        show nothing. If the file is absent we recreate the schema and flag it so
        the UI can warn that data may have been lost.
        """
        missing = not os.path.exists(self.path)
        con = sqlite3.connect(self.path, timeout=30)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            if missing:
                con.executescript(_SCHEMA)
                con.commit()
                # Creating the file on first launch is normal; only treat a
                # disappearance *after* startup as a (reportable) data loss.
                if self._initialized:
                    self.recreated = True
                    log.warning(
                        "database file %s was missing; recreated an empty one",
                        self.path,
                    )
            return con
        except Exception:
            con.close()
            raise

    @staticmethod
    def _looks_corrupt(exc: sqlite3.DatabaseError) -> bool:
        msg = str(exc).lower()
        return "malformed" in msg or "not a database" in msg

    def _quarantine_corrupt_files(self, exc: sqlite3.DatabaseError) -> None:
        """Move a broken SQLite database aside and allow startup with a fresh DB.

        The old files are kept next to the original database for possible manual
        recovery. WAL/SHM sidecar files must move with it; otherwise SQLite may
        keep seeing the same broken state on the next open.
        """
        db_path = Path(self.path)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        moved = []
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(db_path) + suffix)
            if not src.exists():
                continue
            dst = src.with_name(f"{src.name}.corrupt_{stamp}")
            idx = 1
            while dst.exists():
                dst = src.with_name(f"{src.name}.corrupt_{stamp}_{idx}")
                idx += 1
            src.rename(dst)
            moved.append(str(dst))
        log.error(
            "SQLite database is corrupt (%s); moved old files aside: %s",
            exc,
            ", ".join(moved) if moved else "(no files found)",
        )

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
                    self.revision += 1
                    chars, keys = [], []
                    last_flush = now
            if chars or keys:
                self._flush(con, chars, keys)
                self.revision += 1
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

    # ---- ticker quote views (for quote-themed achievements) -------------
    def record_quote_view(self, text: str) -> None:
        """Count one viewing of a ticker line. Low-frequency (one per rotation),
        so written directly rather than through the batched writer queue."""
        h = quote_hash(text)
        now = time.time()
        con = self.connect()
        try:
            con.execute(
                "INSERT INTO quote_views(quote_hash, count, first_ts, last_ts) "
                "VALUES (?, 1, ?, ?) "
                "ON CONFLICT(quote_hash) DO UPDATE SET "
                "count = count + 1, last_ts = excluded.last_ts",
                (h, now, now),
            )
            con.commit()
            self.revision += 1
        finally:
            con.close()

    def quote_stats(self, egg_hash: Optional[str] = None) -> Tuple[int, int, bool]:
        """Return (distinct quotes seen, total views, whether the egg was seen).

        Resilient to an old database that predates the ``quote_views`` table."""
        con = self.connect()
        try:
            row = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(count), 0) FROM quote_views"
            ).fetchone()
            distinct, total = int(row[0]), int(row[1])
            egg = False
            if egg_hash is not None:
                egg = con.execute(
                    "SELECT 1 FROM quote_views WHERE quote_hash = ?", (egg_hash,)
                ).fetchone() is not None
            return distinct, total, egg
        except sqlite3.OperationalError:
            return 0, 0, False
        finally:
            con.close()

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
            self.revision += 1
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
            self.revision += 1
            return n
        finally:
            con.close()

    def purge_retention(self, retention_days: int) -> int:
        """Delete events older than ``retention_days`` (0 disables purging)."""
        if not retention_days or retention_days <= 0:
            return 0
        cutoff = time.time() - retention_days * 86400
        return self.delete_range(None, cutoff)

    def backup_to(self, dest_path) -> None:
        """Copy the live database to ``dest_path`` using SQLite's online backup
        (consistent even while the writer thread is active). Used to move the
        data to a new location without losing anything."""
        src = self.connect()
        try:
            dst = sqlite3.connect(str(dest_path))
            try:
                src.backup(dst)
                dst.commit()
            finally:
                dst.close()
        finally:
            src.close()

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
