@echo off
chcp 65001 > nul
setlocal EnableExtensions

REM YouTube Candidate Watch - LIVE EMAIL launcher.
REM EXTERNAL SIDE EFFECT: Gmail send = YES when eligible signals exist.
REM Safety boundary: no Sheet write, no portfolio mutation, no order API.

for %%I in ("%~dp0..") do set "REPO_ROOT=%%~fI"
cd /d "%REPO_ROOT%"
if errorlevel 1 (
  echo [ERROR] Cannot enter repository root: %REPO_ROOT%
  exit /b 2
)

set "EXPECTED_BRANCH=feat/youtube-candidate-ingest-v0-20260930"
set "CURRENT_BRANCH="
set "CURRENT_HEAD="
set "EXPECTED_HEAD="
for /f "delims=" %%B in ('git branch --show-current 2^>nul') do set "CURRENT_BRANCH=%%B"
for /f "delims=" %%H in ('git rev-parse HEAD 2^>nul') do set "CURRENT_HEAD=%%H"
for /f "delims=" %%H in ('git rev-parse origin/%EXPECTED_BRANCH% 2^>nul') do set "EXPECTED_HEAD=%%H"

set "REF_OK=NO"
if /I "%CURRENT_BRANCH%"=="%EXPECTED_BRANCH%" set "REF_OK=YES"
if not defined CURRENT_BRANCH if defined CURRENT_HEAD if defined EXPECTED_HEAD if /I "%CURRENT_HEAD%"=="%EXPECTED_HEAD%" set "REF_OK=YES"

if /I not "%REF_OK%"=="YES" (
  echo [BLOCKED] LIVE mail is allowed only on %EXPECTED_BRANCH%
  echo [BLOCKED] or detached HEAD exactly matching origin/%EXPECTED_BRANCH%.
  echo [BLOCKED] Current branch: %CURRENT_BRANCH%
  echo [BLOCKED] Current HEAD  : %CURRENT_HEAD%
  echo [BLOCKED] Expected HEAD : %EXPECTED_HEAD%
  exit /b 3
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
if not exist "scripts\youtube_candidate_registered_ip_bootstrap.py" (
  echo [ERROR] Exact-flow bootstrap not found.
  exit /b 4
)
if not exist "scripts\youtube_candidate_live_mail.py" (
  echo [ERROR] LIVE mail module not found.
  exit /b 4
)

if not exist "logs" mkdir logs
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TIMESTAMP=%%I"
if not defined TIMESTAMP set "TIMESTAMP=UNKNOWN"

set "EXACT_JSON=logs\youtube_candidate_live_exact_%TIMESTAMP%.json"
set "MAIL_JSON=logs\youtube_candidate_live_mail_%TIMESTAMP%.json"
set "LOG_OUT=logs\youtube_candidate_live_mail_%TIMESTAMP%.log"
set "PYTHONUNBUFFERED=1"
set "TZ=Asia/Seoul"
set "STOCKBOT_TEST_MODE=0"

 echo ==================================================
 echo   YouTube Candidate LIVE EMAIL
 echo   Gmail send         : YES
 echo   Sheet write        : NO
 echo   Portfolio mutation : NO
 echo   Order API          : NO
 echo ==================================================
 echo.

 echo ================================================== > "%LOG_OUT%"
 echo [YouTube Candidate LIVE EMAIL] >> "%LOG_OUT%"
 echo Started: %date% %time% >> "%LOG_OUT%"
 echo HEAD: %CURRENT_HEAD% >> "%LOG_OUT%"
 echo GmailSend=YES SheetWrite=NO PortfolioMutation=NO OrderAPI=NO >> "%LOG_OUT%"
 echo ================================================== >> "%LOG_OUT%"

"%PYTHON_EXE%" -m scripts.youtube_candidate_registered_ip_bootstrap ^
  --days 35 ^
  --db "%SOURCE_DB%" ^
  --json-out "%EXACT_JSON%" >> "%LOG_OUT%" 2>&1
set "EXACT_EXIT=%ERRORLEVEL%"

set "MAIL_EXIT=SKIPPED"
if "%EXACT_EXIT%"=="0" (
  "%PYTHON_EXE%" -m scripts.youtube_candidate_live_mail ^
    --input-json "%EXACT_JSON%" ^
    --db "%SOURCE_DB%" ^
    --with-market ^
    --send ^
    --json-out "%MAIL_JSON%" >> "%LOG_OUT%" 2>&1
  set "MAIL_EXIT=%ERRORLEVEL%"
)

 echo.
 echo [Exact Flow] ExitCode=%EXACT_EXIT%
 echo [LIVE Mail ] ExitCode=%MAIL_EXIT%
 echo Log       : %LOG_OUT%
 echo Exact JSON: %EXACT_JSON%
 echo Mail JSON : %MAIL_JSON%

if "%MAIL_EXIT%"=="0" if exist "%MAIL_JSON%" (
  echo.
  "%PYTHON_EXE%" -c "import json; d=json.load(open(r'%MAIL_JSON%',encoding='utf-8')); print('LIVE mail sent:',d.get('sent_count',0),' / failures:',d.get('send_failure_count',0),' / recipient:',d.get('recipient')); [print(' -',x.get('signal'),x.get('name'),x.get('ticker'),'sent='+str(x.get('sent')),'reason='+str(x.get('decision_reason'))) for x in d.get('dispatches',[])]"
)

 echo.
 echo LIVE EMAIL SEND = YES
 echo Safety: no Sheet write, no portfolio mutation, no order API.
 echo.

if not "%EXACT_EXIT%"=="0" exit /b %EXACT_EXIT%
if "%MAIL_EXIT%"=="SKIPPED" exit /b 2
exit /b %MAIL_EXIT%
