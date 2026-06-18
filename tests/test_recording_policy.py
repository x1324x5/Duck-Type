"""Privacy/recording policy tests that do not need Windows hooks."""

from ducktype.config import Config
from ducktype.core import CaptureContext, RecordingPolicy


def _policy(**updates):
    cfg = Config()
    for key, value in updates.items():
        setattr(cfg, key, value)
    return RecordingPolicy(cfg)


def test_records_when_no_privacy_gate_matches():
    assert _policy().should_record(CaptureContext(app="Code.exe")) is True


def test_paused_blocks_all_events():
    assert _policy(paused=True).should_record(CaptureContext(app="Code.exe")) is False


def test_password_field_respects_config_gate():
    ctx = CaptureContext(app="notepad.exe", password_field=True)
    assert _policy(exclude_password_fields=True).should_record(ctx) is False
    assert _policy(exclude_password_fields=False).should_record(ctx) is True


def test_blacklisted_app_blocks_events_case_insensitively():
    ctx = CaptureContext(app="KEEPASS.EXE")
    assert _policy().should_record(ctx) is False


def test_unknown_app_is_allowed_unless_other_gates_match():
    assert _policy().should_record(CaptureContext(app=None)) is True
