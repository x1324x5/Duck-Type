from __future__ import annotations

import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8799"
OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

# sidebar data-v -> (output filename, clip height or None for full page).
# The board is the hero shot: clip to the first fold instead of the very long
# full page; the rest read well captured whole.
VIEWS = [
    ("board", "ducktype-dashboard.png", 1280, None),
    ("report", "report.png", None, None),
    ("sequence", "sequence.png", None, None),
    ("fun", "fun.png", None, None),
    ("lexicon", "lexicon.png", None, None),
]


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": 1366, "height": 900},
            device_scale_factor=2,  # 2x so text stays crisp when zoomed
        )
        page = ctx.new_page()

        # enable 演示模式 once (server-side flag persists for later loads)
        page.goto(BASE, wait_until="networkidle")
        page.evaluate(
            "fetch('/api/demo',{method:'POST',headers:{'Content-Type':'application/json'},"
            "body:JSON.stringify({on:true})}).then(r=>r.json())"
        )
        page.wait_for_timeout(800)

        for view, fname, clip_h, rng in VIEWS:
            page.goto(f"{BASE}/#view={view}&clean=1", wait_until="networkidle")
            page.wait_for_timeout(2200)  # let Chart.js settle
            if rng:
                page.click(f'#ranges button[data-r="{rng}"]')
                page.wait_for_timeout(2200)
            page.evaluate("window.scrollTo(0,0)")
            page.wait_for_timeout(200)
            if clip_h:
                page.set_viewport_size({"width": 1366, "height": clip_h})
                page.wait_for_timeout(400)
                page.screenshot(path=str(OUT / fname), full_page=False)
                page.set_viewport_size({"width": 1366, "height": 900})  # restore
            else:
                page.screenshot(path=str(OUT / fname), full_page=True)
            print("saved", fname)

        # mini 随身鸭 — its own small window
        mini = ctx.new_page()
        mini.set_viewport_size({"width": 240, "height": 320})
        mini.goto(f"{BASE}/#mini", wait_until="networkidle")
        mini.wait_for_timeout(2500)
        mini.screenshot(path=str(OUT / "mini.png"), full_page=True)
        print("saved mini.png")

        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
