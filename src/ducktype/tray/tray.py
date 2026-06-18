"""System tray icon (pystray). Runs detached while pywebview owns the GUI loop.

Closing the dashboard window does NOT stop DuckType -- the app keeps running in
the background behind this tray icon until you choose 退出 (Quit).
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pystray
from pystray import Menu, MenuItem

from ..branding import app_image
from ..paths import root_dir

if TYPE_CHECKING:  # pragma: no cover
    from ..app import App


class TrayApp:
    def __init__(self, app: "App"):
        self.app = app
        self._icon = pystray.Icon(
            "DuckType", app_image(64, active=not app.config.paused), "DuckType · 码字鸭"
        )
        self._icon.menu = Menu(
            MenuItem("打开仪表盘", self._open_dashboard, default=True),
            MenuItem("打开数据文件夹", self._open_data_dir),
            Menu.SEPARATOR,
            MenuItem("暂停统计", self._toggle_pause, checked=lambda i: self.app.config.paused),
            MenuItem("开机自启", self._toggle_autostart, checked=lambda i: self.app.config.autostart),
            Menu.SEPARATOR,
            MenuItem("退出", self._quit),
        )

    def _open_dashboard(self, icon, item):
        self.app.show_window()

    def _open_data_dir(self, icon, item):
        path = str(root_dir())
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass

    def _toggle_pause(self, icon, item):
        self.app.set_paused(not self.app.config.paused)
        self._icon.icon = app_image(64, active=not self.app.config.paused)

    def _toggle_autostart(self, icon, item):
        self.app.set_autostart(not self.app.config.autostart)

    def _quit(self, icon, item):
        # Quitting must tear down the native window so the GUI loop returns and
        # the app shuts down. App.request_quit drives that; just stop the icon.
        self.app.request_quit()

    def stop_icon(self) -> None:
        """Remove the tray icon (does not shut the app down)."""
        try:
            self._icon.stop()
        except Exception:
            pass

    def run_detached(self) -> None:
        """Show the tray icon without blocking (the webview owns the main loop)."""
        self._icon.run_detached()
