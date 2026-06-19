"""Render shareable PNG report cards (for 朋友圈 / X / GitHub README).

Drawn server-side with Pillow so it works offline and matches the app's look.
Chinese text needs a CJK font; we try the common Windows ones and fall back
gracefully.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from .branding import app_image

_W = _H = 1080
_BG_TOP = (27, 35, 48)
_BG_BOTTOM = (15, 18, 22)
_ACCENT = (255, 206, 51)
_FG = (231, 236, 243)
_MUTED = (154, 167, 184)

_CJK_FONTS = ["msyh.ttc", "msyhbd.ttc", "simhei.ttf", "Deng.ttf", "simsun.ttc"]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = (["msyhbd.ttc", "msyh.ttc"] if bold else _CJK_FONTS)
    for name in names + _CJK_FONTS:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _vgradient(w: int, h: int, top, bottom) -> Image.Image:
    img = Image.new("RGB", (w, h), top)
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line(
            [(0, y), (w, y)],
            fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)),
        )
    return img


def _rows_for(rep: dict) -> List[Tuple[str, str]]:
    """Pick the most interesting lines for each period."""
    period = rep.get("period")
    rows: List[Tuple[str, str]] = []
    # Lead with the most share-worthy word stats (computed in report_heavy).
    if rep.get("new_word_count"):
        rows.append(("本期新词", f"{rep['new_word_count']} 个"))
    if rep.get("peak_hour") is not None:
        rows.append(("高峰时段", f"{rep['peak_hour']:02d}:00 时段"))
    if period == "year" and rep.get("busiest_week"):
        rows.append(("年度高峰周", f"{rep['busiest_week']} · {rep['busiest_week_count']} 字"))
    if period in ("month", "year") and rep.get("busiest_weekday"):
        rows.append(("最忙星期", f"{rep['busiest_weekday']} · {rep['busiest_weekday_count']} 字"))
    if period in ("month", "year") and rep.get("quietest_weekday"):
        rows.append(("最闲星期", f"{rep['quietest_weekday']} · {rep['quietest_weekday_count']} 字"))
    if rep.get("fav_word") or rep.get("top_bigram"):
        rows.append(("最常用词", rep.get("fav_word") or rep["top_bigram"]))
    if rep.get("fav_char"):
        rows.append(("最爱的字", rep["fav_char"]))
    if period in ("week", "month", "year") and rep.get("top_app"):
        rows.append(("主力应用", f"{rep['top_app']} · {rep['top_app_share']}%"))
    if rep.get("best_day"):
        rows.append(("最高产日", f"{rep['best_day']}（{rep['best_day_count']} 字）"))
    if period in ("month", "year") and rep.get("longest_session_min"):
        rows.append(("最长连续输入", f"{rep['longest_session_min']:.0f} 分钟"))
    if period == "year" and rep.get("streak_best"):
        rows.append(("最长连续天数", f"{rep['streak_best']} 天"))
    return rows[:5]


def _fit_text(d: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
              max_width: int) -> str:
    """Trim a single-line label so right-aligned values never crash into labels."""
    if d.textlength(text, font=font) <= max_width:
        return text
    ell = "…"
    while text and d.textlength(text + ell, font=font) > max_width:
        text = text[:-1]
    return text + ell if text else ell


def render_card(rep: dict) -> Image.Image:
    img = _vgradient(_W, _H, _BG_TOP, _BG_BOTTOM).convert("RGBA")
    d = ImageDraw.Draw(img)

    # accent bar
    d.rectangle([0, 0, _W, 12], fill=_ACCENT)

    # duck
    duck = app_image(150, active=True)
    img.paste(duck, ((_W - 150) // 2, 70), duck)

    # title
    d.text((_W / 2, 260), rep.get("label", "报告"), font=_font(60, True),
           fill=_FG, anchor="mm")

    # big number
    d.text((_W / 2, 410), f"{rep.get('chars', 0):,}", font=_font(170, True),
           fill=_ACCENT, anchor="mm")
    d.text((_W / 2, 520), "个汉字", font=_font(40), fill=_MUTED, anchor="mm")

    # delta
    delta = rep.get("delta_pct")
    if delta is not None:
        sign = "↑" if delta >= 0 else "↓"
        col = (54, 211, 153) if delta >= 0 else (255, 107, 129)
        d.text((_W / 2, 575), f"{sign} 较上一周期 {abs(delta)}%",
               font=_font(34), fill=col, anchor="mm")

    # stat rows
    y = 640
    for label, value in _rows_for(rep):
        d.text((150, y), label, font=_font(38), fill=_MUTED, anchor="lm")
        fnt = _font(38, True)
        value = _fit_text(d, str(value), fnt, 520)
        d.text((_W - 150, y), value, font=fnt, fill=_FG, anchor="rm")
        y += 68

    # keywords (month/year)
    kws = rep.get("keywords") or []
    if kws and rep.get("period") in ("month", "year") and y < _H - 170:
        d.text((150, y + 6), "关键词", font=_font(38), fill=_MUTED, anchor="lm")
        kw = _fit_text(d, " · ".join(kws[:4]), _font(32, True), 520)
        d.text((_W - 150, y + 6), kw, font=_font(32, True), fill=_ACCENT, anchor="rm")

    # footer (a small duck glyph drawn, not an emoji, so any font renders fine)
    _footer(img, d, _W, _H)
    return img.convert("RGB")


# ---- long share image (周报长图 / 年度回顾 / 项目维度) ---------------------
_LW, _LH = 1080, 1920
_CARD_BG = (33, 42, 56)


def _footer(img: Image.Image, d: ImageDraw.ImageDraw, w: int, h: int) -> None:
    foot = "DuckType · 码字鸭"
    fnt = _font(34)
    tw = d.textlength(foot, font=fnt)
    duck_sm = app_image(40, active=True)
    total = 40 + 12 + tw
    fx = (w - total) / 2
    img.paste(duck_sm, (int(fx), h - 80), duck_sm)
    d.text((fx + 52, h - 60), foot, font=fnt, fill=_MUTED, anchor="lm")


def _round_panel(d: ImageDraw.ImageDraw, box, radius=24, fill=_CARD_BG) -> None:
    d.rounded_rectangle(box, radius=radius, fill=fill)


def _daily_chart(d: ImageDraw.ImageDraw, box, series) -> None:
    """Simple bar chart of (date, count) drawn with rectangles."""
    x0, y0, x1, y1 = box
    counts = [int(c or 0) for _dd, c in series] or [0]
    mx = max(counts) or 1
    n = len(counts)
    gap = 4
    bw = max(2, (x1 - x0 - gap * (n - 1)) / max(n, 1))
    for i, c in enumerate(counts):
        bh = (c / mx) * (y1 - y0)
        bx = x0 + i * (bw + gap)
        d.rounded_rectangle([bx, y1 - bh, bx + bw, y1], radius=3, fill=_ACCENT)


def _hbars(d: ImageDraw.ImageDraw, x, y, w, rows, row_h=58, label_w=300):
    """Horizontal labelled bars: rows is [(label, value)]."""
    if not rows:
        return y
    mx = max(v for _l, v in rows) or 1
    for label, value in rows:
        d.text((x, y + row_h / 2), _fit_text(d, str(label), _font(34), label_w - 16),
               font=_font(34), fill=_FG, anchor="lm")
        bx = x + label_w
        bw = (w - label_w - 120) * (value / mx)
        d.rounded_rectangle([bx, y + 10, bx + max(bw, 3), y + row_h - 18],
                            radius=8, fill=_ACCENT)
        d.text((x + w, y + row_h / 2), f"{value:,}", font=_font(32, True),
               fill=_MUTED, anchor="rm")
        y += row_h
    return y


def render_long_card(rep: dict) -> Image.Image:
    """A taller, richer share image: trend chart, behaviour insights, top words
    and a per-application (项目维度) breakdown. Works for any period; it is the
    obvious choice for 周报长图 and 年度回顾."""
    img = _vgradient(_LW, _LH, _BG_TOP, _BG_BOTTOM).convert("RGBA")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, _LW, 12], fill=_ACCENT)

    duck = app_image(120, active=True)
    img.paste(duck, ((_LW - 120) // 2, 56), duck)
    d.text((_LW / 2, 220), rep.get("label", "报告"), font=_font(56, True),
           fill=_FG, anchor="mm")

    d.text((_LW / 2, 350), f"{rep.get('chars', 0):,}", font=_font(150, True),
           fill=_ACCENT, anchor="mm")
    d.text((_LW / 2, 450), "个汉字", font=_font(36), fill=_MUTED, anchor="mm")
    delta = rep.get("delta_pct")
    if delta is not None:
        sign = "↑" if delta >= 0 else "↓"
        col = (54, 211, 153) if delta >= 0 else (255, 107, 129)
        d.text((_LW / 2, 505), f"{sign} 较上一周期 {abs(delta)}%",
               font=_font(32), fill=col, anchor="mm")

    # quick-stat strip
    stats_row = [
        ("活跃天数", f"{rep.get('active_days', 0)} 天"),
        ("不同汉字", f"{rep.get('distinct_chars', 0):,}"),
        ("高峰时段", (f"{rep['peak_hour']:02d}:00" if rep.get("peak_hour") is not None else "—")),
        ("最长连续", (f"{rep.get('longest_session_min', 0):.0f} 分" if rep.get("longest_session_min") else "—")),
    ]
    sx, sw = 60, (_LW - 120) / 4
    _round_panel(d, [sx, 560, _LW - 60, 680])
    for i, (lab, val) in enumerate(stats_row):
        cx = sx + sw * i + sw / 2
        d.text((cx, 600), str(val), font=_font(40, True), fill=_FG, anchor="mm")
        d.text((cx, 650), lab, font=_font(28), fill=_MUTED, anchor="mm")

    y = 720
    # daily trend
    daily = rep.get("daily") or []
    if daily:
        d.text((60, y), "每日产出", font=_font(34, True), fill=_FG, anchor="lm")
        y += 36
        _daily_chart(d, [60, y, _LW - 60, y + 180], daily)
        y += 220

    # behaviour insights (reuse the report's text insights)
    insights = (rep.get("insights") or [])[:3]
    for ins in insights:
        _round_panel(d, [60, y, _LW - 60, y + 132])
        d.text((86, y + 34), ins.get("title", ""), font=_font(34, True), fill=_FG, anchor="lm")
        if ins.get("metric"):
            d.text((_LW - 86, y + 34), str(ins["metric"]), font=_font(34, True),
                   fill=_ACCENT, anchor="rm")
        body = _fit_text(d, ins.get("body", ""), _font(28), _LW - 200)
        d.text((86, y + 88), body, font=_font(28), fill=_MUTED, anchor="lm")
        y += 152

    # top words
    words = rep.get("top_words") or []
    if words and y < _LH - 480:
        d.text((60, y), "高频词", font=_font(34, True), fill=_FG, anchor="lm")
        y += 50
        chip_x, chip_y = 60, y
        for w, c in words[:10]:
            txt = f"{w} {c}"
            tw = d.textlength(txt, font=_font(30))
            cw = tw + 44
            if chip_x + cw > _LW - 60:
                chip_x = 60; chip_y += 64
            _round_panel(d, [chip_x, chip_y, chip_x + cw, chip_y + 50], radius=25)
            d.text((chip_x + cw / 2, chip_y + 25), txt, font=_font(30), fill=_FG, anchor="mm")
            chip_x += cw + 14
        y = chip_y + 84

    # per-application (项目维度) breakdown
    apps = rep.get("apps") or []
    if apps and y < _LH - 240:
        d.text((60, y), "主要输入场景", font=_font(34, True), fill=_FG, anchor="lm")
        y += 56
        y = _hbars(d, 60, y, _LW - 120, [(a, c) for a, c in apps[:5]])

    _footer(img, d, _LW, _LH)
    return img.convert("RGB")
