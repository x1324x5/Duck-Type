"""Config apply / persistence tests (uses a temp APPDATA)."""
import importlib


def _fresh_config(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    # Reload so config_path() picks up the patched APPDATA.
    from ducktype import paths, config as config_mod
    importlib.reload(paths)
    importlib.reload(config_mod)
    return config_mod


def test_apply_coerces_types(tmp_path, monkeypatch):
    cm = _fresh_config(tmp_path, monkeypatch)
    cfg = cm.Config.load()
    cfg.apply({"daily_goal": "1200", "paused": 1, "run_gap_seconds": "2.5"})
    assert cfg.daily_goal == 1200 and isinstance(cfg.daily_goal, int)
    assert cfg.paused is True
    assert cfg.run_gap_seconds == 2.5


def test_apply_ignores_unknown_and_readonly(tmp_path, monkeypatch):
    cm = _fresh_config(tmp_path, monkeypatch)
    cfg = cm.Config.load()
    cfg.apply({"not_a_field": 5, "dashboard_host": "0.0.0.0"})
    assert not hasattr(cfg, "not_a_field")
    assert cfg.dashboard_host == "127.0.0.1"  # not in EDITABLE -> unchanged


def test_apply_blacklist_normalised(tmp_path, monkeypatch):
    cm = _fresh_config(tmp_path, monkeypatch)
    cfg = cm.Config.load()
    cfg.apply({"blacklist_apps": [" KeePass.exe ", "", "Secret.EXE"]})
    assert cfg.blacklist_apps == ["keepass.exe", "secret.exe"]


def test_apply_reports_restart_for_port(tmp_path, monkeypatch):
    cm = _fresh_config(tmp_path, monkeypatch)
    cfg = cm.Config.load()
    assert cfg.apply({"dashboard_port": 9000}) is True
    assert cfg.apply({"daily_goal": 700}) is False


def test_load_roundtrip(tmp_path, monkeypatch):
    cm = _fresh_config(tmp_path, monkeypatch)
    cfg = cm.Config.load()
    cfg.apply({"daily_goal": 333})
    cfg2 = cm.Config.load()
    assert cfg2.daily_goal == 333
