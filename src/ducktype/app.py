"""Application orchestrator: wires capture -> storage -> native window -> tray."""
from __future__ import annotations

import logging
import sys
import threading

from . import autostart, paths
from .config import Config
from .dashboard import Api
from .paths import db_path, hook_dll_path, log_path
from .storage import Database

log = logging.getLogger("ducktype")

# How often the background retention sweep runs (seconds).
_PURGE_INTERVAL = 6 * 3600


class App:
    def __init__(self):
        self.config = Config.load()
        # The data root is resolved by paths.root_dir() (set during the
        # first-run bootstrap); the database always lives under it.
        self.db = Database(db_path())
        # The dashboard UI is rendered in a native window (desktop.py) and talks
        # to this Api over the pywebview bridge -- no HTTP server, no port.
        self.api = Api(self.db, self.config, self.get_status, self.request_quit)
        self._tracker = None
        self._char_hook = None
        self._key_hook = None
        self._tray = None
        self._purge_timer: threading.Timer | None = None

    # ---- status (for the dashboard health banner) -----------------------
    def get_status(self) -> dict:
        ch = self._char_hook
        return {
            "platform_supported": sys.platform.startswith("win"),
            "paused": self.config.paused,
            "hook_dll_found": bool(ch and ch.available),
            "hook_installed": bool(ch and ch.installed),
            "code_units": (ch.units if ch else 0),
            "chars_captured": (ch.received if ch else 0),
            "dll_path": str(hook_dll_path()),
            "active_app": self._active_app(),
            "db_recreated": getattr(self.db, "recreated", False),
        }

    # ---- capture filtering ----------------------------------------------
    def _active_app(self):
        return self._tracker.app if self._tracker else None

    def _should_record(self) -> bool:
        if self.config.paused:
            return False
        if self._tracker is not None:
            if self.config.exclude_password_fields and self._tracker.password:
                return False
            if self.config.is_blacklisted(self._tracker.app):
                return False
        return True

    def _on_char(self, ch: str) -> None:
        if self._should_record():
            self.db.record_char(ch, self._active_app())

    def _on_key(self, kind: str) -> None:
        if self._should_record():
            self.db.record_key(kind, self._active_app())

    # ---- lifecycle -------------------------------------------------------
    def start_background(self) -> None:
        # Imported lazily so analysis-only use works off-Windows.
        from .capture.foreground import ForegroundTracker
        from .capture.key_hook import KeyHook
        from .capture.char_hook import CharHook

        self.db.start()

        self._tracker = ForegroundTracker()
        self._tracker.start()

        self._char_hook = CharHook(self._on_char)
        if self._char_hook.available:
            # The live DLL is now the stable content-addressed copy; drop any
            # older copies left in the native dir by previous builds/updates.
            try:
                paths.prune_stale_hooks(keep=hook_dll_path())
            except Exception:
                log.exception("Pruning stale hook DLLs failed")
            self._char_hook.start()
            log.info("Committed-character capture active.")
        else:
            log.warning(
                "Hook DLL not found at %s -- committed Chinese characters will NOT "
                "be recorded (edit/keystroke stats still work). Build it with "
                "native\\build_dll.bat or use a release build.",
                hook_dll_path(),
            )

        self._key_hook = KeyHook(on_event=self._on_key)
        self._key_hook.start()

        # Keep the registry in sync with the saved preference.
        try:
            autostart.set_enabled(self.config.autostart)
        except Exception:
            log.exception("Failed to apply autostart preference")

        self._schedule_purge()

    # ---- retention -------------------------------------------------------
    def _schedule_purge(self) -> None:
        """Apply the retention policy now and again every few hours."""
        try:
            removed = self.db.purge_retention(self.config.retention_days)
            if removed:
                log.info("Retention sweep deleted %d old characters", removed)
        except Exception:
            log.exception("Retention sweep failed")
        self._purge_timer = threading.Timer(_PURGE_INTERVAL, self._schedule_purge)
        self._purge_timer.daemon = True
        self._purge_timer.start()

    def run(self) -> None:
        if not sys.platform.startswith("win"):
            print("DuckType's capture engine is Windows-only. "
                  "On other platforms you can still inspect an existing database.")
            return
        if not _acquire_single_instance():
            log.warning("Another DuckType instance is already running; exiting.")
            print("DuckType 已经在运行了（看一下系统托盘的小鸭子图标）。")
            return
        self.start_background()
        # Tray runs detached (the webview owns the main GUI loop). The native
        # window then blocks the main thread until the user quits via the tray.
        from .tray import TrayApp
        from . import desktop
        self._tray = TrayApp(self)
        self._tray.run_detached()
        # blocks until quit_window(); start hidden only if the user opted out of
        # showing the window on launch.
        desktop.run_window(self.api, hidden=not self.config.open_dashboard_on_start)
        self.shutdown()

    def request_quit(self) -> None:
        """Quit (callable from any thread, e.g. tray menu or the update flow):
        tear down the window so run()'s GUI loop returns, and remove the tray."""
        from . import desktop
        desktop.quit_window()
        if self._tray is not None:
            self._tray.stop_icon()

    def shutdown(self) -> None:
        log.info("Shutting down ...")
        if self._purge_timer is not None:
            self._purge_timer.cancel()
        for obj in (self._char_hook, self._key_hook, self._tracker):
            try:
                if obj is not None:
                    obj.stop()
            except Exception:
                pass
        try:
            self.db.stop()
        except Exception:
            pass

    # ---- actions invoked from the tray ----------------------------------
    def show_window(self) -> None:
        from . import desktop
        desktop.show_window()

    def set_paused(self, paused: bool) -> None:
        self.config.paused = paused
        self.config.save()
        log.info("Capture %s", "paused" if paused else "resumed")

    def set_autostart(self, enabled: bool) -> None:
        self.config.autostart = enabled
        self.config.save()
        try:
            autostart.set_enabled(enabled)
        except Exception:
            log.exception("Failed to toggle autostart")


# Held for the process lifetime so the kernel keeps the named mutex alive.
_INSTANCE_MUTEX = None


def _acquire_single_instance() -> bool:
    """Return True if we are the only running instance.

    Uses a named kernel mutex so a second launch (e.g. autostart + a manual
    double-click) bails out instead of fighting over the hook and the port.
    """
    global _INSTANCE_MUTEX
    import ctypes
    from ctypes import wintypes

    ERROR_ALREADY_EXISTS = 183
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    _INSTANCE_MUTEX = kernel32.CreateMutexW(None, False, "DuckType_SingleInstance")
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path(), encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def main() -> None:
    # First-run data-folder choice (packaged builds only). Must happen before
    # logging/config/db, since all of those now live under the chosen root.
    if getattr(sys, "frozen", False) and sys.platform.startswith("win"):
        from . import firstrun
        root = firstrun.ensure_data_root()
        if root is None:
            # User declined to choose a folder -- nothing to do.
            return
    else:
        # Source/dev/CLI runs: finish any pending relocation cleanup, then use
        # whatever root paths.root_dir() resolves (default anchor or override).
        try:
            from . import firstrun
            firstrun.cleanup_old_root(paths.root_dir())
        except Exception:
            pass
    setup_logging()
    App().run()
