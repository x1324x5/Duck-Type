"""Filesystem locations used by DuckType.

Everything user-specific lives under %APPDATA%\\DuckType so the program works
identically whether run from source or from a packaged .exe.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional


def data_dir() -> Path:
    """Default directory for config and logs (created if missing).

    Config and logs always live here even when the database is relocated, so the
    program can always find its settings to know where the database went.
    """
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = Path(base) / "DuckType"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path(data_dir_override: Optional[str] = None) -> Path:
    """Location of the SQLite database.

    With an override (the user-chosen data directory) the database lives there;
    otherwise it sits in the default app directory.
    """
    if data_dir_override:
        base = Path(data_dir_override).expanduser()
        base.mkdir(parents=True, exist_ok=True)
        return base / "ducktype.db"
    return data_dir() / "ducktype.db"


def config_path() -> Path:
    return data_dir() / "config.json"


def log_path() -> Path:
    return data_dir() / "ducktype.log"


def resource_dir() -> Path:
    """Directory that ships read-only bundled resources.

    When frozen by PyInstaller everything is unpacked under sys._MEIPASS.
    Otherwise resources sit next to the source tree.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def hook_dll_path() -> Path:
    """Location of the native hook DLL used for injection.

    PyInstaller one-file apps unpack bundled binaries under a random _MEI...
    directory on each launch. Injecting that path into long-lived apps is bad:
    the DLL is pinned there, so the temp dir cannot be deleted, and later
    launches inject additional copies. For frozen builds we copy the bundled DLL
    to a stable, content-addressed path under %APPDATA% and inject that instead.
    """
    bundled = resource_dir() / "native" / "ducktype_hook.dll"
    if not getattr(sys, "frozen", False) or not bundled.exists():
        return bundled
    return _stable_hook_dll_path(bundled)


def _stable_hook_dll_path(bundled: Path) -> Path:
    digest = hashlib.sha256(bundled.read_bytes()).hexdigest()[:12]
    native_dir = data_dir() / "native"
    native_dir.mkdir(parents=True, exist_ok=True)
    dst = native_dir / f"ducktype_hook_{digest}.dll"
    if not dst.exists() or dst.stat().st_size != bundled.stat().st_size:
        shutil.copy2(bundled, dst)
    return dst


def icon_path() -> Path:
    """Location of the bundled application icon (.ico)."""
    return resource_dir() / "assets" / "duck.ico"


def icon_png_path() -> Path:
    """Location of the bundled application icon PNG."""
    return resource_dir() / "assets" / "duck.png"
