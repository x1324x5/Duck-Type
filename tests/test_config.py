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


def test_apply_bool_strings_and_invalid_numbers_keep_old_value(tmp_path, monkeypatch):
    cm = _fresh_config(tmp_path, monkeypatch)
    cfg = cm.Config.load()
    cfg.apply({"paused": "false", "daily_goal": "nope"})
    assert cfg.paused is False
    assert cfg.daily_goal == 500


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


def test_apply_blacklist_accepts_comma_string(tmp_path, monkeypatch):
    cm = _fresh_config(tmp_path, monkeypatch)
    cfg = cm.Config.load()
    cfg.apply({"blacklist_apps": " KeePass.exe, Secret.EXE\nVault.exe "})
    assert cfg.blacklist_apps == ["keepass.exe", "secret.exe", "vault.exe"]


def test_apply_tracked_terms_dedupes_and_keeps_order_and_case(tmp_path, monkeypatch):
    cm = _fresh_config(tmp_path, monkeypatch)
    cfg = cm.Config.load()
    cfg.apply({"tracked_terms": [" 张三 ", "DuckType", "张三", "", "李四"]})
    # order preserved, blanks/dupes dropped, case kept (unlike the blacklist).
    assert cfg.tracked_terms == ["张三", "DuckType", "李四"]


def test_apply_tracked_terms_accepts_comma_string(tmp_path, monkeypatch):
    cm = _fresh_config(tmp_path, monkeypatch)
    cfg = cm.Config.load()
    cfg.apply({"tracked_terms": "张三, 李四\n王五"})
    assert cfg.tracked_terms == ["张三", "李四", "王五"]


def test_apply_tracked_groups_align_to_terms(tmp_path, monkeypatch):
    cm = _fresh_config(tmp_path, monkeypatch)
    cfg = cm.Config.load()
    cfg.apply({"tracked_terms": ["张三", "李四", "王五"],
               "tracked_groups": ["同事", "同事"]})
    # groups are padded to the same length as terms ("" = ungrouped).
    assert cfg.tracked_groups == ["同事", "同事", ""]
    # removing a term re-aligns groups (frontend sends both arrays together).
    cfg.apply({"tracked_terms": ["张三", "王五"], "tracked_groups": ["同事", ""]})
    assert cfg.tracked_groups == ["同事", ""]


def test_apply_tracked_groups_reconcile_when_only_terms_sent(tmp_path, monkeypatch):
    cm = _fresh_config(tmp_path, monkeypatch)
    cfg = cm.Config.load()
    cfg.apply({"tracked_terms": ["甲", "乙"], "tracked_groups": ["A", "B"]})
    cfg.apply({"tracked_terms": ["甲"]})           # groups not resent
    assert cfg.tracked_groups == ["A"]              # truncated to match terms


def test_apply_reports_restart_for_port(tmp_path, monkeypatch):
    cm = _fresh_config(tmp_path, monkeypatch)
    cfg = cm.Config.load()
    assert cfg.apply({"dashboard_port": 9000}) is True
    assert cfg.apply({"daily_goal": 700}) is False


def test_apply_dashboard_preferences(tmp_path, monkeypatch):
    cm = _fresh_config(tmp_path, monkeypatch)
    cfg = cm.Config.load()
    cfg.apply({"theme_mode": "dark", "ticker_refresh_seconds": 5})
    assert cfg.theme_mode == "dark"
    assert cfg.ticker_refresh_seconds == 10
    cfg.apply({"theme_mode": "neon", "ticker_refresh_seconds": 120})
    assert cfg.theme_mode == "system"
    assert cfg.ticker_refresh_seconds == 120


def test_load_roundtrip(tmp_path, monkeypatch):
    cm = _fresh_config(tmp_path, monkeypatch)
    cfg = cm.Config.load()
    cfg.apply({"daily_goal": 333})
    cfg2 = cm.Config.load()
    assert cfg2.daily_goal == 333


def test_load_ignores_non_object_json(tmp_path, monkeypatch):
    cm = _fresh_config(tmp_path, monkeypatch)
    cm.config_path().parent.mkdir(parents=True, exist_ok=True)
    cm.config_path().write_text("[]", encoding="utf-8")
    cfg = cm.Config.load()
    assert cfg.daily_goal == 500


def test_load_normalises_malformed_dashboard_preferences(tmp_path, monkeypatch):
    cm = _fresh_config(tmp_path, monkeypatch)
    cm.config_path().parent.mkdir(parents=True, exist_ok=True)
    cm.config_path().write_text(
        '{"blacklist_apps": "A.exe,B.exe", "theme_mode": "neon", '
        '"ticker_refresh_seconds": "bad"}',
        encoding="utf-8",
    )
    cfg = cm.Config.load()
    assert cfg.blacklist_apps == ["a.exe", "b.exe"]
    assert cfg.theme_mode == "system"
    assert cfg.ticker_refresh_seconds == 60
