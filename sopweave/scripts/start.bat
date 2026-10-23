@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0.."
set "APP_DIR=%CD%"
set "PYTHON="
set "PYTHON_ARGS="
if not defined PORT set "PORT=8080"

for %%P in ("%APP_DIR%\.runtime\Scripts\python.exe" "%APP_DIR%\.venv\Scripts\python.exe" "%APP_DIR%\..\.venv\Scripts\python.exe") do (
    if not defined PYTHON if exist "%%~fP" (
        "%%~fP" -c "import sys, flask, openpyxl, docx, agents, fastapi, uvicorn, yaml; raise SystemExit(sys.version_info[:2] != (3, 14))" >nul 2>&1
        if not errorlevel 1 (
            set "PYTHON=%%~fP"
            set "PYTHON_ARGS="
        )
    )
)

if not defined PYTHON call :find_system_python
if not defined PYTHON goto :python_missing

"%PYTHON%" %PYTHON_ARGS% scripts\check_runtime.py --dependencies-only >nul 2>&1
if errorlevel 1 call :prepare_runtime "%~1"
if errorlevel 1 goto :dependency_failed

if /i "%~1"=="--check" (
    echo Python: %PYTHON% %PYTHON_ARGS%
    "%PYTHON%" %PYTHON_ARGS% scripts\check_runtime.py
    if errorlevel 1 exit /b 1
    exit /b 0
)

echo SOPweave
echo URL: http://localhost:%PORT%
echo.

"%PYTHON%" %PYTHON_ARGS% -m uvicorn asgi:app --host 127.0.0.1 --port %PORT%
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%

:find_system_python
py -3.14 -c "import sys; raise SystemExit(sys.version_info[:2] != (3, 14))" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=py"
    set "PYTHON_ARGS=-3.14"
    exit /b 0
)
py -3 -c "import sys; raise SystemExit(sys.version_info[:2] != (3, 14))" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=py"
    set "PYTHON_ARGS=-3"
    exit /b 0
)
python -c "import sys; raise SystemExit(sys.version_info[:2] != (3, 14))" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=python"
    set "PYTHON_ARGS="
)
exit /b 0

:prepare_runtime
echo Preparing a local Python 3.14 environment...
if not exist "%APP_DIR%\.runtime\Scripts\python.exe" (
    "%PYTHON%" %PYTHON_ARGS% -m venv "%APP_DIR%\.runtime"
    if errorlevel 1 exit /b 1
)
set "PYTHON=%APP_DIR%\.runtime\Scripts\python.exe"
set "PYTHON_ARGS="
"%PYTHON%" -m pip install --no-index --find-links "%APP_DIR%\offline_packages" --require-hashes -r "%APP_DIR%\requirements.lock"
if not errorlevel 1 exit /b 0
if /i "%~1"=="--offline" exit /b 1
echo Trying the configured package index...
"%PYTHON%" -m pip install --require-hashes -r "%APP_DIR%\requirements.lock"
exit /b %ERRORLEVEL%

:python_missing
echo Python 3.14 x64 was not found.
echo Install Python, then run this file again.
pause
exit /b 1

:dependency_failed
echo Failed to prepare the Python environment.
pause
exit /b 1
