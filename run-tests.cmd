@echo off
rem Runs all R-52 tests.
cd /d "%~dp0"
set "R52_PY=py -3"
where py >nul 2>nul
if errorlevel 1 set "R52_PY=python"
%R52_PY% -m unittest discover -s tests -v
pause
