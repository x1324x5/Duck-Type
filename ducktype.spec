# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for a single-file, windowed DuckType.exe.

Build:  pyinstaller ducktype.spec
(Run native\\build_dll.bat and tools\\make_icon.py first so the hook DLL and the
icon get bundled.)
"""
import os
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# jieba ships dictionaries that must travel with the exe.
for pkg in ("jieba",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

datas += [("src/ducktype/dashboard/static", "ducktype/dashboard/static")]
# Ship repo-root artwork as extra_assets, but skip source packs and preview
# files so they don't bloat the exe.
_ASSET_SKIP = {
    "mail.png", "sleep.png", "hat.png", "hat_slip.png", "smile.png",
    "duck_sheet_5x5.svg", "duck_sheet_5x5_clean.png",
    "preview_5x5_positioned.png", "preview_5x5_positioned.svg",
    "crop_metadata.json",
}
if os.path.isdir("assets"):
    for _root, _dirs, _files in os.walk("assets"):
        _dirs[:] = [d for d in _dirs if d != "png_reference"]
        for _f in _files:
            if _f.endswith(".zip") or _f in _ASSET_SKIP:
                continue
            _src = os.path.join(_root, _f)
            _rel = os.path.relpath(_root, "assets")
            _dest = "extra_assets" if _rel == "." else os.path.join("extra_assets", _rel)
            datas += [(_src, _dest)]

# Bundle generated icons so the tray / app can find them at runtime.
_ico = os.path.join("src", "ducktype", "assets", "duck.ico")
if os.path.exists(_ico):
    datas += [(_ico, "assets")]
_png = os.path.join("src", "ducktype", "assets", "duck.png")
if os.path.exists(_png):
    datas += [(_png, "assets")]

# Bundle the native hook DLL if it has been built.
_dll = os.path.join("src", "ducktype", "native", "ducktype_hook.dll")
if os.path.exists(_dll):
    binaries += [(_dll, "native")]

a = Analysis(
    ["run.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["PIL._tkinter_finder"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DuckType",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # windowed: no console window
    disable_windowed_traceback=False,
    target_arch=None,
    icon=_ico if os.path.exists(_ico) else None,
)
