"""Aggregate statistics consumed by the dashboard and CLI.

Every query is bounded by an optional half-open time window ``[since, until)``
(both in epoch seconds; ``None`` means unbounded on that side). The dashboard
maps its range buttons -- and a custom date picker -- onto these two numbers, so
"today", "last 7 days", "this month" and an arbitrary date range are all just
different bounds over the same functions.

This module used to hold every statistic in one ~2,000-line file. In 0.3.0 it
was split into cohesive sibling modules (matching the earlier ``fun`` /
``reporting`` extractions); this file is now a thin facade that re-exports the
whole public surface so every ``stats.foo`` caller and test keeps working
unchanged. The groups:

    statutil        shared SQL/time/label helpers
    char_stats      counts, top chars, daily/hourly series, heatmap, calendars
    edit_stats      deletion accounting + typing-speed (efficiency)
    word_stats      word / POS / topic analytics (jieba)
    sequence_stats  typed-sequence reconstruction, search, tracked terms
    gamify          streaks, goals, achievements
    report_stats    periodic reports, trend comparison, narratives, insights
    ticker          board ticker + live mini-counter numbers
    overview        the board overview header
    fun             生僻字 classifier + playful leaderboards (0.2.8)
"""
from __future__ import annotations

# Re-exported submodule namespaces / passthroughs that callers reach through
# ``stats`` (e.g. tests use ``stats.segment``; ``stats.resolve_range`` etc.).
from . import segment  # noqa: F401
from .time_ranges import resolve_range, since_for  # noqa: F401
from .common_chars import COMMON_CHARS  # noqa: F401

# 生僻字 classifier + playful leaderboards (extracted 0.2.8).
from .fun import (  # noqa: F401
    _COMMON_SUPPLEMENT,
    _is_uncommon,
    fun_rankings,
    set_user_common,
)

# Shared helpers.
from .statutil import (  # noqa: F401
    EASTER_EGG_QUOTE,
    _day_bounds,
    _hour_window_label,
    _weekday_cn,
    _where,
    pretty_app,
)

# Character-level board stats.
from .char_stats import (  # noqa: F401
    app_detail,
    contrib_calendar,
    daily,
    heatmap,
    per_app,
    richness_trend,
    timeseries,
    top_chars,
    total_chars,
)

# Edit / deletion + efficiency.
from .edit_stats import (  # noqa: F401
    _effective_deletions,
    edits,
    efficiency,
)

# Word / POS / topics.
from .word_stats import (  # noqa: F401
    COARSE_LABELS,
    COARSE_ORDER,
    POS_LABELS,
    _tail_word_pos,
    coarse_pos,
    pos_distribution,
    pos_distribution_daily,
    pos_word_distribution,
    top_words,
    top_words_daily,
    topics,
    topics_daily,
)

# Committed-character sequence / search / tracked terms.
from .sequence_stats import (  # noqa: F401
    _app_filter_set,
    search,
    sequence_apps,
    sequence_recent,
    sequence_runs,
    tracked_terms,
)

# Streaks / goals / achievements.
from .gamify import (  # noqa: F401
    _ACHIEVEMENTS,
    _daily_map,
    _streak,
    _week_month_chars,
    gamify,
)

# Periodic reports + trend + narratives.
from .report_stats import (  # noqa: F401
    _activity_rhythm,
    _behavior_insights,
    _build_narrative,
    _longest_session,
    _peak_hour,
    _peak_window,
    _top_multichar_word,
    period_bounds,
    report,
    report_bounds,
    report_fast,
    report_heavy,
    report_words,
    trend,
)

# Board ticker + live mini-counter.
from .ticker import (  # noqa: F401
    EASTER_EGG_QUOTE as _EASTER_EGG_QUOTE,  # (kept importable; same object)
    load_phrases,
    mini_stats,
    ticker,
)

# Board overview header.
from .overview import overview  # noqa: F401

# Higher-level board insights (0.3.0).
from .insights import (  # noqa: F401
    app_efficiency,
    vocab_growth,
    weekday_rhythm,
)
