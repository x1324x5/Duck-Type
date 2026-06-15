"""Low-level keyboard hook (WH_KEYBOARD_LL).

Unlike the committed-character capture this hook needs NO injected DLL: a
low-level hook procedure runs in the installing thread's context. It cannot see
IME-composed Chinese (that is what the native hook is for), but it perfectly
captures control keys, which is exactly what we need for edit/deletion stats and
a raw keystroke total used by the efficiency metrics.
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Callable, Optional

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_QUIT = 0x0012

VK_BACK = 0x08
VK_DELETE = 0x2E
VK_RETURN = 0x0D

_VK_KIND = {VK_BACK: "backspace", VK_DELETE: "delete", VK_RETURN: "enter"}


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


HOOKPROC = ctypes.CFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

_user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD
]
_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.CallNextHookEx.argtypes = [
    wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
]
_user32.CallNextHookEx.restype = ctypes.c_ssize_t
_user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
_user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
]
_user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class KeyHook:
    """Calls ``on_event(kind)`` for the control keys we track (backspace /
    delete / enter). Runs its own message loop on a dedicated daemon thread."""

    def __init__(self, on_event: Callable[[str], None]):
        self._on_event = on_event
        self._hook = None
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        # Keep a strong reference to the C callback for the hook's lifetime.
        self._proc = HOOKPROC(self._callback)

    def _callback(self, nCode, wParam, lParam):
        if nCode == 0 and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            try:
                kind = _VK_KIND.get(kb.vkCode)
                if kind is not None:
                    self._on_event(kind)
            except Exception:
                pass
        return _user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _run(self) -> None:
        self._thread_id = _kernel32.GetCurrentThreadId()
        hmod = _kernel32.GetModuleHandleW(None)
        self._hook = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, hmod, 0)
        if not self._hook:
            return
        msg = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass  # we only need the loop alive to dispatch the LL hook
        _user32.UnhookWindowsHookEx(self._hook)
        self._hook = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="key-hook", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
