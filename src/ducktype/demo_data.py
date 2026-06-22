"""Synthetic sample data so the dashboard can be explored (and screenshotted)
before any real input has been captured.

The demo database is a throwaway SQLite file in the system temp directory. It is
never the live database: the dashboard swaps to it only while "演示模式" is on
(see ``dashboard.api.Api.demo_set``) and the user's real records are untouched.
"""
from __future__ import annotations

import os
import random
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from .storage import Database

# Plausible host applications for a Chinese writer's day.
_APPS = ["WeChat.exe", "Code.exe", "WINWORD.EXE", "Obsidian.exe", "msedge.exe", "Notepad.exe"]

# The 关注词 (tracked terms) shown in demo mode. These appear verbatim in the
# corpus below, so the 词库 page's 关注词 pie and the 关注词 search page both have
# data even though the user hasn't configured any real tracked terms.
DEMO_TRACKED_TERMS = ["码字鸭", "晨光计划", "张小满"]

# Short fragments sampled into "runs". Deliberately reuse common words (今天 /
# 我们 / 项目 / 记录…) so the frequency panels look alive, sprinkle in a name
# (张小满) and project codenames (码字鸭 / 晨光计划) so 关注词 and 主题 have
# something to show, and weave in a handful of real 成语 (画龙点睛 / 水到渠成 …)
# so the 词库 page's 成语 pie matches the built-in idiom dictionary.
_CORPUS = [
    "今天的进度比预期顺利",
    "我们把这个想法记录下来",
    "晨光计划的第一版方案已经写完了",
    "张小满说下午再开一次短会",
    "码字鸭最近的统计很有意思",
    "把草稿整理成一篇正式的文章",
    "其实我更喜欢安静的清晨写作",
    "这段时间的输入主要在文档里",
    "周末打算把笔记重新归档一遍",
    "先列一个大纲再慢慢填内容",
    "他的反馈让方案清楚了不少",
    "晚上把今天的想法都敲进去",
    "项目的关键节点要提前确认",
    "我觉得这句话还可以再打磨",
    "记录每一个字都是一种坚持",
    "张小满负责整理会议纪要",
    "把复杂的问题拆成几个小步骤",
    "今天的高产时段是上午十点",
    "这个词最近出现得越来越多",
    "慢一点写也没关系重要的是连贯",
    "码字鸭帮我看清了写作习惯",
    "下一阶段重点是打磨细节",
    "把零散的灵感汇成一条主线",
    "我们约定明天继续推进",
    "认真写下的句子值得被保存",
    "晨光计划需要一份简短的总结",
    "其实坚持本身就是一种成果",
    "把今天的结论先记在文档开头",
    "这次的修改比上次干净很多",
    "傍晚的思路往往更发散一些",
    "好的结尾常常需要画龙点睛的一笔",
    "准备得足够充分自然水到渠成",
    "这段开场白我反复字斟句酌",
    "趁着思路顺畅一气呵成写完初稿",
    "解释得太多反而画蛇添足",
    "把每一处细节都精益求精",
    "学东西要循序渐进急不得",
    "灵感都是日积月累攒出来的",
    "晨光计划的总结也要字斟句酌",
    "码字鸭的报告让进步日积月累看得见",
]

# Hour-of-day weights: light overnight, busy morning and evening.
_HOUR_WEIGHTS = (
    [1, 1, 1, 1, 1, 2, 3, 5, 8, 12, 13, 11, 7, 8, 10, 11, 10, 9, 8, 10, 12, 11, 7, 3]
)


def demo_db_path() -> Path:
    return Path(tempfile.gettempdir()) / "ducktype_demo.db"


def _weighted_hour(rng: random.Random) -> int:
    return rng.choices(range(24), weights=_HOUR_WEIGHTS, k=1)[0]


def _seed(con, seed: int = 20240619) -> None:
    rng = random.Random(seed)
    now = datetime.now()
    chars: list = []
    keys: list = []
    for back in range(44, -1, -1):              # ~45 days up to today
        day = now - timedelta(days=back)
        if back != 0 and rng.random() < 0.18:   # leave some quiet days (never today)
            continue
        for _ in range(rng.randint(1, 4)):      # a few sessions per active day
            hour = _weighted_hour(rng)
            t = day.replace(hour=hour, minute=rng.randint(0, 59),
                            second=rng.randint(0, 59), microsecond=0).timestamp()
            # don't place a session in the future on the current day
            if t > now.timestamp():
                t = now.timestamp() - rng.uniform(60, 3600)
            app = rng.choice(_APPS)
            for _ in range(rng.randint(2, 8)):  # several runs per session
                frag = rng.choice(_CORPUS)
                for ch in frag:
                    t += rng.uniform(0.12, 0.45)
                    chars.append((t, ch, app))
                    if rng.random() < 0.05:
                        keys.append((t + 0.05, "backspace", app))
                if rng.random() < 0.3:
                    keys.append((t + 0.1, "enter", app))
                t += rng.uniform(3.5, 25)        # gap > default run_gap -> new run
    con.executemany("INSERT INTO char_events(ts, ch, app) VALUES (?,?,?)", chars)
    con.executemany("INSERT INTO key_events(ts, kind, app) VALUES (?,?,?)", keys)
    con.commit()


def build_demo_database() -> Database:
    """Create (overwriting any previous one) and seed the demo database. The
    returned :class:`Database` is read-only in practice -- its writer thread is
    never started -- which is all the dashboard's read endpoints need."""
    path = demo_db_path()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(str(path) + suffix)
        except OSError:
            pass
    db = Database(path)
    con = db.connect()
    try:
        _seed(con)
    finally:
        con.close()
    db.revision += 1
    return db
