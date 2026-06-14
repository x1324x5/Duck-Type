@echo off
REM Build the hook DLL + icon, then package DuckType.exe with PyInstaller.
setlocal
cd /d "%~dp0"

echo === [1/4] Building native hook DLL ===
call native\build_dll.bat
if errorlevel 1 (
    echo WARNING: DLL build failed -- the exe will still build but cannot record
    echo committed Chinese characters until a DLL is present.
)

echo === [2/4] Installing Python dependencies ===
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

echo === [3/4] Generating duck icon ===
python tools\make_icon.py

echo === [4/4] Packaging exe ===
pyinstaller --noconfirm ducktype.spec

echo.
echo Done. See dist\DuckType.exe
endlocal
