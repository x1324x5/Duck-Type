"""Native application window (pywebview + WebView2).

Replaces the browser dashboard: the app renders ``dashboard/static/index.html``
in a frameless desktop window and talks to the backend through the in-process
``Api`` bridge (no HTTP, no localhost port). The window lives for the process
lifetime on the main thread (``webview.start`` owns the GUI loop); the tray runs
detached so it can coexist.

Closing the window hides it to the tray (capture keeps running); quitting from
the tray destroys it so the GUI loop returns and the app shuts down.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

log = logging.getLogger("ducktype")

_window = None
_mini = None
_api = None
_quitting = False
_maximized = False
_pending_maximize = False   # defer the first maximize when started hidden (silent start)
_mini_save_timer = None     # debounces persisting the mini size during a drag
_DEFAULT_W, _DEFAULT_H = 1180, 820
# Mini counter window: starts compact (the full layout fits ~2/3 of the old size)
# and can be drag-resized via the in-page corner grip (frameless windows have no
# native resize border, so the frontend drives resize_mini()).
_MINI_DEFAULT_W, _MINI_DEFAULT_H = 196, 246
_MINI_MIN_W, _MINI_MIN_H = 150, 154
_MINI_MAX_W, _MINI_MAX_H = 360, 480


def _clamp_mini(w, h):
    """Clamp a (w, h) to the mini window's allowed range."""
    return (max(_MINI_MIN_W, min(_MINI_MAX_W, int(w))),
            max(_MINI_MIN_H, min(_MINI_MAX_H, int(h))))


def _saved_mini_size():
    """The last drag-resized mini size from config, or the built-in default when
    unset / unavailable."""
    cfg = getattr(_api, "_config", None)
    w, h = _MINI_DEFAULT_W, _MINI_DEFAULT_H
    try:
        if cfg is not None:
            if getattr(cfg, "mini_width", 0):
                w = cfg.mini_width
            if getattr(cfg, "mini_height", 0):
                h = cfg.mini_height
    except Exception:
        pass
    return _clamp_mini(w, h)


def _persist_mini_size(w, h) -> None:
    """Remember the mini window size so the next open restores it. Debounced so a
    drag (which fires many resize calls) only writes config once it settles."""
    global _mini_save_timer
    cfg = getattr(_api, "_config", None)
    if cfg is None:
        return
    if _mini_save_timer is not None:
        try:
            _mini_save_timer.cancel()
        except Exception:
            pass

    def _do():
        try:
            cfg.mini_width, cfg.mini_height = int(w), int(h)
            cfg.save()
        except Exception:
            log.debug("persist mini size failed", exc_info=True)

    _mini_save_timer = threading.Timer(0.5, _do)
    _mini_save_timer.daemon = True
    _mini_save_timer.start()


def _main_hwnd():
    """HWND of the dashboard window, by its (unique) title, falling back to the
    foreground window. WinForms sets ``Form.Text`` to the title even when the
    window is frameless, so ``FindWindowW`` matches it reliably regardless of
    which monitor it sits on."""
    import ctypes
    try:
        hwnd = ctypes.windll.user32.FindWindowW(None, "码字鸭 · DuckType")
        if hwnd:
            return hwnd
    except Exception:
        pass
    try:
        return ctypes.windll.user32.GetForegroundWindow()
    except Exception:
        return 0


