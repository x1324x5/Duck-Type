"""User configuration, persisted as JSON under %APPDATA%\\DuckType\\config.json."""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from typing import List

from .paths import config_path

# Process names (lower-case, with .exe) that are excluded by default for privacy.
_DEFAULT_BLACKLIST = [
    "keepass.exe",
    "keepassxc.exe",
    "1password.exe",
    "bitwarden.exe",
    "lastpass.exe",
    "dashlane.exe",
]


@dataclass
class Config:
    # capture
    paused: bool = False
    exclude_password_fields: bool = True
    blacklist_apps: List[str] = field(default_factory=lambda: list(_DEFAULT_BLACKLIST))

    # run-grouping: characters typed within this many seconds (and in the same
    # app) are treated as one continuous "run" and segmented together.
    run_gap_seconds: float = 3.0
    # efficiency: a typing "session" ends after this many idle seconds.
    session_gap_seconds: float = 60.0

    # privacy: automatically delete character/key events older than this many
    # days. 0 (the default) keeps everything forever.
    retention_days: int = 0

    # storage: LEGACY. The data root is now driven by paths.root_dir() /
    # location.json (see firstrun.py), not this field. Kept so an old 0.1.7
    # config that set it can still be detected and migrated on first 0.1.8 run
    # (firstrun._legacy_root). Never written by the new relocate flow.
    data_dir: str = ""

    # gamification: per-day character goal used by the goal ring / streak.
    daily_goal: int = 500

    # tracked terms: user-picked characters/words/names that jieba would not
    # segment on its own (e.g. personal names, project codenames). The dashboard
    # counts every committed occurrence of each term directly from the character
    # stream, independent of segmentation. Stored in entry order.
    tracked_terms: List[str] = field(default_factory=list)
    # optional group label per tracked term (parallel to tracked_terms, "" =
    # ungrouped). Lets the dashboard cluster names/projects under headers. Always
    # reconciled to the same length as tracked_terms.
    tracked_groups: List[str] = field(default_factory=list)

    # dashboard
    # dashboard_host/port are LEGACY: the app now renders in a native window
    # (desktop.py) with no HTTP server. Kept only for the dev/preview server.
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8765
    # Now means "show the native window on launch" (vs start hidden in the tray).
    open_dashboard_on_start: bool = True
    theme_mode: str = "system"  # system / light / dark
    ticker_refresh_seconds: int = 60

    # startup
    autostart: bool = False

    _lock = threading.Lock()

    def __post_init__(self) -> None:
        self.blacklist_apps = _normalise_blacklist(self.blacklist_apps)
        self.tracked_terms = _normalise_terms(self.tracked_terms)
        self.tracked_groups = _reconcile_groups(self.tracked_groups, self.tracked_terms)
        if self.theme_mode not in ("system", "light", "dark"):
            self.theme_mode = "system"
        self.ticker_refresh_seconds = _bounded_int(
            self.ticker_refresh_seconds, default=60, lower=10, upper=3600
        )

    # ---- persistence -----------------------------------------------------
    @classmethod
    def load(cls) -> "Config":
        p = config_path()
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                raw = {}
            if not isinstance(raw, dict):
                raw = {}
            known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
            cfg = cls(**{k: v for k, v in raw.items() if k in known})
        else:
            cfg = cls()
            cfg.save()
        return cfg

    def save(self) -> None:
        data = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        p = config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)

    # Fields the dashboard settings page is allowed to write.
    EDITABLE = (
        "paused", "exclude_password_fields", "blacklist_apps",
        "run_gap_seconds", "session_gap_seconds", "retention_days",
        "daily_goal", "dashboard_port", "open_dashboard_on_start", "autostart",
        "theme_mode", "ticker_refresh_seconds", "tracked_terms", "tracked_groups",
    )
    # Changing these takes effect only after a restart.
    RESTART_REQUIRED = ("dashboard_port",)

    # ---- helpers ---------------------------------------------------------
    def is_blacklisted(self, app: str | None) -> bool:
        if not app:
            return False
        return app.lower() in {a.lower() for a in self.blacklist_apps}

    def apply(self, updates: dict) -> bool:
        """Apply a partial settings update (only EDITABLE keys). Coerces values
        to the dataclass field type. Returns True if a restart is needed."""
        restart = False
        for key, value in updates.items():
            if key not in self.EDITABLE:
                continue
            old = getattr(self, key)
            if isinstance(old, bool):
                value = _coerce_bool(value)
            elif isinstance(old, int):
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    value = old
            elif isinstance(old, float):
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    value = old
            elif isinstance(old, list):
                if key == "tracked_terms":
                    value = _normalise_terms(value)
                elif key == "tracked_groups":
                    value = _normalise_group_list(value)
                else:
                    value = _normalise_blacklist(value)
            elif isinstance(old, str):
                value = str(value)
            if key == "theme_mode" and value not in ("system", "light", "dark"):
                value = "system"
            if key == "ticker_refresh_seconds":
                value = max(10, min(3600, int(value)))
            if value != old and key in self.RESTART_REQUIRED:
                restart = True
            setattr(self, key, value)
        # Groups are positional metadata for tracked_terms; keep them aligned
        # even if only one of the two arrays was sent in this update.
        self.tracked_groups = _reconcile_groups(self.tracked_groups, self.tracked_terms)
        self.save()
        return restart


def _coerce_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _normalise_blacklist(value) -> List[str]:
    if isinstance(value, str):
        raw = value.replace("\n", ",").split(",")
    else:
        try:
            raw = list(value)
        except TypeError:
            raw = []
    return [str(x).strip().lower() for x in raw if str(x).strip()]


def _normalise_terms(value) -> List[str]:
    """Tracked terms: split on newlines/commas, trim, drop blanks, de-dupe while
    preserving entry order. Case is kept as typed (Chinese has none anyway).
    Capped at 100 terms of <=32 chars each to keep the single-pass scan cheap."""
    if isinstance(value, str):
        raw = value.replace("\n", ",").split(",")
    else:
        try:
            raw = list(value)
        except TypeError:
            raw = []
    out: List[str] = []
    seen = set()
    for x in raw:
        term = str(x).strip()[:32]
        if term and term not in seen:
            seen.add(term)
            out.append(term)
        if len(out) >= 100:
            break
    return out


def _normalise_group_list(value) -> List[str]:
    """Group labels for tracked terms: a positional list, one per term. Blanks
    are kept (they mean "ungrouped") so positions stay aligned. Each label is
    trimmed and capped at 16 chars; the list is capped at 100 to match terms."""
    if isinstance(value, str):
        raw = value.split("\n")
    else:
        try:
            raw = list(value)
        except TypeError:
            raw = []
    return [str(x).strip()[:16] for x in raw][:100]


def _reconcile_groups(groups, terms) -> List[str]:
    """Pad/truncate the group list so it lines up 1:1 with ``terms``."""
    groups = _normalise_group_list(groups)
    n = len(terms)
    if len(groups) < n:
        groups = groups + [""] * (n - len(groups))
    return groups[:n]


def _bounded_int(value, default: int, lower: int, upper: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(lower, min(upper, n))
