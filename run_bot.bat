@echo off
setlocal
REM ValoHub one-click launcher. Double-click from any working directory.
REM Optional env flags (set before launch):
REM   HOST=127.0.0.1 PORT=18791      Bind address and port (HOST=0.0.0.0 for LAN).
REM   VALO_LOBBY=1                   Trusted-LAN lobby (no account auth/TLS).
REM   VALO_WATCHER=1                 Auto-watch .vrf files (off by default).
REM   VALO_DEMOS_DIR=<path>          Folder watched for .vrf files.
REM   VALOHUB_DEMOS_DIR=<path>     Local import picker folder; set both to align.
REM   VALO_AUTO_REQUEUE=1            Re-queue eligible failed decodes at startup.
REM   VALO_OFFLINE=1                 Skip identity lookups; HENRIKDEV_TOKEN enables them.
REM   VALO_LLM_URL=<local /v1 URL>   First-priority coach/review llama.cpp server.
REM   VALO_LLM_KEY_FILE=<path>       Managed server descriptor for coach.
REM   VALO_LLM_MODEL=<model id>      Default Qwen3.8-9B-Q6_K.
REM   VALO_LMSTUDIO_URL=<local URL>  Default http://127.0.0.1:1234/v1.
REM   VALO_OLLAMA_URL=<local URL>    Default http://127.0.0.1:11434.
REM   VALO_LLM_TIMEOUT=<seconds>    Max 300 seconds for model requests.
REM More flags/defaults: bot\web\static\replays_app\README.md.
cd /d "%~dp0"
if errorlevel 1 (echo ERROR: Could not enter the project directory.& exit /b 1)
py -3.12 --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3.12 was not found by the Windows py launcher.
    echo Install Python 3.12, including the py launcher, then retry.
    exit /b 1
)
py -3.12 -m pip show aiohttp >nul 2>&1
if errorlevel 1 (
    echo ERROR: aiohttp is missing from Python 3.12.
    echo Install dependencies: py -3.12 -m pip install -r bot\requirements.txt
    exit /b 1
)
if not defined HOST set "HOST=127.0.0.1"
if not defined PORT set "PORT=18791"
py -3.12 run_bot_checks.py
REM Warnings never prevent the server from starting.
if "%HOST%"=="0.0.0.0" (
    echo Replay library: http://127.0.0.1:%PORT%/replays ^(also on your LAN IP^)
) else (
    echo Replay library: http://%HOST%:%PORT%/replays
)
py -3.12 -m bot.web
set "result=%errorlevel%"
if not "%result%"=="0" echo ERROR: ValoHub exited with code %result%.
exit /b %result%