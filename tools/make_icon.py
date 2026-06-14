"""Generate src/ducktype/assets/duck.ico (multi-size) from the code-drawn duck.

If you drop a custom square PNG at assets/duck.png it is used instead, so you can
swap in your own duck artwork without touching anything else.

    python tools/make_icon.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image  # noqa: E402

OUT = ROOT / "src" / "ducktype" / "assets" / "duck.ico"
CUSTOM_PNG = ROOT / "assets" / "duck.png"
_SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if CUSTOM_PNG.exists():
        img = Image.open(CUSTOM_PNG).convert("RGBA")
        # Trim the transparent margin so the subject fills the icon, then re-pad
        # to a square with a small breathing-room margin.
        bbox = img.getbbox()
        if bbox:
            img = img.crop(bbox)
        margin = round(max(img.size) * 0.08)
        side = max(img.size) + 2 * margin
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
        # Derive every size from a crisp 256px master.
        canvas = canvas.resize((256, 256), Image.LANCZOS)
        canvas.save(OUT, sizes=_SIZES)
        print(f"Wrote {OUT} from custom {CUSTOM_PNG}")
    else:
        from ducktype.branding import duck_image
        base = duck_image(256, active=True)
        base.save(OUT, sizes=_SIZES)
        print(f"Wrote {OUT} from generated duck")


if __name__ == "__main__":
    main()
