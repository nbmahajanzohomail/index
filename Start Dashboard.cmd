@echo off
setlocal
cd /d "%~dp0"
echo Starting the separate NSE/BSE Index Options Research dashboard...
where py >nul 2>&1
if not errorlevel 1 (
  py -3 app.py
) else (
  where python >nul 2>&1
  if not errorlevel 1 (
    python app.py
  ) else (
    echo Python 3.10 or newer was not found.
    set "INDEX_EXIT=1"
    goto finish
  )
)
set "INDEX_EXIT=%ERRORLEVEL%"
:finish
if not "%INDEX_EXIT%"=="0" (
  echo.
  echo The dashboard stopped with error code %INDEX_EXIT%.
  echo If a startup error appeared above, use that message to diagnose the problem.
  pause
)
exit /b %INDEX_EXIT%
