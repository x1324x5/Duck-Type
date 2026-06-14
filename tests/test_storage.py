"""Storage / data-management tests."""
import time

from ducktype.analysis import stats


def test_clear_all(db, insert_chars, insert_keys, now):
    insert_chars(db, [(now, "鸭", None)] * 5)
    insert_keys(db, [(now, "backspace", None)] * 2)
    deleted = db.clear_all()
    assert deleted == 5
    assert stats.total_chars(db, None) == 0
    assert db.stats_summary()["key_rows"] == 0


def test_delete_range(db, insert_chars, now):
    insert_chars(db, [(now - 100, "旧", None), (now, "新", None)])
    deleted = db.delete_range(None, now - 50)  # delete everything before now-50
    assert deleted == 1
    remaining = dict(stats.top_chars(db, None, 10))
    assert "新" in remaining and "旧" not in remaining


def test_purge_retention(db, insert_chars, now):
    old = now - 10 * 86400
    insert_chars(db, [(old, "旧", None), (now, "新", None)])
    removed = db.purge_retention(retention_days=7)
    assert removed == 1
    assert stats.total_chars(db, None) == 1


def test_purge_retention_zero_disabled(db, insert_chars, now):
    insert_chars(db, [(now - 999 * 86400, "古", None)])
    assert db.purge_retention(retention_days=0) == 0
    assert stats.total_chars(db, None) == 1


def test_summary_counts(db, insert_chars, now):
    insert_chars(db, [(now - 10, "a", None), (now, "b", None)])
    s = db.stats_summary()
    assert s["char_rows"] == 2
    assert s["first_ts"] == now - 10 and s["last_ts"] == now


def test_writer_thread_roundtrip(tmp_path):
    """The async writer path actually persists records."""
    from ducktype.storage import Database
    d = Database(tmp_path / "rt.db")
    d.start()
    try:
        for _ in range(5):
            d.record_char("鸭", "app.exe")
        d.record_key("backspace", "app.exe")
        # allow the batched writer to flush
        deadline = time.time() + 5
        while stats.total_chars(d, None) < 5 and time.time() < deadline:
            time.sleep(0.1)
    finally:
        d.stop()
    assert stats.total_chars(d, None) == 5
