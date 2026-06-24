"""Check GitHub Releases for a newer DuckType and (for packaged builds) apply it.

The packaged .exe cannot overwrite itself while running, so applying an update
streams the new exe to disk (reporting download progress) **right next to the
running exe**, then writes a tiny .bat that waits for this process to exit, swaps
the file in place -- keeping the exact same path and name -- and relaunches, then
asks the app to quit. Staying in the original folder under the original name is
what keeps the user's setup intact: the autostart Run-key and any shortcuts point
at that path, so a lossless in-place swap leaves them all working.

Three failure modes this guards against (all seen as a "找不到路径 / can't relaunch"
report after a download that otherwise succeeded):

  * **Stale _MEI dir.** A PyInstaller one-file process exports ``_MEIPASS2`` /
    ``_PYI_*`` env vars pointing at its private temp-extraction directory. If the
    relaunch inherits them, the new exe tries to reuse that dir -- which the old
    process deletes on exit -- and fails to find its Python DLL. We strip those
    vars from the swap script's environment.
  * **Invalid working directory.** cmd inherits this process's CWD; if that was a
    now-deleted temp dir, cmd aborts with "The system cannot find the path
    specified" before the swap even runs. We launch it with an explicit ``cwd``
    of the exe's folder, which always exists.
  * **Non-ASCII paths.** When the exe lives under a Chinese folder name, writing
    the .bat as ASCII dropped those characters and produced a broken path. We
    write it in the system codepage (``mbcs``) so the path survives.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import urllib.request
from typing import Callable, Optional

from . import __version__
from .paths import root_dir

log = logging.getLogger("ducktype")

REPO = "x1324x5/Duck-Type"
_API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
_RELEASES = f"https://github.com/{REPO}/releases"

# Shared progress state for the dashboard, guarded by _lock.
_lock = threading.Lock()
_state: dict = {"phase": "idle", "downloaded": 0, "total": 0, "error": "",
                "latest": "", "path": "", "target": ""}
_worker: Optional[threading.Thread] = None


def _set_state(**kw) -> None:
    with _lock:
        _state.update(kw)


def progress() -> dict:
    """Snapshot of the current download/staging progress for the dashboard."""
    with _lock:
        return dict(_state)


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


def _clean_env() -> dict:
    """A copy of the environment with PyInstaller's one-file bootstrap vars
    removed, so a relaunched exe extracts fresh instead of reusing this
    process's (about-to-be-deleted) _MEI temp directory."""
    return {k: v for k, v in os.environ.items()
            if not (k.startswith("_MEI") or k.startswith("_PYI"))}


