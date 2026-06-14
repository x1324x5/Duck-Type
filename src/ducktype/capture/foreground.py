"""Track the foreground application and detect password input fields.

This module is Windows-specific but imports lazily so the rest of the package
can be imported (e.g. for analysis-only use) on any platform.
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes
from typing import Optional

# ---- Win32 plumbing ------------------------------------------------------
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GWL_STYLE = -16
ES_PASSWORD = 0x0020
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
_user32.GetGUIThreadInfo.restype = wintypes.BOOL
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetClassNameW.restype = ctypes.c_int

# GetWindowLongPtrW exists only on 64-bit; fall back to GetWindowLongW.
_GetWindowLong = getattr(_user32, "GetWindowLongPtrW", _user32.GetWindowLongW)
_GetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int]
_GetWindowLong.restype = ctypes.c_ssize_t

_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
]
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def _process_name(pid: int) -> Optional[str]:
    if not pid:
        return None
    h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        size = wintypes.DWORD(260)
        buf = ctypes.create_unicode_buffer(size.value)
        if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
    finally:
        _kernel32.CloseHandle(h)
    return None


def foreground_app() -> Optional[str]:
    """Return the executable name (e.g. 'WINWORD.EXE') of the focused window."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return _process_name(pid.value)


def focused_is_password() -> bool:
    """True if the control with keyboard focus is a classic password edit.

    Note: this detects standard Win32 password fields. Password inputs inside
    browsers / Electron apps are not Win32 controls and cannot be detected this
    way -- use the app blacklist for those.
    """
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return False
    tid = _user32.GetWindowThreadProcessId(hwnd, None)
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not _user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
        return False
    focus = info.hwndFocus
    if not focus:
        return False
    style = _GetWindowLong(focus, GWL_STYLE)
    return bool(style & ES_PASSWORD)


class ForegroundTracker:
    """Polls the foreground app + password state on a background thread so the
    hot path (consuming characters) never blocks on Win32 calls."""

    def __init__(self, interval: float = 0.25):
        self.interval = interval
        self._app: Optional[str] = None
        self._password = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def app(self) -> Optional[str]:
        return self._app

    @property
    def password(self) -> bool:
        return self._password

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._app = foreground_app()
                self._password = focused_is_password()
            except Exception:
                pass
            self._stop.wait(self.interval)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="fg-tracker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
