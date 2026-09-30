@echo off
chcp 65001 > nul
setlocal EnableExtensions

REM YouTube Candidate Watch - exact-flow READ-ONLY validation launcher.
REM Safety: no Sheet write, no email send, no portfolio mutation, no order API.
REM Intended to run manually on the Windows machine/public IP registered with Kiwoom REST.

REM Resolve repository root from this batch file so the launcher also works in a Git worktree.
for %%I in ("%~dp0..") do set "REPO_ROOT=%%~fI"
cd /d "%REPO_ROOT%"
if errorlevel 1 (
  echo [ERROR] Cannot enter repository root: %REPO_ROOT%
  exit /b 2
)

REM Never switch branches automatically: protect unrelated local work.
set "EXPECTED_BRANCH=feat/youtube-candidate-ingest-v0-20260930"
set "CURRENT_BRANCH="
for /f "delims=" %%B in ('git branch --show-current 2^>nul') do set "CURRENT_BRANCH=%%B"
for /f "delims=" %%H in ('git rev-parse HEAD 2^>nul') do set "CURRENT_HEAD=%%H"
for /f "delims=" %%H in ('git rev-parse origin/%EXPECTED_BRANCH% 2^>nul') do set "EXPECTED_HEAD=%%H"

set "REF_OK=NO"
if /I "%CURRENT_BRANCH%"=="%EXPECTED_BRANCH%" set "REF_OK=YES"
if not defined CURRENT_BRANCH if defined CURRENT_HEAD if defined EXPECTED_HEAD if /I "%CURRENT_HEAD%"=="%EXPECTED_HEAD%" set "REF_OK=YES"

if /I not "%REF_OK%"=="YES" (
  echo [BLOCKED] Current branch is "%CURRENT_BRANCH%".
  echo [BLOCKED] Current HEAD   : %CURRENT_HEAD%
  echo [BLOCKED] Expected HEAD : %EXPECTED_HEAD%
  echo [BLOCKED] Allowed only on %EXPECTED_BRANCH% or detached HEAD exactly matching origin/%EXPECTED_BRANCH%.
  echo [BLOCKED] No branch was changed automatically.
  exit /b 3
)

if not exist "scripts\youtube_candidate_registered_ip_bootstrap.py" (
  echo [ERROR] credential bootstrap module not found on the validated ref.
  exit /b 4
)
if not exist "scripts\youtube_candidate_registered_ip_validated_dry_run.py" (
  echo [ERROR] validated dry-run module not found on the validated ref.
  exit /b 4
)
if not exist "scripts\youtube_candidate_review_preview.py" (
  echo [ERROR] review preview module not found on the validated ref.
  exit /b 4
)

set "PYTHON_EXE=C:\Users\jooji\AppData\Local\Programs\Python\Python312\python.exe"
set "ENV_FILE=C:\Users\jooji\.env"
set "SOURCE_DB=C:\Users\jooji\.gemini\antigravity\scratch\stock_analysis_system\data\stock_system.db"

if not exist "%PYTHON_EXE%" (
  echo [ERROR] Python not found: %PYTHON_EXE%
  exit /b 5
)
if not exist "%ENV_FILE%" (
  echo [ERROR] Environment file not found: %ENV_FILE%
  exit /b 6
)
if not exist "%SOURCE_DB%" (
  echo [ERROR] Source DB not found: %SOURCE_DB%
  exit /b 7
)

if not exist "logs" mkdir logs
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TIMESTAMP=%%I"
if not defined TIMESTAMP set "TIMESTAMP=UNKNOWN"

set "JSON_OUT=logs\youtube_candidate_registered_ip_dry_run_%TIMESTAMP%.json"
set "LOG_OUT=logs\youtube_candidate_registered_ip_dry_run_%TIMESTAMP%.log"
set "REVIEW_JSON=logs\youtube_candidate_review_preview_%TIMESTAMP%.json"
set "REVIEW_TXT=logs\youtube_candidate_review_preview_%TIMESTAMP%.txt"
set "PYTHONUNBUFFERED=1"
set "TZ=Asia/Seoul"

echo ================================================== > "%LOG_OUT%"
echo [YouTube Candidate READ-ONLY exact-flow dry run] >> "%LOG_OUT%"
echo Started: %date% %time% >> "%LOG_OUT%"
echo Branch: %CURRENT_BRANCH% >> "%LOG_OUT%"
echo HEAD: %CURRENT_HEAD% >> "%LOG_OUT%"
echo WorkingDirectory: %CD% >> "%LOG_OUT%"
echo SourceDB: %SOURCE_DB% >> "%LOG_OUT%"
echo JSON: %JSON_OUT% >> "%LOG_OUT%"
echo ReviewJSON: %REVIEW_JSON% >> "%LOG_OUT%"
echo ReviewTXT: %REVIEW_TXT% >> "%LOG_OUT%"
echo KiwoomCredentialsSource=config.settings local .env bootstrap >> "%LOG_OUT%"
echo Safety: SheetWrite=NO EmailSend=NO PortfolioMutation=NO OrderAPI=NO >> "%LOG_OUT%"
echo ================================================== >> "%LOG_OUT%"

"%PYTHON_EXE%" -m scripts.youtube_candidate_registered_ip_bootstrap ^
  --days 35 ^
  --db "%SOURCE_DB%" ^
  --json-out "%JSON_OUT%" >> "%LOG_OUT%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

set "PREVIEW_EXIT=SKIPPED"
if "%EXIT_CODE%"=="0" (
  "%PYTHON_EXE%" -m scripts.youtube_candidate_review_preview ^
    --input-json "%JSON_OUT%" ^
    --db "%SOURCE_DB%" ^
    --with-market ^
    --json-out "%REVIEW_JSON%" ^
    --text-out "%REVIEW_TXT%" >> "%LOG_OUT%" 2>&1
  set "PREVIEW_EXIT=%ERRORLEVEL%"
)

echo ================================================== >> "%LOG_OUT%"
echo Finished: %date% %time% ExitCode=%EXIT_CODE% PreviewExit=%PREVIEW_EXIT% >> "%LOG_OUT%"
echo ================================================== >> "%LOG_OUT%"

echo.
echo [YouTube Candidate Dry Run] ExitCode=%EXIT_CODE%
echo [Review Preview] ExitCode=%PREVIEW_EXIT%
echo Log        : %LOG_OUT%
echo Exact JSON : %JSON_OUT%
echo Review JSON: %REVIEW_JSON%
echo Review TXT : %REVIEW_TXT%
echo.

if "%PREVIEW_EXIT%"=="0" if exist "%REVIEW_TXT%" (
  echo ================= REVIEW PREVIEW =================
  "%PYTHON_EXE%" -c "from pathlib import Path; print(Path(r'%REVIEW_TXT%').read_text(encoding='utf-8'))"
  echo ==================================================
  echo.
)

echo Safety: READ-ONLY validation only. No Sheet write, no email send, no order.
echo.

exit /b %EXIT_CODE%
