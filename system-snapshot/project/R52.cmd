@echo off
rem R-52 window (Edge or Chrome app window). If no core is running, the window starts its own core.
cd /d "%~dp0"
set "R52_PY=py -3"
set "R52_PYW=pyw -3"
where py >nul 2>nul
if errorlevel 1 (
    set "R52_PY=python"
    set "R52_PYW=pythonw"
)
%R52_PY% -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if errorlevel 1 (
    echo R-52 needs Python 3.11 or newer.
    echo Install Python from https://www.python.org
    echo Details: run R52-doctor.cmd
    pause
    exit /b 1
)
start "" %R52_PYW% -m r52.desktop
