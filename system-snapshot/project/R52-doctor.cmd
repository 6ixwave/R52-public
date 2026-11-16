@echo off
rem R-52 environment check: Python, SQLite, data folder, database integrity.
cd /d "%~dp0"
set "R52_PY=py -3"
where py >nul 2>nul
if errorlevel 1 set "R52_PY=python"
%R52_PY% -m r52 doctor
pause
