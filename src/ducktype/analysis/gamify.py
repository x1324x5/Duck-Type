"""Streaks, goal progress and the achievement ladder for the gamification panel."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Tuple

from .fun import _is_uncommon
from .statutil import EASTER_EGG_QUOTE


def _daily_map(db) -> Dict[str, int]:
    con = db.connect()
    try:
        rows = con.execute(
            "SELECT date(ts,'unixepoch','localtime') d, COUNT(*) c "
            "FROM char_events GROUP BY d"
        ).fetchall()
    finally:
        con.close()
    return {d: c for d, c in rows}


def _streak(daymap: Dict[str, int]) -> Tuple[int, int]:
    """(current, best) run of consecutive active days. Current counts back from
    today, tolerating that today itself may not have activity yet."""
    if not daymap:
        return 0, 0
    days = sorted(daymap)
    best = run = 1
    prev = datetime.strptime(days[0], "%Y-%m-%d")
    for d in days[1:]:
        cur = datetime.strptime(d, "%Y-%m-%d")
        run = run + 1 if (cur - prev).days == 1 else 1
        best = max(best, run)
        prev = cur

    today = datetime.now().date()
    current = 0
    cursor = today
    while cursor.strftime("%Y-%m-%d") in daymap:
        current += 1
        cursor = cursor - timedelta(days=1)
    if current == 0:  # nothing today -> maybe the streak ended yesterday
        cursor = today - timedelta(days=1)
        while cursor.strftime("%Y-%m-%d") in daymap:
            current += 1
            cursor = cursor - timedelta(days=1)
    return current, best


# (id, 名称, 描述, 指标键, 阈值, 分类)
_ACHIEVEMENTS = [
    # 累计字数 total
    ("first_word", "破壳而出", "记录下第一个字", "total", 1, "字数"),
    ("k1", "牛刀小试", "累计 1,000 字", "total", 1_000, "字数"),
    ("k5", "初露锋芒", "累计 5,000 字", "total", 5_000, "字数"),
    ("k10", "出口成章", "累计 10,000 字", "total", 10_000, "字数"),
    ("k50", "笔耕不辍", "累计 50,000 字", "total", 50_000, "字数"),
    ("k100", "著作等身", "累计 100,000 字", "total", 100_000, "字数"),
    ("k300", "学富五车", "累计 300,000 字", "total", 300_000, "字数"),
    ("k1m", "百万雄师", "累计 1,000,000 字", "total", 1_000_000, "字数"),
    # 不同汉字 distinct
    ("distinct100", "初识百字", "用过 100 个不同的字", "distinct", 100, "汉字"),
    ("distinct500", "博览群字", "用过 500 个不同的字", "distinct", 500, "汉字"),
    ("distinct1500", "胸有千壑", "用过 1,500 个不同的字", "distinct", 1_500, "汉字"),
    ("distinct3000", "万象包罗", "用过 3,000 个不同的字", "distinct", 3_000, "汉字"),
    ("hapax100", "偶遇百字", "有 100 个字只出现过一次", "hapax_count", 100, "汉字"),
    ("hapax500", "字海拾贝", "有 500 个字只出现过一次", "hapax_count", 500, "汉字"),
    # 生僻字 rare chars
    ("rare10", "识字冷门派", "用过 10 个生僻字", "rare_distinct", 10, "生僻"),
    ("rare30", "冷字收藏家", "用过 30 个生僻字", "rare_distinct", 30, "生僻"),
    ("rare100", "异体寻踪", "用过 100 个生僻字", "rare_distinct", 100, "生僻"),
    ("rare_total100", "冷门常客", "累计输入生僻字 100 次", "rare_total", 100, "生僻"),
    ("rare_total500", "字库探险", "累计输入生僻字 500 次", "rare_total", 500, "生僻"),
    # 单字重复 single character
    ("char100", "一字百遍", "同一个字累计输入 100 次", "char_max", 100, "单字"),
    ("char500", "念念不忘", "同一个字累计输入 500 次", "char_max", 500, "单字"),
    ("char1000", "千锤百炼", "同一个字累计输入 1,000 次", "char_max", 1_000, "单字"),
    # 趣味彩蛋
    ("duck10", "鸭鸭报到", "「鸭」字累计出现 10 次", "duck_count", 10, "趣味"),
    ("duck100", "鸭力全开", "「鸭」字累计出现 100 次", "duck_count", 100, "趣味"),
    ("duck500", "鸭王之王", "「鸭」字累计出现 500 次", "duck_count", 500, "趣味"),
    # 连续天数 streak
    ("streak3", "小有恒心", "连续 3 天码字", "streak", 3, "连续"),
    ("streak7", "持之以恒", "连续 7 天码字", "streak", 7, "连续"),
    ("streak14", "习惯成形", "连续 14 天码字", "streak", 14, "连续"),
    ("streak30", "铁杵成针", "连续 30 天码字", "streak", 30, "连续"),
    ("streak100", "百日筑基", "连续 100 天码字", "streak", 100, "连续"),
    # 累计活跃天数 active_days
    ("days7", "崭露头角", "累计 7 天有记录", "active_days", 7, "活跃"),
    ("days30", "月度常客", "累计 30 天有记录", "active_days", 30, "活跃"),
    ("days100", "百炼成钢", "累计 100 天有记录", "active_days", 100, "活跃"),
    ("days365", "周年陪伴", "累计 365 天有记录", "active_days", 365, "活跃"),
    # 单日字数 day_max
    ("day1k", "文思泉涌", "单日码字过千", "day_max", 1_000, "单日"),
    ("day5k", "倚马可待", "单日码字过五千", "day_max", 5_000, "单日"),
    ("day10k", "日破万言", "单日码字过万", "day_max", 10_000, "单日"),
    # 看板语录 quote views (distinct / total / egg)
    ("quote_d50", "初拾珠玑", "读过 50 条不同的语录", "quotes_distinct", 50, "语录"),
    ("quote_d200", "渐入佳境", "读过 200 条不同的语录", "quotes_distinct", 200, "语录"),
    ("quote_d500", "博览群句", "读过 500 条不同的语录", "quotes_distinct", 500, "语录"),
    ("quote_v200", "日积月累", "累计看过 200 次语录", "quotes_total", 200, "语录"),
    ("quote_v1000", "手不释卷", "累计看过 1,000 次语录", "quotes_total", 1_000, "语录"),
    ("quote_egg", "一片留白", "在滚动语录里遇见一片空白", "quotes_egg", 1, "语录"),
]


def _week_month_chars(daymap: Dict[str, int]) -> Tuple[int, int]:
    """Sum of characters in the current calendar week (Mon-Sun) and month."""
    now = datetime.now()
    monday = (now - timedelta(days=now.weekday())).date()
    week = month = 0
    ym = now.strftime("%Y-%m")
    for d, c in daymap.items():
        try:
            dt = datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            continue
        if dt >= monday and dt <= now.date():
            week += c
        if d.startswith(ym):
            month += c
    return week, month


def gamify(db, daily_goal: int, weekly_goal: int = 0, monthly_goal: int = 0) -> Dict:
    """Goal progress + streak + achievement list for the gamification panel."""
    daymap = _daily_map(db)
    total = sum(daymap.values())
    today_key = datetime.now().strftime("%Y-%m-%d")
    today_chars = daymap.get(today_key, 0)
    day_max = max(daymap.values()) if daymap else 0
    current, best = _streak(daymap)
    week_chars, month_chars = _week_month_chars(daymap)

    con = db.connect()
    try:
        distinct = con.execute("SELECT COUNT(DISTINCT ch) FROM char_events").fetchone()[0]
        char_counts = dict(
            con.execute(
                "SELECT ch, COUNT(*) c FROM char_events GROUP BY ch"
            ).fetchall()
        )
    finally:
        con.close()

    from ..storage.db import quote_hash
    q_distinct, q_total, q_egg = db.quote_stats(quote_hash(EASTER_EGG_QUOTE))

    rare_total = sum(n for ch, n in char_counts.items() if _is_uncommon(ch))
    rare_distinct = sum(1 for ch in char_counts if _is_uncommon(ch))
    char_max = max(char_counts.values()) if char_counts else 0
    hapax_count = sum(1 for n in char_counts.values() if n == 1)

    metrics = {"total": total, "distinct": distinct, "streak": best,
               "day_max": day_max, "active_days": len(daymap),
               "rare_total": rare_total, "rare_distinct": rare_distinct,
               "char_max": char_max, "duck_count": char_counts.get("鸭", 0),
               "hapax_count": hapax_count,
               "quotes_distinct": q_distinct, "quotes_total": q_total,
               "quotes_egg": 1 if q_egg else 0}
    achievements = []
    unlocked_ids = []
    for aid, name, desc, key, threshold, category in _ACHIEVEMENTS:
        value = metrics.get(key, 0)
        is_unlocked = value >= threshold
        if is_unlocked:
            unlocked_ids.append(aid)
        achievements.append({
            "id": aid, "name": name, "desc": desc, "category": category,
            "unlocked": is_unlocked,
            "progress": min(1.0, round(value / threshold, 4)) if threshold else 1.0,
        })
    # Stamp (and persist) first-unlock times so the page can show them and the
    # frontend can detect freshly-earned achievements for the toast.
    try:
        stamps = db.record_achievements(unlocked_ids)
    except Exception:
        stamps = {}
    for a in achievements:
        a["unlocked_at"] = stamps.get(a["id"])

    goal = max(1, int(daily_goal or 1))
    # Weekly / monthly goals: fall back to a daily-derived target when unset (0).
    import calendar as _cal
    now = datetime.now()
    days_in_month = _cal.monthrange(now.year, now.month)[1]
    wgoal = max(1, int(weekly_goal) if weekly_goal else goal * 7)
    mgoal = max(1, int(monthly_goal) if monthly_goal else goal * days_in_month)
    return {
        "today_chars": today_chars,
        "daily_goal": goal,
        "goal_pct": round(min(today_chars / goal, 99.99), 4),
        "week_chars": week_chars,
        "weekly_goal": wgoal,
        "week_goal_pct": round(min(week_chars / wgoal, 99.99), 4),
        "month_chars": month_chars,
        "monthly_goal": mgoal,
        "month_goal_pct": round(min(month_chars / mgoal, 99.99), 4),
        "streak_current": current,
        "streak_best": best,
        "total_chars": total,
        "unlocked": sum(1 for a in achievements if a["unlocked"]),
        "achievements": achievements,
    }
