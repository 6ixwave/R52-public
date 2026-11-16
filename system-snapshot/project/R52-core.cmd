@echo off
rem R-52 core with automatic restart after failures. Keep this window open, it can be minimized.
rem To stop: press Ctrl+C here, or use the stop button on the Service tab of the R-52 window.
cd /d "%~dp0"
title R-52 core
set "R52_PY=py -3"
where py >nul 2>nul
if errorlevel 1 set "R52_PY=python"
%R52_PY% -m r52 supervise --console
pause
