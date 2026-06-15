"""Committed-character capture via the native WH_GETMESSAGE hook DLL.

Flow:
  1. We create a hidden top-level window of class "DuckTypeHostWindowV2".
  2. We load ducktype_hook.dll and install a global WH_GETMESSAGE hook using the
     DLL's exported GetMsgProc. Windows injects the DLL into every GUI process.
  3. Inside each process the hook posts every WM_CHAR / WM_IME_CHAR code unit to
     our window via a system-wide registered message.
  4. Our window procedure reassembles surrogate pairs and forwards finished
     characters to ``on_char``.

If the DLL is missing (e.g. it was never built) ``CharHook.available`` is False
and the rest of the app still runs -- it just won't record committed characters.
"""
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable, Optional

from ..paths import hook_dll_path

log = logging.getLogger("ducktype")

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_GETMESSAGE = 3
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
CLASS_NAME = "DuckTypeHostWindowV2"
REG_MSG_NAME = "DuckType_CommittedChar_V2"

WNDPROC = ctypes.CFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


# ---- function prototypes -------------------------------------------------
_user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
_user32.RegisterClassW.restype = wintypes.ATOM
_user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
_user32.CreateWindowExW.restype = wintypes.HWND
_user32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
_user32.DefWindowProcW.restype = ctypes.c_ssize_t
_user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
]
_user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
_user32.RegisterWindowMessageW.restype = wintypes.UINT
_user32.DestroyWindow.argtypes = [wintypes.HWND]
_user32.PostMessageW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
_user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, ctypes.c_void_p, wintypes.HINSTANCE, wintypes.DWORD
]
_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
_user32.PostQuitMessage.argtypes = [ctypes.c_int]
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE
_kernel32.GetProcAddress.argtypes = [wintypes.HMODULE, wintypes.LPCSTR]
_kernel32.GetProcAddress.restype = ctypes.c_void_p


def _is_han(cp: int) -> bool:
    return (
        0x4E00 <= cp <= 0x9FFF       # CJK Unified Ideographs
        or 0x3400 <= cp <= 0x4DBF    # Extension A
        or 0xF900 <= cp <= 0xFAFF    # Compatibility Ideographs
        or 0x20000 <= cp <= 0x2FFFF  # Extensions B-F (astral)
    )


class CharHook:
    def __init__(self, on_char: Callable[[str], None]):
        self._on_char = on_char
        self.available = hook_dll_path().exists()
        self.installed = False          # True once the global hook is set
        self.units = 0                  # ANY WM_CHAR code unit posted by the DLL
        self.received = 0               # committed Han chars seen this session
        self._dll = None
        self._hook = None
        self._hwnd = None
        self._reg_msg = 0
        self._thread: Optional[threading.Thread] = None
        self._pending_high: Optional[int] = None
        self._wndproc = WNDPROC(self._window_proc)
        self._class_atom = 0

    # ---- window procedure (runs on the pump thread) ----------------------
    def _window_proc(self, hwnd, msg, wparam, lparam):
        if self._reg_msg and msg == self._reg_msg:
            self._handle_code_unit(int(wparam) & 0xFFFF)
            return 0
        if msg == WM_CLOSE:
            _user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            _user32.PostQuitMessage(0)
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _handle_code_unit(self, unit: int) -> None:
        if self.units == 0:
            log.info("CharHook: first WM_CHAR code unit received (injection works).")
        self.units += 1
        cp = unit
        if 0xD800 <= unit <= 0xDBFF:           # high surrogate
            self._pending_high = unit
            return
        if 0xDC00 <= unit <= 0xDFFF:           # low surrogate
            if self._pending_high is None:
                return
            cp = 0x10000 + ((self._pending_high - 0xD800) << 10) + (unit - 0xDC00)
            self._pending_high = None
        else:
            self._pending_high = None
        if _is_han(cp):
            if self.received == 0:
                log.info("First committed character captured (hook is working).")
            self.received += 1
            try:
                self._on_char(chr(cp))
            except Exception:
                pass

    # ---- lifecycle -------------------------------------------------------
    def _run(self) -> None:
        hinst = _kernel32.GetModuleHandleW(None)
        wc = WNDCLASS()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = CLASS_NAME
        self._class_atom = _user32.RegisterClassW(ctypes.byref(wc))

        # Hidden top-level window (NOT message-only) so the injected DLL can
        # locate it with FindWindowW.
        self._hwnd = _user32.CreateWindowExW(
            0, CLASS_NAME, "DuckType", 0, 0, 0, 0, 0, None, None, hinst, None
        )
        if not self._hwnd:
            log.error("CharHook: CreateWindowExW failed (err=%d)", ctypes.get_last_error())
            return

        self._reg_msg = _user32.RegisterWindowMessageW(REG_MSG_NAME)

        try:
            self._dll = ctypes.WinDLL(str(hook_dll_path()))
        except OSError as exc:
            # Most often: the DLL is the wrong architecture for this Python.
            log.error("CharHook: failed to load %s (%s). Is it the same 64/32-bit "
                      "as DuckType?", hook_dll_path(), exc)
            return

        proc = _kernel32.GetProcAddress(self._dll._handle, b"GetMsgProc")
        if not proc:
            log.error("CharHook: GetProcAddress('GetMsgProc') failed -- the DLL is "
                      "missing its export.")
            return

        self._hook = _user32.SetWindowsHookExW(
            WH_GETMESSAGE, ctypes.c_void_p(proc),
            wintypes.HINSTANCE(self._dll._handle), 0
        )
        if not self._hook:
            log.error("CharHook: SetWindowsHookExW failed (err=%d). The global hook "
                      "could not be installed.", ctypes.get_last_error())
            return
        self.installed = True
        log.info("CharHook: global WH_GETMESSAGE hook installed from %s", hook_dll_path())

        msg = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

        if self._hook:
            _user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
        self.installed = False
        self._hwnd = None

    def start(self) -> bool:
        if not self.available:
            return False
        self._thread = threading.Thread(target=self._run, name="char-hook", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._hwnd:
            _user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._dll = None
