"""Entry point.

    python -m ducktype              # run in background with tray + dashboard
    python -m ducktype --report     # print a quick text summary and exit
    python -m ducktype --export DIR # dump char/word/sequence data and exit
    python -m ducktype --clear      # delete all captured data and exit
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path


def _report(range_key: str) -> None:
    from .analysis import stats
    from .config import Config
    from .paths import db_path
    from .storage import Database

    cfg = Config.load()
    db = Database(db_path())
    since = stats.since_for(range_key)
    o = stats.overview(db, since, cfg.run_gap_seconds, cfg.session_gap_seconds)
    print(f"=== DuckType ({range_key}) ===")
    print(f"总字数: {o['total_chars']}   不同汉字: {o['distinct_chars']}")
    print(f"输入速度: {o['cpm']} 字/分 (峰值 {o['peak_cpm']})   活跃 {o['active_minutes']} 分")
    print(f"删除键: {o['backspace'] + o['delete']}   修改率: {o['edit_ratio'] * 100:.1f}%")
    print("\n高频字:")
    for ch, c in stats.top_chars(db, since, 15):
        print(f"  {ch}  {c}")
    print("\n高频词:")
    for w, c in stats.top_words(db, since, 15, cfg.run_gap_seconds):
        print(f"  {w}  {c}")


def _export(out_dir: str, range_key: str) -> None:
    from .analysis import stats
    from .config import Config
    from .paths import db_path
    from .storage import Database

    cfg = Config.load()
    db = Database(db_path())
    since = stats.since_for(range_key)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    with open(out / "char_freq.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["char", "count"])
        w.writerows(stats.top_chars(db, since, 100000))

    with open(out / "word_freq.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["word", "count"])
        w.writerows(stats.top_words(db, since, 100000, cfg.run_gap_seconds))

    # The committed-character sequence itself ("打过的词语序列"), one run per line.
    with open(out / "sequence.txt", "w", encoding="utf-8") as f:
        for line in stats.sequence_runs(db, since, cfg.run_gap_seconds):
            f.write(line + "\n")

    print(f"Exported data to {out.resolve()}")


def _clear() -> None:
    from .config import Config  # noqa: F401  (kept for symmetry / future use)
    from .paths import db_path
    from .storage import Database

    db = Database(db_path())
    n = db.clear_all()
    print(f"Deleted {n} recorded characters. DuckType is now empty.")


def main() -> None:
    ap = argparse.ArgumentParser(prog="ducktype", description="输入法汉字键入统计 · 码字鸭")
    ap.add_argument("--report", action="store_true", help="打印文本统计摘要后退出")
    ap.add_argument("--export", metavar="DIR", help="导出字频/词频/序列到目录后退出")
    ap.add_argument("--clear", action="store_true", help="删除全部已记录数据后退出")
    ap.add_argument("--range", default="all", choices=["today", "7d", "30d", "all"],
                    help="统计时间范围 (默认 all)")
    args = ap.parse_args()

    if args.report:
        _report(args.range)
    elif args.export:
        _export(args.export, args.range)
    elif args.clear:
        _clear()
    else:
        from .app import main as run_app
        run_app()


if __name__ == "__main__":
    main()
