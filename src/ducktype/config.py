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

    # storage: directory for the database. Empty = the default %APPDATA%\DuckType.
    # Changed only through the data-management "relocate" flow (which also moves
    # the existing database), so it is intentionally NOT in EDITABLE below.
    data_dir: str = ""

    # gamification: per-day character goal used by the goal ring / streak.
    daily_goal: int = 500

    # dashboard
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8765
    open_dashboard_on_start: bool = False
    theme_mode: str = "system"  # system / light / dark
    ticker_refresh_seconds: int = 60

    # startup
    autostart: bool = False

    _lock = threading.Lock()

    # ---- persistence -----------------------------------------------------
    @classmethod
    def load(cls) -> "Config":
        p = config_path()
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                raw = {}
            known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
            cfg = cls(**{k: v for k, v in raw.items() if k in known})
        else:
            cfg = cls()
            cfg.save()
        return cfg

    def save(self) -> None:
        data = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        config_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # Fields the dashboard settings page is allowed to write.
    EDITABLE = (
        "paused", "exclude_password_fields", "blacklist_apps",
        "run_gap_seconds", "session_gap_seconds", "retention_days",
        "daily_goal", "dashboard_port", "open_dashboard_on_start", "autostart",
        "theme_mode", "ticker_refresh_seconds",
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
                value = bool(value)
            elif isinstance(old, int):
                value = int(value)
            elif isinstance(old, float):
                value = float(value)
            elif isinstance(old, list):
                value = [str(x).strip().lower() for x in value if str(x).strip()]
            elif isinstance(old, str):
                value = str(value)
            if key == "theme_mode" and value not in ("system", "light", "dark"):
                value = "system"
            if key == "ticker_refresh_seconds":
                value = max(10, min(3600, int(value)))
            if value != old and key in self.RESTART_REQUIRED:
                restart = True
            setattr(self, key, value)
        self.save()
        return restart
