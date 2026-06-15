"""DuckType branding -- a single place that resolves the little duck icon.

The bundled PNG/ICO is generated from assets/duck.png by tools/make_icon.py. The
code-drawn duck remains as a fallback for development checkouts without assets.
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageEnhance

# Palette
_POND = (210, 236, 255, 255)        # soft blue background
_POND_OFF = (208, 214, 222, 255)    # muted background when paused
_BODY = (255, 206, 51, 255)         # duck yellow
_BODY_OFF = (203, 197, 168, 255)    # greyed yellow when paused
_WING = (242, 184, 28, 255)
_WING_OFF = (176, 170, 146, 255)
_BEAK = (255, 140, 38, 255)
_EYE = (40, 40, 48, 255)


def duck_image(size: int = 256, active: bool = True) -> Image.Image:
    """Return an RGBA image of the duck at ``size`` x ``size`` pixels."""
    # Supersample for smooth edges, then downscale.
    ss = 4
    s = size * ss
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def box(x0, y0, x1, y1):
        return [x0 * s, y0 * s, x1 * s, y1 * s]

    pond = _POND if active else _POND_OFF
    body = _BODY if active else _BODY_OFF
    wing = _WING if active else _WING_OFF

    # Pond background disc.
    d.ellipse(box(0.02, 0.02, 0.98, 0.98), fill=pond)
    # Body.
    d.ellipse(box(0.18, 0.46, 0.82, 0.84), fill=body)
    # Head.
    d.ellipse(box(0.50, 0.22, 0.84, 0.56), fill=body)
    # Wing.
    d.ellipse(box(0.30, 0.55, 0.58, 0.74), fill=wing)
    # Beak (triangle pointing right).
    d.polygon(
        [(0.82 * s, 0.36 * s), (0.97 * s, 0.40 * s), (0.82 * s, 0.45 * s)],
        fill=_BEAK,
    )
    # Eye + highlight.
    d.ellipse(box(0.66, 0.31, 0.71, 0.36), fill=_EYE)
    d.ellipse(box(0.673, 0.318, 0.690, 0.335), fill=(255, 255, 255, 255))

    return img.resize((size, size), Image.LANCZOS)


def ico_images() -> list:
    """The set of square sizes Windows expects inside a .ico."""
    return [duck_image(sz, active=True) for sz in (256, 128, 64, 48, 32, 16)]


def _dim(img: Image.Image) -> Image.Image:
    """Greyed-out version used for the 'paused' tray state."""
    grey = ImageEnhance.Color(img).enhance(0.25)
    return ImageEnhance.Brightness(grey).enhance(0.8)


def app_image(size: int = 64, active: bool = True) -> Image.Image:
    """Tray/app icon. Prefers bundled custom art, then falls back to code duck."""
    try:
        from .paths import icon_path, icon_png_path  # local import avoids cycles
        for p in (icon_png_path(), icon_path()):
            if p.exists():
                img = Image.open(p).convert("RGBA").resize((size, size), Image.LANCZOS)
                return img if active else _dim(img)
    except Exception:
        pass
    return duck_image(size, active)
