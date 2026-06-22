"""Milestone notifications (Windows tray balloons).

A small background poller computes the gamification snapshot every few minutes
and fires a tray notification when something worth celebrating happens:

* an achievement is freshly unlocked,
* the daily / weekly / monthly character goal is reached (once per period).

State (which achievements / goals were already announced) is persisted next to
the database so restarts don't re-announce old milestones. The whole thing is a
no-op when ``config.notify_enabled`` is False, so the user can silence it.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Callable, Optional

from .analysis import stats
from .paths import root_dir

log = logging.getLogger("ducktype")

# How often to recompute the snapshot. Generous: milestones are not time-critical
# and the snapshot does a couple of full GROUP BY scans.
_POLL_SECONDS = 5 * 60
# Small delay before the first check so startup stays snappy.
_FIRST_DELAY = 45


def _state_path():
    return root_dir() / "notify_state.json"


class NotificationManager:
    def __init__(self, db, config, notify_fn: Callable[[str, str], None]):
        self._db = db
        self._config = config
        self._notify = notify_fn
        self._timer: Optional[threading.Timer] = None
        self._state = self._load_state()
        # First-run guard: if we have never recorded any known achievements, seed
        # silently on the first pass so we don't dump a wall of toasts at once.
        self._seeded = "known" in self._state

    # ---- persistence ----------------------------------------------------
    def _load_state(self) -> dict:
        try:
            return json.loads(_state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        try:
            p = _state_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(self._state, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        except OSError:
            log.exception("Saving notify state failed")

    # ---- lifecycle ------------------------------------------------------
    def start(self) -> None:
        self._schedule(_FIRST_DELAY)

    def _schedule(self, delay: float) -> None:
        self._timer = threading.Timer(delay, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.cancel()

    def _tick(self) -> None:
        try:
            if self._config.notify_enabled:
                self._check()
        except Exception:
            log.exception("Notification check failed")
        finally:
            self._schedule(_POLL_SECONDS)

    # ---- the actual milestone logic ------------------------------------
    def _check(self) -> None:
        g = stats.gamify(self._db, self._config.daily_goal,
                         self._config.weekly_goal, self._config.monthly_goal)
        unlocked = [a for a in g["achievements"] if a["unlocked"]]
        unlocked_ids = [a["id"] for a in unlocked]
        known = set(self._state.get("known", []))
        dirty = False
        # Whether this is the silent first-ever pass (seed state, no toasts).
        silent = not self._seeded

        if not self._seeded:
            # First time we run on this machine: remember the current state but
            # stay quiet (the user has presumably had these for a while).
            self._state["known"] = unlocked_ids
            self._seeded = True
            dirty = True
        else:
            fresh = [a for a in unlocked if a["id"] not in known]
            # Cap so a big import / first big session can't spam dozens of toasts.
            for a in fresh[:3]:
                self._notify("🎉 成就达成 · " + a["name"], a["desc"])
            if len(fresh) > 3:
                self._notify("🎉 又解锁了多个成就",
                             f"本次共解锁 {len(fresh)} 个成就，打开看板的「趣味」页看看。")
            if fresh:
                self._state["known"] = unlocked_ids
                dirty = True

        dirty |= self._check_goal(
            "daily", g["today_chars"], g["daily_goal"],
            datetime.now().strftime("%Y-%m-%d"),
            "✅ 今日目标达成！",
            lambda goal: f"今天已经写了 {g['today_chars']:,} 字，达成每日目标 {goal:,} 字。", silent)
        dirty |= self._check_goal(
            "weekly", g["week_chars"], g["weekly_goal"],
            datetime.now().strftime("%Y-W%U"),
            "🏆 本周目标达成！",
            lambda goal: f"本周累计 {g['week_chars']:,} 字，达成每周目标 {goal:,} 字。", silent)
        dirty |= self._check_goal(
            "monthly", g["month_chars"], g["monthly_goal"],
            datetime.now().strftime("%Y-%m"),
            "🌟 本月目标达成！",
            lambda goal: f"本月累计 {g['month_chars']:,} 字，达成每月目标 {goal:,} 字。", silent)

        if dirty:
            self._save_state()

    def _check_goal(self, key, value, goal, period_key, title, body_fn, silent) -> bool:
        """Notify once per period when ``value`` reaches ``goal``. Returns True if
        state changed (so the caller knows to persist)."""
        if not goal or value < goal:
            return False
        done = self._state.get("goal_periods", {})
        if done.get(key) == period_key:
            return False
        done[key] = period_key
        self._state["goal_periods"] = done
        # Don't fire goal toasts during the silent first-run seed pass.
        if not silent:
            self._notify(title, body_fn(goal))
        return True
