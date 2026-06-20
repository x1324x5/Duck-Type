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
from pathlib import Path

log = logging.getLogger("ducktype")

_window = None
_mini = None
_api = None
_quitting = False
_maximized = False
_DEFAULT_W, _DEFAULT_H = 1180, 820
# Mini counter window: starts compact (the full layout fits ~2/3 of the old size)
# and can be drag-resized via the in-page corner grip (frameless windows have no
# native resize border, so the frontend drives resize_mini()).
_MINI_DEFAULT_W, _MINI_DEFAULT_H = 196, 214
_MINI_MIN_W, _MINI_MIN_H = 150, 154
_MINI_MAX_W, _MINI_MAX_H = 360, 460


def _work_area():
    """Primary monitor work area (screen minus taskbar), in physical pixels.

    pywebview's frameless ``maximize()`` is unreliable on the WebView2/WinForms
    backend (a borderless form maximizes over the taskbar, or not at all), so we
    size the window to the work area ourselves for a predictable "maximized"."""
    import ctypes
    from ctypes import wintypes
    rect = wintypes.RECT()
    # SPI_GETWORKAREA = 0x0030
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def maximize_main() -> bool:
    """Fill the work area (our deterministic maximize). Safe to call repeatedly."""
    global _maximized
    if _window is None:
        return False
    try:
        left, top, w, h = _work_area()
        _window.move(left, top)
        _window.resize(w, h)
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
    def _maximize_on_show():
        maximize_main()
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


def show_window() -> None:
    """Bring the window back from the tray (safe from any thread)."""
    if _window is None:
        return
    try:
        _window.show()
        _window.restore()
    except Exception:
        pass


def show_mini() -> None:
    """Hide the dashboard and open the always-on-top mini counter window.

    Loads the same index.html with a ``#mini`` fragment so the frontend renders
    only the gauge view. Safe to call repeatedly (re-shows an existing mini)."""
    global _mini
    import webview
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
    _mini = webview.create_window(
        "码字鸭 · 迷你计数器",
        url=index.as_uri() + "#mini",
        js_api=_api,
        frameless=True,
        resizable=True,             # user can zoom the gauge/sparkline within a range
        on_top=True,
        width=_MINI_DEFAULT_W, height=_MINI_DEFAULT_H,
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
        w = max(_MINI_MIN_W, min(_MINI_MAX_W, int(w)))
        h = max(_MINI_MIN_H, min(_MINI_MAX_H, int(h)))
        _mini.resize(w, h)
        return {"ok": True, "w": w, "h": h}
    except Exception:
        log.exception("resize_mini failed")
        return {"ok": False}


def _on_mini_closing():
    # Mini window is going away (user closed it) -> bring the dashboard back.
    global _mini
    _mini = None
    try:
        show_window()
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
    show_window()


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