def _work_area():
    """Work area (screen minus taskbar) of the monitor the dashboard is *currently
    on*, in *logical* pixels.

    Two things the old code got wrong on a multi-monitor / high-DPI setup:
      1. ``SystemParametersInfoW(SPI_GETWORKAREA)`` only ever reports the *primary*
         monitor — so "maximize" filled the laptop screen even when the window
         had been dragged to the external display. We instead query the monitor
         under the window via ``MonitorFromWindow`` + ``GetMonitorInfoW``.
      2. ``move``/``resize`` take *logical* (CSS) pixels but the monitor rect is
         *physical*; we divide by that monitor's DPI so it fills any screen at
         any scaling.

    pywebview's frameless ``maximize()`` is itself unreliable on the WebView2/
    WinForms backend, which is why we size to the work area ourselves."""
    import ctypes
    from ctypes import wintypes

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    user32 = ctypes.windll.user32
    hwnd = _main_hwnd()
    rect = None
    scale = 1.0
    try:
        if hwnd:
            # MONITOR_DEFAULTTONEAREST = 2
            mon = user32.MonitorFromWindow(hwnd, 2)
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(_MONITORINFO)
            if mon and user32.GetMonitorInfoW(mon, ctypes.byref(info)):
                rect = info.rcWork
            dpi = user32.GetDpiForWindow(hwnd)
            if dpi:
                scale = dpi / 96.0
    except Exception:
        rect = None
    if rect is None:
        # Fallback: primary monitor work area (SPI_GETWORKAREA = 0x0030).
        rect = wintypes.RECT()
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        try:
            scale = (user32.GetDpiForSystem() or 96) / 96.0
        except Exception:
            scale = 1.0
    scale = scale or 1.0
    left = int(rect.left / scale)
    top = int(rect.top / scale)
    w = int((rect.right - rect.left) / scale)
    h = int((rect.bottom - rect.top) / scale)
    return left, top, w, h


def maximize_main() -> bool:
    """Fill the whole work area of the window's current monitor (our deterministic
    maximize). Adapts to any screen size, DPI and multi-monitor layout; safe to
    call repeatedly."""
    global _maximized
    if _window is None:
        return False
    try:
        left, top, w, h = _work_area()
        _window.resize(w, h)
        _window.move(left, top)
        _maximized = True
    except Exception:
        log.exception("maximize_main failed")
    return _maximized


