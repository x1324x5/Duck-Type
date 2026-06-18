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
_quitting = False


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
    global _window
    import webview

    index = _index_html()
    _window = webview.create_window(
        "码字鸭 · DuckType",
        url=index.as_uri(),
        js_api=api,
        frameless=True,
        resizable=True,
        easy_drag=False,            # we mark our own .pywebview-drag-region
        width=1180, height=820,
        min_size=(760, 560),
        background_color="#0f1216",
        hidden=hidden,
    )
    api._set_window(_window)
    _window.events.closing += _on_closing
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
