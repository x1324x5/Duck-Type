"""Data-root relocation worker used by the dashboard bridge."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger("ducktype")


class Relocator:
    """Move the whole data root to a new folder on a background thread.

    The flow is copy -> verify -> mark old root for cleanup -> switch pointer.
    The old root is deleted on the next startup so a crash mid-move cannot lose
    the source data.
    """

    def __init__(self, db):
        self.db = db
        self._lock = threading.Lock()
        self._state = {
            "phase": "idle",
            "done": 0,
            "total": 0,
            "error": "",
            "db_path": "",
        }
        self._thread: Optional[threading.Thread] = None

    def progress(self) -> dict:
        with self._lock:
            return dict(self._state)

    def _set(self, **kw) -> None:
        with self._lock:
            self._state.update(kw)

    def start(self, target: Optional[str]) -> dict:
        from ..paths import root_dir

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return {"ok": False, "error": "正在移动中，请稍候。"}
        target = (target or "").strip()
        if not target:
            return {"ok": False, "error": "请选择目标文件夹。"}
        src = root_dir()
        dst = Path(target).expanduser()
        try:
            if dst.resolve() == src.resolve():
                return {"ok": False, "error": "目标位置与当前相同。"}
        except OSError:
            pass
        self._set(
            phase="copying",
            done=0,
            total=0,
            error="",
            db_path=str(dst / "ducktype.db"),
        )
        self._thread = threading.Thread(
            target=self._run, args=(src, dst), name="relocate", daemon=True
        )
        self._thread.start()
        return {
            "ok": True,
            "started": True,
            "restart_required": True,
            "db_path": str(dst / "ducktype.db"),
        }

    def _run(self, src: Path, dst: Path) -> None:
        from .. import firstrun, paths

        try:
            dst.mkdir(parents=True, exist_ok=True)
            plan = firstrun.plan_files(src, dst, include_db=False, include_log=False)
            other_total = sum(s.stat().st_size for s, _ in plan if s.exists())
            db_file = src / "ducktype.db"
            db_size = db_file.stat().st_size if db_file.exists() else 0
            total = other_total + db_size
            self._set(total=total, done=0)

            self.db.backup_to(dst / "ducktype.db")
            self._set(done=db_size)

            firstrun.copy_files(
                plan, on_progress=lambda d, _t: self._set(done=db_size + d)
            )

            ok = firstrun.verify_files(plan) and (dst / "ducktype.db").stat().st_size > 0
            if not ok:
                self._set(phase="error", error="校验失败，已保留原数据，未切换位置。")
                return
            (dst / firstrun.CLEANUP_MARKER).write_text(str(src), encoding="utf-8")
            paths.write_pointer(str(dst))
            self._set(phase="done", done=total)
            log.info("Relocated data root %s -> %s (restart to apply)", src, dst)
        except Exception as exc:
            log.exception("Relocate failed")
            self._set(phase="error", error=str(exc))
