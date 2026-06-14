"""System tray icon (pystray). Runs on the main thread and blocks until quit.

Closing the dashboard browser tab does NOT stop DuckType -- the app keeps
running in the background behind this tray icon until you choose 退出 (Quit).
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pystray
from pystray import Menu, MenuItem

from ..branding import app_image
from ..paths import data_dir

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
        self.app.open_dashboard()

    def _open_data_dir(self, icon, item):
        path = str(data_dir())
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
        self.app.shutdown()
        self._icon.stop()

    def run(self) -> None:
        self._icon.run()
