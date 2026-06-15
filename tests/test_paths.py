import importlib
import sys


def test_frozen_hook_dll_uses_stable_appdata_path(tmp_path, monkeypatch):
    appdata = tmp_path / "appdata"
    meipass = tmp_path / "_MEI12345"
    bundled = meipass / "native" / "ducktype_hook.dll"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"duck hook dll")

    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)

    from ducktype import paths

    importlib.reload(paths)
    p = paths.hook_dll_path()

    assert p.parent == appdata / "DuckType" / "native"
    assert p.name.startswith("ducktype_hook_")
    assert p.suffix == ".dll"
    assert p.read_bytes() == bundled.read_bytes()
