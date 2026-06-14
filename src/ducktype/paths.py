"""Filesystem locations used by DuckType.

Everything user-specific lives under %APPDATA%\\DuckType so the program works
identically whether run from source or from a packaged .exe.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    """Directory for the database, config and logs (created if missing)."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = Path(base) / "DuckType"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
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


def hook_dll_path() -> Path:
    """Location of the native hook DLL (ducktype_hook.dll)."""
    return resource_dir() / "native" / "ducktype_hook.dll"


def icon_path() -> Path:
    """Location of the bundled application icon (.ico)."""
    return resource_dir() / "assets" / "duck.ico"
