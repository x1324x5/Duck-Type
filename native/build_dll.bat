@echo off
REM ==========================================================================
REM Build the DuckType hook DLL (64-bit).
REM
REM Tries MinGW-w64 (gcc) first, then falls back to MSVC (cl.exe).
REM Run this from a normal "cmd" prompt. If you have neither compiler, you can
REM instead rely on the GitHub Actions release build (see .github/workflows).
REM
REM Output: src\ducktype\native\ducktype_hook.dll
REM ==========================================================================
setlocal
set HERE=%~dp0
set OUTDIR=%HERE%..\src\ducktype\native
if not exist "%OUTDIR%" mkdir "%OUTDIR%"

where x86_64-w64-mingw32-gcc >nul 2>nul
if %ERRORLEVEL%==0 (
    echo [build_dll] Using MinGW-w64 ...
    x86_64-w64-mingw32-gcc -O2 -shared -o "%OUTDIR%\ducktype_hook.dll" ^
        "%HERE%ducktype_hook.c" "%HERE%ducktype_hook.def" -luser32
    goto :done
)

where gcc >nul 2>nul
if %ERRORLEVEL%==0 (
    echo [build_dll] Using gcc ...
    gcc -O2 -shared -o "%OUTDIR%\ducktype_hook.dll" ^
        "%HERE%ducktype_hook.c" "%HERE%ducktype_hook.def" -luser32
    goto :done
)

where cl >nul 2>nul
if %ERRORLEVEL%==0 (
    echo [build_dll] Using MSVC cl.exe ...
    pushd "%OUTDIR%"
    cl /LD /O2 "%HERE%ducktype_hook.c" /Fe:ducktype_hook.dll ^
        /link /DEF:"%HERE%ducktype_hook.def" user32.lib
    del ducktype_hook.obj ducktype_hook.lib ducktype_hook.exp 2>nul
    popd
    goto :done
)

echo [build_dll] ERROR: no compiler found (need MinGW-w64 gcc or MSVC cl.exe).
exit /b 1

:done
if exist "%OUTDIR%\ducktype_hook.dll" (
    echo [build_dll] OK -^> %OUTDIR%\ducktype_hook.dll
) else (
    echo [build_dll] FAILED.
    exit /b 1
)
endlocal
