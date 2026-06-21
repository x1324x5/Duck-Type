"""Windows global hotkeys for the mini counter (0.2.8).

``RegisterHotKey`` is thread-affine: the registering thread receives the
``WM_HOTKEY`` messages, so all registration and the message pump live on one
dedicated daemon thread. The dashboard re-registers live whenever the user edits
the bindings in settings; :meth:`HotkeyManager.apply` posts the request to the
pump thread and reports, per binding, whether the OS accepted it (``False`` =
the combo is already held by another app -> a conflict the settings UI surfaces).

No-op on non-Windows so analysis-only / CI use is unaffected.
"""
from __future__ import annotations

import logging
import sys
import threading
from typing import Callable, Dict, Optional, Tuple

log = logging.getLogger("ducktype")

# WinUser modifier flags + message ids.
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_APP_REREGISTER = 0x8001   # WM_APP + 1: our "re-register now" wake message
WM_APP_QUIT = 0x8002

_MOD_FLAG = {"Ctrl": MOD_CONTROL, "Alt": MOD_ALT, "Shift": MOD_SHIFT, "Win": MOD_WIN}

# Named keys -> virtual-key codes (letters/digits use ord()).
_VK_NAMED = {
    "Space": 0x20, "Enter": 0x0D, "Esc": 0x1B, "Tab": 0x09,
    "Insert": 0x2D, "Delete": 0x2E, "Home": 0x24, "End": 0x23,
    "PageUp": 0x21, "PageDown": 0x22,
    "Up": 0x26, "Down": 0x28, "Left": 0x25, "Right": 0x27,
    "`": 0xC0,
}

_OPEN_ID, _CLOSE_ID = 1, 2


def parse_spec(spec: str) -> Optional[Tuple[int, int]]:
    """'Ctrl+Alt+D' -> (modifiers, vk). None when empty/unparseable.

    Accepts the canonical form produced by ``config._normalise_hotkey``; the main
    key may be a single letter/digit, F1–F24, or one of the named keys above."""
    if not spec or not isinstance(spec, str):
        return None
    parts = [p for p in spec.split("+") if p]
    if not parts:
        return None
    mods, vk = 0, None
    for p in parts:
        if p in _MOD_FLAG:
            mods |= _MOD_FLAG[p]
            continue
        if len(p) == 1 and p.isalnum():
            vk = ord(p.upper())
        elif len(p) >= 2 and p[0] in "Ff" and p[1:].isdigit():
            n = int(p[1:])
            if 1 <= n <= 24:
                vk = 0x70 + (n - 1)
        elif p in _VK_NAMED:
            vk = _VK_NAMED[p]
        else:
            return None
    if vk is None or mods == 0:
        return None
    return mods | MOD_NOREPEAT, vk


class HotkeyManager:
    def __init__(self, on_open: Callable[[], None], on_close: Callable[[], None]):
        self._on_open = on_open
        self._on_close = on_close
        self._thread: Optional[threading.Thread] = None
        self._tid = 0
        self._ready = threading.Event()
        self._req_lock = threading.Lock()
        self._req: Optional[Tuple[str, str]] = None
        self._result: Dict[str, Optional[bool]] = {}
        self._done = threading.Event()
        self._enabled = sys.platform.startswith("win")

    # ---- public API ------------------------------------------------------
    def start(self) -> None:
        if not self._enabled or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)

    def apply(self, open_spec: str, close_spec: str) -> Dict[str, Optional[bool]]:
        """Re-register both bindings; return {'open': ok, 'close': ok} where each
        value is True (registered), False (rejected/conflict) or None (disabled)."""
        if not self._enabled:
            return {"open": None, "close": None}
        if self._thread is None:
            self.start()
        with self._req_lock:
            self._req = (open_spec or "", close_spec or "")
            self._done.clear()
            self._post(WM_APP_REREGISTER)
            self._done.wait(timeout=2.0)
            return dict(self._result)

    def stop(self) -> None:
        if self._enabled and self._thread is not None:
            self._post(WM_APP_QUIT)

    # ---- pump thread -----------------------------------------------------
    def _post(self, msg: int) -> None:
        import ctypes
        if self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, msg, 0, 0)

    def _loop(self) -> None:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        # Force the thread message queue to exist before anyone PostThreadMessages.
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        self._ready.set()
        registered = []   # ids currently registered

        def _unregister_all():
            for hid in registered:
                user32.UnregisterHotKey(None, hid)
            registered.clear()

        def _reregister(open_spec, close_spec):
            _unregister_all()
            res = {"open": None, "close": None}
            for spec, hid, key in ((open_spec, _OPEN_ID, "open"),
                                   (close_spec, _CLOSE_ID, "close")):
                parsed = parse_spec(spec)
                if parsed is None:
                    continue
                mods, vk = parsed
                ok = bool(user32.RegisterHotKey(None, hid, mods, vk))
                res[key] = ok
                if ok:
                    registered.append(hid)
                else:
                    log.info("hotkey %r rejected by OS (likely in use)", spec)
            return res

        try:
            while True:
                got = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if got == 0 or got == -1:
                    break
                if msg.message == WM_HOTKEY:
                    try:
                        (self._on_open if msg.wParam == _OPEN_ID
                         else self._on_close)()
                    except Exception:
                        log.exception("hotkey callback failed")
                elif msg.message == WM_APP_REREGISTER:
                    with self._req_lock:
                        req = self._req
                    if req is not None:
                        self._result = _reregister(*req)
                        self._done.set()
                elif msg.message == WM_APP_QUIT:
                    break
        finally:
            _unregister_all()