def _staging_dir(cur: str) -> str:
    """Where to drop the downloaded exe and swap script.

    Prefer the folder the running exe lives in, so the swap is a fast, reliable
    same-directory rename and the new exe ends up exactly where the old one was.
    Falls back to the data root only if that folder isn't writable (e.g. the user
    parked the exe under Program Files)."""
    exe_dir = os.path.dirname(cur)
    try:
        probe = os.path.join(exe_dir, ".dt_update_write_test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return exe_dir
    except OSError:
        return str(root_dir())


def _write_swap_script(new: str, cur: str, staging: str) -> str:
    """Write the wait-then-swap-then-relaunch batch file; return its path.

    The swap is lossless at every step: the old exe is moved aside (not deleted)
    before the new one takes its place, and if the install can't complete the old
    exe is moved back, so a working DuckType always remains at ``cur``. ``ping`` is
    the delay because ``timeout`` needs console stdin, which a hidden process lacks.
    Written in the system codepage so non-ASCII (e.g. Chinese) paths survive."""
    bat = os.path.join(staging, "_update.bat")
    pid = os.getpid()
    bak = cur + ".old"
    script = (
        "@echo off\r\n"
        ":wait\r\n"
        f'tasklist /FI "PID eq {pid}" | find "{pid}" >nul\r\n'
        "if not errorlevel 1 ( ping -n 2 127.0.0.1 >nul & goto wait )\r\n"
        # Move the old exe aside, retrying while the bootloader releases its lock.
        "set /a tries=0\r\n"
        ":mvold\r\n"
        f'if not exist "{cur}" goto putnew\r\n'
        f'move /Y "{cur}" "{bak}" >nul 2>&1\r\n'
        "if not errorlevel 1 goto putnew\r\n"
        "set /a tries+=1\r\n"
        "if %tries% geq 30 goto giveup\r\n"
        "ping -n 2 127.0.0.1 >nul\r\n"
        "goto mvold\r\n"
        # Put the freshly downloaded exe in place under the original name.
        ":putnew\r\n"
        f'move /Y "{new}" "{cur}" >nul 2>&1\r\n'
        "if errorlevel 1 goto rollback\r\n"
        f'del /F /Q "{bak}" >nul 2>&1\r\n'
        "goto run\r\n"
        # Install failed: restore the old exe so nothing is lost.
        ":rollback\r\n"
        f'if exist "{bak}" move /Y "{bak}" "{cur}" >nul 2>&1\r\n'
        f'del /F /Q "{new}" >nul 2>&1\r\n'
        "goto run\r\n"
        # Old exe never unlocked: leave it untouched and still relaunch it.
        ":giveup\r\n"
        ":run\r\n"
        f'if exist "{cur}" start "" "{cur}"\r\n'
        'del "%~f0"\r\n'
    )
    try:
        f = open(bat, "w", encoding="mbcs", errors="replace", newline="")
    except LookupError:                          # non-Windows (tests/import only)
        f = open(bat, "w", encoding="utf-8", errors="replace", newline="")
    with f:
        f.write(script)
    return bat


def _download_and_stage(url: str, cur: str, on_quit: Optional[Callable]) -> None:
    staging = _staging_dir(cur)
    new = os.path.join(staging, "DuckType-new.exe")
    _set_state(phase="downloading", downloaded=0, total=0, error="",
               path=new, target=cur)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DuckType-Updater"})
        with urllib.request.urlopen(req, timeout=30) as r:
            total = int(r.headers.get("Content-Length") or 0)
            _set_state(total=total)
            downloaded = 0
            with open(new, "wb") as f:
                while True:
                    chunk = r.read(262144)  # 256 KB
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    _set_state(downloaded=downloaded)
    except Exception as exc:
        _set_state(phase="error", error="下载失败：" + str(exc))
        log.exception("Update download failed")
        return

    # Guard against a redirect/error page or truncated download replacing the
    # exe with garbage. A real build is tens of MB; under 1 MB is not us.
    _set_state(phase="verifying")
    try:
        size = os.path.getsize(new)
    except OSError:
        size = 0
    if size < 1_000_000 or (total and size != total):
        try:
            os.remove(new)
        except OSError:
            pass
        _set_state(phase="error",
                   error=f"下载的文件不完整（{size} 字节），已取消，请稍后重试。")
        return

    try:
        bat = _write_swap_script(new, cur, staging)
        CREATE_NO_WINDOW = 0x08000000
        # Run from the exe's folder (always exists) so cmd never inherits this
        # process's about-to-be-deleted _MEI temp dir as its working directory.
        exe_dir = os.path.dirname(cur) or staging
        subprocess.Popen(["cmd", "/c", bat], creationflags=CREATE_NO_WINDOW,
                         close_fds=True, env=_clean_env(), cwd=exe_dir)
    except Exception as exc:
        _set_state(phase="error", error="启动更新脚本失败：" + str(exc))
        log.exception("Failed to launch update script")
        return

    _set_state(phase="staged")
    log.info("Update staged: %s (%d bytes) -> %s on exit.", new, size, cur)
    if on_quit:
        # Give the dashboard a moment to read the 'staged' state before we exit
        # so the swap-on-exit script can run.
        threading.Timer(2.0, on_quit).start()


def start_apply(on_quit: Optional[Callable] = None) -> dict:
    """Begin downloading the update on a background thread. The dashboard polls
    ``progress()`` for status; the app quits itself once the swap is staged."""
    global _worker
    if not getattr(sys, "frozen", False):
        return {"ok": False,
                "error": "源码运行请用 git pull 后重新构建（仅打包版支持一键更新）。"}
    with _lock:
        if _worker is not None and _worker.is_alive():
            return {"ok": True, "started": True}  # already running

    info = check()
    if not info.get("ok"):
        return {"ok": False, "error": "无法连接更新服务器：" + info.get("error", "")}
    if not info.get("has_update"):
        return {"ok": False, "error": "已经是最新版本。"}
    if not info.get("download_url"):
        return {"ok": False, "error": "该版本没有可下载的 exe 资源。"}

    cur = os.path.abspath(sys.executable)
    _set_state(phase="downloading", downloaded=0, total=0, error="",
               latest=info.get("latest", ""), path="", target=cur)
    _worker = threading.Thread(
        target=_download_and_stage, args=(info["download_url"], cur, on_quit),
        name="updater", daemon=True)
    _worker.start()
    return {"ok": True, "started": True, "latest": info.get("latest")}


# Back-compat: the old synchronous entry point. Now just kicks off the async
# flow so any caller still invoking apply() keeps working.
def apply(on_quit: Optional[Callable] = None) -> dict:
    return start_apply(on_quit)
