"""Board ticker (rotating data facts + user phrases) and the live mini-counter
numbers. Both are cheap, frequently-polled read paths.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Dict, List, Optional

from .statutil import EASTER_EGG_QUOTE
from .char_stats import total_chars
from .gamify import gamify
from .report_stats import _peak_hour, _top_multichar_word, period_bounds
from .sequence_stats import search
from .word_stats import top_words


# Seed lines written to phrases.txt on first run. A mix of literary,
# philosophical, romantic, cute and trivia/tips.
_DEFAULT_PHRASES = [
    "# DuckType 看板滚动文字 · 每行一句，以 # 开头的行会被忽略。",
    "# DuckType 会把这些句子和自动生成的数据事实一起轮播。",
    "",
    "# —— 文学 / 哲思 ——",
    "每一个字，都是思想落在纸上的脚印。",
    "笔落惊风雨，键响动心弦。",
    "今天写下的每一句，都是明天的回忆。",
    "字句汇成河，日久见汪洋。",
    "慢慢写，认真写，字会记得你的用心。",
    "一个人真正走远的时候，常常不是脚步先动，而是心先安静下来。",
    "人总要在某个清晨，原谅昨夜那个想太多的自己。",
    "答案有时不是被找到的，而是在一次次追问里慢慢长出来的。",
    "所谓成熟，大概是把许多话咽下去以后，仍然愿意温柔地开口。",
    "时间不回答问题，它只是把问题变成经历。",
    "很多事当时像山，后来回头看，不过是一段上坡路。",
    "真正重要的东西，往往不急着证明自己。",
    "生活不是把日子过成结论，而是在细节里慢慢练习理解。",
    "人会被一句话点亮，也会被长久的沉默照见自己。",
    "有些风景必须走过一段孤独，才看得出它的辽阔。",
    "别急着成为谁，先认真听见自己。",
    "世界很吵，能把心安放好，本身就是一种本事。",
    "念念不忘，不一定会有回响，但一定会改变回望的人。",
    "真正的告别，不是删掉名字，而是想起时不再慌张。",
    "许多遗憾后来都变成了方向，提醒我们下一次怎样珍惜。",
    "最深的理解，常常不是赞同，而是愿意多停留一会儿。",
    "命运有时像一条河，你不能命令它转弯，但可以学会划船。",
    "人这一生，总要学会在无解处继续生活。",
    "把平凡的一天过认真，就是在替未来保存证据。",
    "热爱不是永远沸腾，而是冷下来以后仍愿意靠近。",
    "",
    "# —— 爱情 / 时间 ——",
    "爱情这东西，时间很关键，认识得太早或太晚，都不行。——《2046》",
    "世上最遥远的距离，不是生与死，而是我就站在你面前，你却不知道我爱你。——《荷包里的单人床》张小娴",
    "我是天空里的一片云，偶尔投影在你的波心。——《偶然》徐志摩",
    "你我相逢在黑夜的海上，你有你的，我有我的，方向。——《偶然》徐志摩",
    "你记得也好，最好你忘掉，在这交会时互放的光亮。——《偶然》徐志摩",
    "有些人渐渐不联系了，不是淡了远了，而是没有合适的身份陪伴，没有合适的理由联系，没有合适的机会见面。",
    "有些人只能放在心里，偶尔回忆，经常想念。",
    "爱不是把一个人留在身边，而是在想起时仍愿意祝他天晴。",
    "错过有时不是惩罚，只是时间用另一种方式保存了温柔。",
    "相遇是两条河短暂并行，告别是各自奔向更宽阔的海。",
    "最好的喜欢，不是急着占有，而是愿意让对方成为自己。",
    "有人教会你爱，也有人教会你把爱放回人海。",
    "爱一个人最难的部分，可能是承认他不必按照你的期待生活。",
    "心动是一瞬间的光，长久相处才知道那束光能不能照路。",
    "有些名字不再提起，不是忘了，而是终于学会轻轻放好。",
    "时间会筛掉很多热闹，留下真正愿意并肩的人。",
    "爱若只剩执念，就该让风替它松一松手。",
    "相爱的人未必总能抵达，但真诚的片刻不会白白发生。",
    "所有来不及说出口的话，后来都在某个夜里变成了月光。",
    "人和人的缘分，常常是深一脚浅一脚地走到某个路口。",
    "爱不是答案，它更像一道题，让人一次次重新认识自己。",
    "愿你遇见的人，既懂你的沉默，也珍惜你的开口。",
    "",
    "# —— 写作 / 思考 ——",
    "写作不是把心事说尽，而是给混乱留出秩序。",
    "每一次敲键，都是把无形的念头请到人间坐一会儿。",
    "语言有边界，但沉默太辽阔，所以我们才需要写字。",
    "一个词被反复使用，可能是生活正在反复叩门。",
    "把想法写下来，是给未来的自己留一盏灯。",
    "如果今天没有答案，就先把问题写清楚。",
    "文字不一定能改变世界，但能让一个人不被世界轻易带走。",
    "思考不是为了赢过别人，而是为了少误会一点自己。",
    "好句子像窗，推开以后，心里有风。",
    "慢一点也没关系，重要的是别把自己的声音弄丢。",
    "真正的表达，是把复杂的心事交给清楚的句子。",
    "日子会过去，写下来的东西会替你留下来。",
    "",
    "# —— 冷知识 / 小贴士 ——",
    "「的」是现代汉语里使用频率最高的字。",
    "小贴士：点击高频字 / 词的条形图，可直接查看它的详情。",
    "小贴士：拖动高频面板上的滑条，可以看到更多名次。",
    "小贴士：「按小时」图可单独切换今天 / 近 24 小时 / 近 7 天。",
    "小贴士：每张图右上角的 ⬇ 可把图表存成图片。",
    "成语「一目十行」形容读得快——那你打字有多快呢？",
    "汉字数量逾八万，但日常常用的不过三千余个。",
    EASTER_EGG_QUOTE,
]


def _phrase_lines(lines: List[str]) -> List[str]:
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


def load_phrases() -> List[str]:
    """Read local rotating phrases and merge in built-in defaults.

    Blank lines and ``#`` comments are ignored. Any read/write error degrades to
    the built-in defaults so the ticker never breaks the dashboard.
    """
    from ..paths import phrases_path
    p = phrases_path()
    try:
        from .quote_bank import QUOTES as quote_bank
    except Exception:
        quote_bank = ()
    defaults = _phrase_lines(_DEFAULT_PHRASES) + list(quote_bank)
    try:
        if not p.exists():
            p.write_text("\n".join(_DEFAULT_PHRASES) + "\n", encoding="utf-8")
        lines = p.read_text(encoding="utf-8").splitlines()
    except Exception:
        return defaults

    out: List[str] = []
    seen = set()
    for phrase in _phrase_lines(lines) + defaults:
        if phrase not in seen:
            out.append(phrase)
            seen.add(phrase)
    return out


def _since_last_word(db, run_gap: float) -> Optional[str]:
    """A "remember when" stat: when you last typed a notable word, and how many
    characters you've committed since. Returns None if there's nothing to show."""
    import random
    import time as _time
    words = [w for w, _c in top_words(db, None, 60, run_gap) if len(w) >= 2]
    if not words:
        return None
    word = random.choice(words[:40])
    r = search(db, word, None, run_gap)
    if not r["total"] or not r["last_seen"]:
        return None
    hours = (_time.time() - r["last_seen"]) / 3600.0
    after = total_chars(db, r["last_seen"])
    when = "不到 1 小时前" if hours < 1 else f"约 {hours:.0f} 小时前"
    return f"你上次打出「{word}」是在{when}，之后又码了 {after:,} 字。"


def ticker(db, run_gap: float, session_gap: float, daily_goal: int) -> Dict:
    """Content for the board ticker: code-generated data facts + user phrases."""
    facts: List[str] = []
    try:
        g = gamify(db, daily_goal)
        today, goal = g["today_chars"], g["daily_goal"]
        if today > 0:
            facts.append(
                f"今天已输入 {today:,} 字，已经达成今日目标。" if today >= goal
                else f"今天已输入 {today:,} 字，距离今日目标还差 {goal - today:,} 字。")
        if g["streak_current"] > 0:
            facts.append(f"已连续码字 {g['streak_current']} 天，最长纪录 {g['streak_best']} 天。")
        if g["total_chars"] > 0:
            facts.append(f"到目前为止，你一共码了 {g['total_chars']:,} 个汉字。")
        nxt = next((a for a in g["achievements"] if not a["unlocked"]), None)
        if nxt:
            facts.append(f"继续积累，就能解锁成就「{nxt['name']}」：{nxt['desc']}。")
        since, until, _ps, _pe, _lbl = period_bounds("today")
        ph, _cnt = _peak_hour(db, since, until)
        if ph is not None:
            facts.append(f"今天 {ph:02d}:00 时段你最高产。")
        fw = _top_multichar_word(db, since, until, run_gap)
        if fw:
            facts.append(f"今天你最常用的词是「{fw}」。")
        sl = _since_last_word(db, run_gap)
        if sl:
            facts.append(sl)
    except Exception:
        pass
    return {"facts": facts, "phrases": load_phrases()}


def mini_stats(db, session_gap: float = 60.0, daily_goal: int = 500) -> Dict:
    """Live numbers for the floating mini counter (item 4). Cheap, polled ~1s.

    - speed_cpm: a time-decayed instantaneous typing rate (chars/min). Each
      recent commit contributes exp(-Δt/TAU); the sum ÷ TAU estimates the
      current rate, so the value is *continuous* (no coarse 6-cpm steps), reacts
      quickly, and decays smoothly to 0 on idle. Requires >=2 chars in a short
      window, so a single isolated keystroke reads as 0 (one char has no rate).
    - session_chars: characters in the current continuous session (walking back
      from the latest commit while gaps stay within session_gap).
    - today_chars / goal_pct: progress toward the daily goal."""
    now = datetime.now().timestamp()
    midnight = datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()
    con = db.connect()
    try:
        today = con.execute(
            "SELECT COUNT(*) FROM char_events WHERE ts >= ?", (midnight,)
        ).fetchone()[0]
        rows = con.execute(
            "SELECT ts FROM char_events ORDER BY ts DESC LIMIT 5000"
        ).fetchall()
    finally:
        con.close()
    # decay-weighted current rate (see docstring)
    TAU = 6.0            # seconds; decay constant
    GUARD_WIN = 12.0     # need >=2 chars within this window to register any speed
    recent = 0
    weighted = 0.0
    for (ts,) in rows:
        dt = now - ts
        if dt > 40.0:        # rows are DESC -> everything older follows; stop
            break
        if dt <= GUARD_WIN:
            recent += 1
        weighted += math.exp(-dt / TAU)
    speed_cpm = round(min(weighted / TAU * 60.0, 600.0), 1) if recent >= 2 else 0.0
    session_chars = 0
    session_start = None
    if rows and (now - rows[0][0]) <= session_gap:
        session_chars = 1
        prev = session_start = rows[0][0]
        for (ts,) in rows[1:]:
            if prev - ts > session_gap:
                break
            session_chars += 1
            session_start = prev = ts
    # elapsed since the session's first keystroke (0 when no live session)
    session_seconds = int(now - session_start) if session_start is not None else 0
    goal_pct = (today / daily_goal) if daily_goal else 0.0
    return {
        "speed_cpm": speed_cpm,
        "session_chars": session_chars,
        "session_seconds": session_seconds,
        "today_chars": today,
        "daily_goal": daily_goal,
        "goal_pct": round(min(goal_pct, 99.99), 4),
    }
