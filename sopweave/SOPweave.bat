@echo off
if /i "%~1"=="--check" goto legacy
if /i "%~1"=="--offline" goto legacy
if exist "%~dp0SOPweave.exe" (
    start "" "%~dp0SOPweave.exe" %*
    exit /b 0
)
:legacy
call "%~dp0scripts\start.bat" %*
exit /b %ERRORLEVEL%
