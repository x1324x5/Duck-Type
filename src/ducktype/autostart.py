"""Toggle 'start on Windows login' via the HKCU Run registry key."""
from __future__ import annotations

import sys

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE = "DuckType"


def _command() -> str:
    """Best-effort command line that relaunches DuckType."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    # Running from source: python -m ducktype (relies on the package on sys.path)
    return f'"{sys.executable}" -m ducktype'


def is_enabled() -> bool:
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.QueryValueEx(k, _VALUE)
            return True
    except OSError:
        return False


def enable() -> None:
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
        winreg.SetValueEx(k, _VALUE, 0, winreg.REG_SZ, _command())


def disable() -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, _VALUE)
    except OSError:
        pass


def set_enabled(enabled: bool) -> None:
    if enabled:
        enable()
    else:
        disable()
