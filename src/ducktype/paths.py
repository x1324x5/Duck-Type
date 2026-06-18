"""Filesystem locations used by DuckType.

All user-specific runtime files (database, config, logs, native hook copies,
phrases) live under a single **data root**. From 0.1.8 the user chooses that
root on first launch; the choice is recorded in a tiny pointer file
(``location.json``) that stays at the OS-default anchor so we can always find
the root again. Before a root has been chosen -- source/dev runs, the analysis
CLI, and tests -- everything falls back to the default anchor, preserving the
old behaviour.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

log = logging.getLogger("ducktype")

# Resolved data root, cached for the process. None until first resolved.
_root: Optional[Path] = None


def data_dir() -> Path:
    """The OS-default anchor directory (created if missing).

    This is *not* necessarily where data lives -- it only ever holds
    ``location.json`` (the pointer to the real data root). Kept stable so the
    pointer can always be found regardless of where the user moved their data.
    """
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = Path(base) / "DuckType"
    d.mkdir(parents=True, exist_ok=True)
    return d


def location_file() -> Path:
    """Pointer file recording the chosen data root (lives at the anchor)."""
    return data_dir() / "location.json"


def read_pointer() -> Optional[str]:
    """Return the stored data-root path, or None if unset/unreadable."""
    p = location_file()
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    val = raw.get("data_dir")
    return val if isinstance(val, str) and val.strip() else None


def write_pointer(path: Optional[str]) -> None:
    """Record (or clear, when given None/'') the chosen data root."""
    payload = {"data_dir": str(path) if path else ""}
    location_file().write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def root_dir() -> Path:
    """The active data root: where db/config/logs/native/phrases live.

    Resolution order (cached after the first call):
      1. ``DUCKTYPE_DATA_DIR`` env override (dev/tests/CI).
      2. The pointer file, if it names an existing directory.
      3. The default anchor (back-compat for unconfigured/source/CLI runs).
    """
    global _root
    if _root is not None:
        return _root

    override = os.environ.get("DUCKTYPE_DATA_DIR")
    if override:
        d = Path(override).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        _root = d
        return _root

    pointed = read_pointer()
    if pointed:
        d = Path(pointed).expanduser()
        if d.exists():
            _root = d
            return _root

    _root = data_dir()
    return _root


def set_root(path) -> Path:
    """Force the data root (used by the first-run bootstrap after the user
    picks a folder). Clears caches that derive from the root."""
    global _root
    d = Path(path).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    _root = d
    hook_dll_path.cache_clear()
    return _root


def db_path(data_dir_override: Optional[str] = None) -> Path:
    """Location of the SQLite database under the active root (or an override,
    used to preview a relocation target)."""
    if data_dir_override:
        base = Path(data_dir_override).expanduser()
        base.mkdir(parents=True, exist_ok=True)
        return base / "ducktype.db"
    return root_dir() / "ducktype.db"


def config_path() -> Path:
    return root_dir() / "config.json"


def log_path() -> Path:
    return root_dir() / "ducktype.log"


def phrases_path() -> Path:
    """User-editable file of fun/literary/trivia lines shown in the board ticker.

    Lives under the data root so it travels with the rest of the data; seeded
    with defaults on first use (see ``stats.load_phrases``).
    """
    return root_dir() / "phrases.txt"


def resource_dir() -> Path:
    """Directory that ships read-only bundled resources.

    When frozen by PyInstaller everything is unpacked under sys._MEIPASS.
    Otherwise resources sit next to the source tree.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent


def native_dir() -> Path:
    """Directory holding the stable, content-addressed hook DLL copies."""
    d = root_dir() / "native"
    d.mkdir(parents=True, exist_ok=True)
    return d


@lru_cache(maxsize=1)
def hook_dll_path() -> Path:
    """Location of the native hook DLL used for injection.

    PyInstaller one-file apps unpack bundled binaries under a random _MEI...
    directory on each launch. Injecting that path into long-lived apps is bad:
    the DLL is pinned there, so the temp dir cannot be deleted, and later
    launches inject additional copies. For frozen builds we copy the bundled DLL
    to a stable, content-addressed path under the data root and inject that.
    """
    bundled = resource_dir() / "native" / "ducktype_hook.dll"
    if not getattr(sys, "frozen", False) or not bundled.exists():
        return bundled
    return _stable_hook_dll_path(bundled)


def _stable_hook_dll_path(bundled: Path) -> Path:
    digest = hashlib.sha256(bundled.read_bytes()).hexdigest()[:12]
    dst = native_dir() / f"ducktype_hook_{digest}.dll"
    if not dst.exists() or dst.stat().st_size != bundled.stat().st_size:
        shutil.copy2(bundled, dst)
    return dst


def prune_stale_hooks(keep: Optional[Path] = None) -> int:
    """Delete every ``ducktype_hook_*.dll`` in the native dir except ``keep``.

    Old content-addressed copies accumulate across rebuilds/updates. A copy that
    is still pinned inside a running host process can't be deleted yet -- those
    OSErrors are swallowed and the file is retried on a later launch. Returns the
    number of files actually removed.
    """
    nd = root_dir() / "native"
    if not nd.is_dir():
        return 0
    keep_name = Path(keep).name if keep else None
    removed = 0
    for f in nd.glob("ducktype_hook_*.dll"):
        if keep_name and f.name == keep_name:
            continue
        try:
            f.unlink()
            removed += 1
        except OSError:
            # Still loaded/pinned somewhere; leave it for next time.
            pass
    if removed:
        log.info("Pruned %d stale hook DLL(s) from %s", removed, nd)
    return removed


def icon_path() -> Path:
    """Location of the bundled application icon (.ico)."""
    return resource_dir() / "assets" / "duck.ico"


def icon_png_path() -> Path:
    """Location of the bundled application icon PNG."""
    return resource_dir() / "assets" / "duck.png"


def extra_assets_dir() -> Path:
    """Additional dashboard artwork shipped from the repository's assets dir."""
    if getattr(sys, "frozen", False):
        return resource_dir() / "extra_assets"
    return Path(__file__).resolve().parents[2] / "assets"
