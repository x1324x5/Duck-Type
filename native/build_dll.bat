@echo off
REM ==========================================================================
REM Build the DuckType capture DLL (64-bit, C++ / TSF).
REM
REM Tries MinGW-w64 g++ first, then falls back to MSVC (cl.exe). The DLL is
REM linked with a static runtime so it loads cleanly when injected into other
REM processes (no libgcc / vcruntime dependency needed in the target).
REM
REM Output: src\ducktype\native\ducktype_hook.dll
REM ==========================================================================
setlocal
set HERE=%~dp0
set OUTDIR=%HERE%..\src\ducktype\native
if not exist "%OUTDIR%" mkdir "%OUTDIR%"

where x86_64-w64-mingw32-g++ >nul 2>nul
if %ERRORLEVEL%==0 (
    echo [build_dll] Using MinGW-w64 g++ ...
    x86_64-w64-mingw32-g++ -O2 -shared -static -static-libgcc -static-libstdc++ ^
        -o "%OUTDIR%\ducktype_hook.dll" ^
        "%HERE%ducktype_hook.cpp" "%HERE%ducktype_hook.def" -lole32 -luuid -luser32
    goto :done
)

where g++ >nul 2>nul
if %ERRORLEVEL%==0 (
    echo [build_dll] Using g++ ...
    g++ -O2 -shared -static -static-libgcc -static-libstdc++ ^
        -o "%OUTDIR%\ducktype_hook.dll" ^
        "%HERE%ducktype_hook.cpp" "%HERE%ducktype_hook.def" -lole32 -luuid -luser32
    goto :done
)

where cl >nul 2>nul
if %ERRORLEVEL%==0 (
    echo [build_dll] Using MSVC cl.exe ...
    pushd "%OUTDIR%"
    cl /LD /O2 /EHsc /MT "%HERE%ducktype_hook.cpp" /Fe:ducktype_hook.dll ^
        /link /DEF:"%HERE%ducktype_hook.def" ole32.lib uuid.lib user32.lib
    del ducktype_hook.obj ducktype_hook.lib ducktype_hook.exp 2>nul
    popd
    goto :done
)

echo [build_dll] ERROR: no compiler found (need MinGW-w64 g++ or MSVC cl.exe).
exit /b 1

:done
if exist "%OUTDIR%\ducktype_hook.dll" (
    echo [build_dll] OK -^> %OUTDIR%\ducktype_hook.dll
) else (
    echo [build_dll] FAILED.
    exit /b 1
)
endlocal
