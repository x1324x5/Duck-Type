"""Make the ``src`` layout importable and provide a populated temp database."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ducktype.storage import Database  # noqa: E402


def _insert_chars(db, rows):
    """rows: iterable of (ts, ch, app). Writes directly (no async writer)."""
    con = db.connect()
    try:
        con.executemany("INSERT INTO char_events(ts, ch, app) VALUES (?,?,?)", rows)
        con.commit()
    finally:
        con.close()


def _insert_keys(db, rows):
    con = db.connect()
    try:
        con.executemany("INSERT INTO key_events(ts, kind, app) VALUES (?,?,?)", rows)
        con.commit()
    finally:
        con.close()


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture
def insert_chars():
    return _insert_chars


@pytest.fixture
def insert_keys():
    return _insert_keys


@pytest.fixture
def now():
    return time.time()
