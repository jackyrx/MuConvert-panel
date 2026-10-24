@echo off
REM Simple launcher for MuConvert Panel (Windows).
REM Keeps the caller's working directory so relative input/output paths behave normally.

setlocal EnableExtensions
set "SCRIPT_DIR=%~dp0"

if defined PYTHONPATH (
  set "PYTHONPATH=%SCRIPT_DIR%;%PYTHONPATH%"
) else (
  set "PYTHONPATH=%SCRIPT_DIR%"
)
set "PYTHONUTF8=1"

set "PYTHON="
where py >nul 2>nul && set "PYTHON=py -3"
if not defined PYTHON (
  where python >nul 2>nul && set "PYTHON=python"
)

if not defined PYTHON (
  echo Python 3.10+ not found. Install Python and try again.
  exit /b 1
)

%PYTHON% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo Python 3.10+ required.
  exit /b 1
)

set "SKIP="
echo %* | findstr /I /C:"--help" >nul && set "SKIP=1"
echo %* | findstr /I /C:"-h" >nul && set "SKIP=1"
echo %* | findstr /I /C:"--version" >nul && set "SKIP=1"
echo %* | findstr /I /C:"--build" >nul && set "SKIP=1"

if not defined SKIP (
  %PYTHON% -c "import muconvert; muconvert.resolve_bin()" >nul 2>nul
  if errorlevel 1 (
    echo.
    echo MuConvert binary not found for this platform.
    echo Options:
    echo   1^) Download a Release wheel and install it
    echo   2^) Build the host binary: %PYTHON% -m muconvert --build
    echo   3^) Set MUCONVERT_BIN to an existing MuConvert binary
    exit /b 1
  )
)

%PYTHON% -m muconvert %*
exit /b %errorlevel%
