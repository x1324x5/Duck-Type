"""Tests for the 0.1.8 data-root pointer, path redirection, hook pruning, and
the updater environment scrub."""
import importlib
import os


def _fresh_paths(monkeypatch, appdata):
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.delenv("DUCKTYPE_DATA_DIR", raising=False)
    from ducktype import paths
    importlib.reload(paths)
    return paths


def test_root_falls_back_to_default_without_pointer(tmp_path, monkeypatch):
    paths = _fresh_paths(monkeypatch, tmp_path / "appdata")
    assert paths.root_dir() == paths.data_dir()
    assert paths.config_path().parent == paths.root_dir()
    assert paths.log_path().parent == paths.root_dir()
    assert paths.phrases_path().parent == paths.root_dir()


def test_pointer_round_trip(tmp_path, monkeypatch):
    paths = _fresh_paths(monkeypatch, tmp_path / "appdata")
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    paths.write_pointer(str(chosen))
    assert paths.read_pointer() == str(chosen)
    importlib.reload(paths)            # drop the cached root
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    assert paths.root_dir() == chosen
    assert paths.db_path() == chosen / "ducktype.db"
    assert paths.config_path() == chosen / "config.json"


def test_env_override_wins(tmp_path, monkeypatch):
    appdata = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(appdata))
    override = tmp_path / "override"
    monkeypatch.setenv("DUCKTYPE_DATA_DIR", str(override))
    from ducktype import paths
    importlib.reload(paths)
    assert paths.root_dir() == override
    assert override.exists()


def test_set_root_updates_and_clears_cache(tmp_path, monkeypatch):
    paths = _fresh_paths(monkeypatch, tmp_path / "appdata")
    new = tmp_path / "new"
    paths.set_root(new)
    assert paths.root_dir() == new
    assert paths.native_dir() == new / "native"


def test_prune_stale_hooks_keeps_current(tmp_path, monkeypatch):
    paths = _fresh_paths(monkeypatch, tmp_path / "appdata")
    nd = paths.native_dir()
    keep = nd / "ducktype_hook_aaaaaaaaaaaa.dll"
    keep.write_bytes(b"keep")
    for d in ("bbbbbbbbbbbb", "cccccccccccc", "dddddddddddd"):
        (nd / f"ducktype_hook_{d}.dll").write_bytes(b"old")
    removed = paths.prune_stale_hooks(keep=keep)
    assert removed == 3
    remaining = sorted(p.name for p in nd.glob("ducktype_hook_*.dll"))
    assert remaining == [keep.name]


def test_migration_copy_verify_delete(tmp_path):
    from ducktype import firstrun
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "native").mkdir(parents=True)
    (src / "ducktype.db").write_bytes(b"D" * 5000)
    (src / "config.json").write_text("{}", encoding="utf-8")
    (src / "phrases.txt").write_text("hi", encoding="utf-8")
    (src / "native" / "ducktype_hook_abc123abc123.dll").write_bytes(b"H" * 200)

    plan = firstrun.plan_files(src, dst)
    names = {s.name for s, _ in plan}
    assert "ducktype.db" in names and "config.json" in names
    assert "ducktype_hook_abc123abc123.dll" in names

    seen = []
    firstrun.copy_files(plan, on_progress=lambda d, t: seen.append((d, t)))
    assert firstrun.verify_files(plan)
    assert (dst / "ducktype.db").read_bytes() == b"D" * 5000
    assert (dst / "native" / "ducktype_hook_abc123abc123.dll").exists()
    assert seen and seen[-1][0] == seen[-1][1]  # ends at 100%

    firstrun.delete_files([s for s, _ in plan])
    assert not (src / "ducktype.db").exists()


def test_cleanup_old_root_after_relocation(tmp_path):
    from ducktype import firstrun
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    (old / "ducktype.db").write_bytes(b"x" * 100)
    (new / "ducktype.db").write_bytes(b"x" * 100)   # already migrated
    (new / firstrun.CLEANUP_MARKER).write_text(str(old), encoding="utf-8")

    firstrun.cleanup_old_root(new)
    assert not (old / "ducktype.db").exists()        # old data removed
    assert not (new / firstrun.CLEANUP_MARKER).exists()
    assert (new / "ducktype.db").exists()            # new data intact


def test_cleanup_old_root_keeps_data_if_unverified(tmp_path):
    from ducktype import firstrun
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir(); new.mkdir()
    (old / "ducktype.db").write_bytes(b"x" * 100)
    # new is missing the db -> verify fails -> old must be preserved
    (new / firstrun.CLEANUP_MARKER).write_text(str(old), encoding="utf-8")
    firstrun.cleanup_old_root(new)
    assert (old / "ducktype.db").exists()


def test_updater_clean_env_strips_pyinstaller_vars(monkeypatch):
    from ducktype import updater
    monkeypatch.setenv("_MEIPASS2", r"C:\Temp\_MEI363082")
    monkeypatch.setenv("_PYI_ARCHIVE_INDEX", "0")
    monkeypatch.setenv("DUCKTYPE_KEEP", "yes")
    env = updater._clean_env()
    assert "_MEIPASS2" not in env
    assert "_PYI_ARCHIVE_INDEX" not in env
    assert env.get("DUCKTYPE_KEEP") == "yes"


def test_updater_swap_script_waits_replaces_and_restarts(tmp_path, monkeypatch):
    from ducktype import updater

    monkeypatch.setattr(updater, "root_dir", lambda: tmp_path)
    new = r"C:\Users\me\AppData\Roaming\DuckType\DuckType-new.exe"
    cur = r"C:\Program Files\DuckType\DuckType.exe"

    bat = updater._write_swap_script(new, cur)
    script = (tmp_path / "_update.bat").read_text(encoding="ascii")

    assert bat == str(tmp_path / "_update.bat")
    assert f'move /Y "{new}" "{cur}"' in script
    assert f'start "" "{cur}"' in script
    assert "tasklist /FI" in script
    assert "if %tries% geq 30 goto run" in script
    assert 'del "%~f0"' in script