def restore_main() -> bool:
    """Return to the default window size, centred in the work area."""
    global _maximized
    if _window is None:
        return False
    try:
        left, top, w, h = _work_area()
        _window.resize(_DEFAULT_W, _DEFAULT_H)
        _window.move(left + max(0, (w - _DEFAULT_W) // 2),
                     top + max(0, (h - _DEFAULT_H) // 2))
        _maximized = False
    except Exception:
        log.exception("restore_main failed")
    return _maximized


def toggle_maximize() -> bool:
    """Toggle maximized/restored; returns the new maximized state."""
    if _maximized:
        restore_main()
    else:
        maximize_main()
    return _maximized


def _record_open(kind: str) -> None:
    """Log one dashboard/mini opening for the usage-history panel. Best-effort:
    never let a logging hiccup interfere with showing the window."""
    try:
        if _api is not None and getattr(_api, "_db", None) is not None:
            _api._db.record_dashboard_open(kind)
    except Exception:
        log.debug("record_dashboard_open failed", exc_info=True)


def _index_html() -> Path:
    from .paths import resource_dir
    candidates = [
        resource_dir() / "dashboard" / "static" / "index.html",            # source run
        resource_dir() / "ducktype" / "dashboard" / "static" / "index.html",  # frozen
        Path(__file__).resolve().parent / "dashboard" / "static" / "index.html",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError("dashboard index.html not found in any known location")


def run_window(api, hidden: bool = False) -> None:
    """Create the window and run the GUI loop (blocks until quit).

    ``hidden`` starts the app in the tray with no visible window (the user opens
    it from the tray); otherwise the window is shown on launch.
    """
    global _window, _api
    import webview

    _api = api
    index = _index_html()
    _window = webview.create_window(
        "码字鸭 · DuckType",
        url=index.as_uri(),
        js_api=api,
        frameless=True,
        resizable=True,
        easy_drag=False,            # we mark our own .pywebview-drag-region
        width=_DEFAULT_W, height=_DEFAULT_H,
        min_size=(760, 560),
        background_color="#0f1216",
        hidden=hidden,
    )
    api._set_window(_window)
    _window.events.closing += _on_closing
    # Open maximized: do it once the window is realized (the create-time
    # `maximized` flag is ignored for frameless WebView2 windows).
    #
    # When starting hidden (silent tray start) we must NOT touch the window's
    # geometry: the `shown` event still fires while WebView2 initialises, and
    # resizing/moving it then forces the still-empty window visible as a black
    # flash (the bug where a silent start popped a black dashboard). Instead we
    # defer the maximize to the first real open from the tray/hotkey.
    global _pending_maximize
    _pending_maximize = True
    def _maximize_on_show():
        global _pending_maximize
        if _pending_maximize:
            maximize_main()
            _pending_maximize = False
        _record_open("dashboard")
    if not hidden:
        try:
            _window.events.shown += _maximize_on_show
        except Exception:
            pass
    log.info("Native window loading %s", index)
    webview.start()                  # blocks; returns when the window is destroyed


def _on_closing():
    # Returning False cancels the close; we hide to the tray instead so capture
    # keeps running. On a real quit (_quitting) we allow it through.
    if _quitting:
        return True
    try:
        _window.hide()
    except Exception:
        pass
    return False


def _restore_main_window() -> None:
    """Show + restore the dashboard window (no usage logging). Used internally
    when returning from the mini counter so it isn't counted as a fresh open."""
    global _pending_maximize
    if _window is None:
        return
    try:
        _window.show()
        _window.restore()
        # If we started hidden, the launch-time maximize was deferred so it
        # wouldn't flash an empty window; apply it now on the first real open.
        if _pending_maximize:
            maximize_main()
            _pending_maximize = False
    except Exception:
        pass


def show_window() -> None:
    """Bring the window back from the tray/hotkey (safe from any thread)."""
    _record_open("dashboard")
    _restore_main_window()


def show_mini() -> None:
    """Hide the dashboard and open the always-on-top mini counter window.

    Loads the same index.html with a ``#mini`` fragment so the frontend renders
    only the gauge view. Safe to call repeatedly (re-shows an existing mini)."""
    global _mini
    import webview
    _record_open("mini")
    if _window is not None:
        try:
            _window.hide()
        except Exception:
            pass
    if _mini is not None:
        try:
            _mini.show()
            _mini.restore()
            return
        except Exception:
            _mini = None
    index = _index_html()
    init_w, init_h = _saved_mini_size()         # restore the last drag-resized size
    _mini = webview.create_window(
        "码字鸭 · 迷你计数器",
        url=index.as_uri() + "#mini",
        js_api=_api,
        frameless=True,
        resizable=True,             # user can zoom the gauge/sparkline within a range
        on_top=True,
        width=init_w, height=init_h,
        min_size=(_MINI_MIN_W, _MINI_MIN_H),   # smallest keeps the speed gauge + 本次会话
        background_color="#0f1216",
    )
    try:
        _mini.events.closing += _on_mini_closing
    except Exception:
        pass


def resize_mini(w, h) -> dict:
    """Resize the mini window to (w, h), clamped to its allowed range.

    Frameless WebView2 windows expose no native resize border, so the in-page
    corner grip calls this through the Api as the user drags."""
    if _mini is None:
        return {"ok": False}
    try:
        w, h = _clamp_mini(w, h)
        _mini.resize(w, h)
        _persist_mini_size(w, h)    # remember it for the next open
        return {"ok": True, "w": w, "h": h}
    except Exception:
        log.exception("resize_mini failed")
        return {"ok": False}


def _on_mini_closing():
    # Mini window is going away (user closed it) -> bring the dashboard back.
    global _mini
    _mini = None
    try:
        _restore_main_window()
    except Exception:
        pass
    return True


def close_mini() -> None:
    """Destroy the mini counter and restore the dashboard (safe from any thread)."""
    global _mini
    m = _mini
    _mini = None
    if m is not None:
        try:
            m.destroy()
        except Exception:
            pass
    _restore_main_window()


def quit_window() -> None:
    """Destroy the window so ``run_window`` returns (safe from any thread)."""
    global _quitting
    _quitting = True
    if _window is None:
        return
    try:
        _window.destroy()
    except Exception:
        pass
