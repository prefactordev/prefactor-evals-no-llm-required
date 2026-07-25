@echo off
setlocal EnableDelayedExpansion

REM Build and open a quality dashboard for one agent.
REM
REM   dashboard.bat <agent-id> [pack] [limit]
REM
REM Credentials come from PREFACTOR_API_URL and PREFACTOR_API_TOKEN. If they are
REM not already set, a .env file next to this script is read. Nothing from it is
REM ever printed: it holds an API token.

set "AGENT=%~1"
set "PACK=%~2"
set "LIMIT=%~3"

if "%AGENT%"=="" (
  echo Usage: dashboard.bat ^<agent-id^> [pack] [limit]
  echo.
  echo   agent-id  The Prefactor agent to evaluate.
  echo   pack      Pack name or path. Default: standard (the zero config health checks)
  echo   limit     How many recent runs. Default: 10
  echo.
  echo Example:
  echo   dashboard.bat 01kwp2by1c7synth5spw67ryvzwnb1yt
  exit /b 1
)

if "%PACK%"=="" set "PACK=standard"
if "%LIMIT%"=="" set "LIMIT=10"

set "ROOT=%~dp0"
set "PKG=%ROOT%packages\python"
set "OUT=%ROOT%quality\%AGENT%"

REM A bare pack name resolves to the shipped pack of that name.
if exist "%PACK%" (
  set "PACKPATH=%PACK%"
) else (
  set "PACKPATH=%ROOT%spec\packs\%PACK%.yaml"
)

if not exist "!PACKPATH!" (
  echo Pack not found: !PACKPATH!
  echo Available packs:
  for %%F in ("%ROOT%spec\packs\*.yaml") do echo   %%~nF
  exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found on PATH. Install Python 3.10 or newer, then retry.
  exit /b 1
)

set "ENVARG="
if exist "%ROOT%.env" set "ENVARG=--env-file "%ROOT%.env""

echo Evaluating agent %AGENT%
echo   pack:  !PACKPATH!
echo   runs:  %LIMIT% most recent
echo   into:  %OUT%
echo.

set "PYTHONPATH=%PKG%\src"
python -m prefactor_evals_no_llm.cli run ^
  --pack "!PACKPATH!" ^
  --agent "%AGENT%" ^
  --limit %LIMIT% ^
  --purpose any ^
  --html "%OUT%" ^
  --no-report ^
  --no-fail-exit ^
  --open ^
  !ENVARG!

set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo The run did not finish. Exit code %CODE%.
  echo If this is a credentials problem, set PREFACTOR_API_URL and
  echo PREFACTOR_API_TOKEN, or put them in a .env next to this script.
  echo The read endpoints need an admin API token, not the SDK ingestion token.
  exit /b %CODE%
)

endlocal
