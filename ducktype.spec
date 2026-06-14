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

# Bundle the generated icon so the tray / app can find it at runtime.
_ico = os.path.join("src", "ducktype", "assets", "duck.ico")
if os.path.exists(_ico):
    datas += [(_ico, "ducktype/assets")]

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
