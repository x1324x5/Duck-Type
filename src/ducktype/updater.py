"""Check GitHub Releases for a newer DuckType and (for packaged builds) apply it.

The packaged .exe cannot overwrite itself while running, so applying an update
downloads the new exe, writes a tiny .bat that waits for this process to exit,
swaps the file and relaunches, then asks the app to quit.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import urllib.request
from typing import Optional

from . import __version__
from .paths import data_dir

log = logging.getLogger("ducktype")

REPO = "x1324x5/Duck-Type"
_API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
_RELEASES = f"https://github.com/{REPO}/releases"


def _version_tuple(v: str):
    parts = []
    for p in (v or "").lstrip("vV").split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts) or (0,)


def _is_newer(latest: str, current: str) -> bool:
    return _version_tuple(latest) > _version_tuple(current)


def check() -> dict:
    """Query the latest release. Never raises -- returns ok=False on failure."""
    try:
        req = urllib.request.Request(
            _API_LATEST,
            headers={"User-Agent": "DuckType-Updater",
                     "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
    except Exception as exc:  # network/proxy/rate-limit/no-release
        log.info("Update check failed: %s", exc)
        return {"ok": False, "error": str(exc), "current": __version__,
                "releases_url": _RELEASES}

    latest = (data.get("tag_name") or "").lstrip("vV")
    exe_url = None
    for a in data.get("assets", []):
        if str(a.get("name", "")).lower().endswith(".exe"):
            exe_url = a.get("browser_download_url")
            break
    return {
        "ok": True,
        "current": __version__,
        "latest": latest,
        "has_update": bool(latest) and _is_newer(latest, __version__),
        "html_url": data.get("html_url") or _RELEASES,
        "releases_url": _RELEASES,
        "download_url": exe_url,
        "notes": (data.get("body") or "")[:4000],
        "frozen": bool(getattr(sys, "frozen", False)),
    }


def apply() -> dict:
    """Download the new exe and stage a swap-on-exit. Returns pending=True on
    success; the caller should then quit the app so the swap can proceed."""
    if not getattr(sys, "frozen", False):
        return {"ok": False,
                "error": "源码运行请用 git pull 后重新构建（仅打包版支持一键更新）。"}
    info = check()
    if not info.get("ok"):
        return {"ok": False, "error": "无法连接更新服务器：" + info.get("error", "")}
    if not info.get("has_update"):
        return {"ok": False, "error": "已经是最新版本。"}
    if not info.get("download_url"):
        return {"ok": False, "error": "该版本没有可下载的 exe 资源。"}

    cur = os.path.abspath(sys.executable)
    new = os.path.join(str(data_dir()), "DuckType-update.exe")
    try:
        urllib.request.urlretrieve(info["download_url"], new)
    except Exception as exc:
        return {"ok": False, "error": "下载失败：" + str(exc)}

    # Guard against a redirect/error page or a truncated download silently
    # replacing the exe with garbage (which would then fail to launch). A real
    # build is tens of MB; anything under 1 MB is not the program.
    try:
        size = os.path.getsize(new)
    except OSError:
        size = 0
    if size < 1_000_000:
        try:
            os.remove(new)
        except OSError:
            pass
        return {"ok": False,
                "error": f"下载的文件不完整（仅 {size} 字节），已取消。请稍后重试或到发布页手动下载。"}

    bat = os.path.join(str(data_dir()), "_update.bat")
    pid = os.getpid()
    # Wait for THIS process to exit, then replace the exe -- retrying the move so
    # the brief window where the PyInstaller bootloader still holds the file lock
    # doesn't abort the swap -- then relaunch. `ping` is used for the delay
    # because `timeout` needs console stdin, which a hidden-console process lacks.
    script = (
        "@echo off\r\n"
        ":wait\r\n"
        f'tasklist /FI "PID eq {pid}" | find "{pid}" >nul\r\n'
        "if not errorlevel 1 ( ping -n 2 127.0.0.1 >nul & goto wait )\r\n"
        "set /a tries=0\r\n"
        ":swap\r\n"
        f'move /Y "{new}" "{cur}" >nul 2>&1\r\n'
        "if not errorlevel 1 goto run\r\n"
        "set /a tries+=1\r\n"
        "if %tries% geq 30 goto run\r\n"
        "ping -n 2 127.0.0.1 >nul\r\n"
        "goto swap\r\n"
        ":run\r\n"
        f'start "" "{cur}"\r\n'
        'del "%~f0"\r\n'
    )
    try:
        with open(bat, "w", encoding="ascii", errors="ignore") as f:
            f.write(script)
        # CREATE_NO_WINDOW keeps a (hidden) console so `start` can relaunch the
        # app; DETACHED_PROCESS would remove the console and the relaunch fails.
        CREATE_NO_WINDOW = 0x08000000
        subprocess.Popen(["cmd", "/c", bat],
                         creationflags=CREATE_NO_WINDOW, close_fds=True)
    except Exception as exc:
        return {"ok": False, "error": "启动更新脚本失败：" + str(exc)}

    log.info("Update staged: downloaded %s (%d bytes); will replace %s on exit.",
             new, size, cur)
    return {"ok": True, "pending": True, "latest": info.get("latest"),
            "path": new, "target": cur, "size": size}
