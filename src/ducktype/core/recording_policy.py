"""Privacy policy for deciding whether capture events may be persisted.

This module is intentionally platform-independent. Windows-specific code can
detect the active application and focused-control state, but the decision about
whether to write a character/key event belongs here so it can be tested without
installing hooks or touching Win32 APIs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


class RecordingConfig(Protocol):
    paused: bool
    exclude_password_fields: bool

    def is_blacklisted(self, app: Optional[str]) -> bool:
        ...


@dataclass(frozen=True)
class CaptureContext:
    """Runtime context known at the moment an input event arrives."""

    app: Optional[str] = None
    password_field: bool = False


class RecordingPolicy:
    """Applies DuckType's privacy gates before storage writes."""

    def __init__(self, config: RecordingConfig):
        self._config = config

    def should_record(self, context: CaptureContext | None = None) -> bool:
        context = context or CaptureContext()
        if self._config.paused:
            return False
        if self._config.exclude_password_fields and context.password_field:
            return False
        if self._config.is_blacklisted(context.app):
            return False
        return True
