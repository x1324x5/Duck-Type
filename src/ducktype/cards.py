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
    if rep.get("peak_hour") is not None:
        rows.append(("高峰时段", f"{rep['peak_hour']:02d}:00 时段"))
    if rep.get("fav_word"):
        rows.append(("最常用词", rep["fav_word"]))
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
    return rows[:4]


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
    foot = "DuckType · 码字鸭"
    fnt = _font(34)
    tw = d.textlength(foot, font=fnt)
    duck_sm = app_image(40, active=True)
    total = 40 + 12 + tw
    fx = (_W - total) / 2
    img.paste(duck_sm, (int(fx), _H - 80), duck_sm)
    d.text((fx + 52, _H - 60), foot, font=fnt, fill=_MUTED, anchor="lm")
    return img.convert("RGB")
