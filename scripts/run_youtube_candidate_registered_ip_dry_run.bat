@echo off
chcp 65001 > nul
setlocal

REM YouTube Candidate Watch - exact-flow READ-ONLY validation launcher.
REM Safety: no Sheet write, no email send, no portfolio mutation, no order API.
REM Intended to run manually on the Windows machine/public IP registered with Kiwoom REST.

cd /d "C:\Users\jooji\.gemini\antigravity\scratch\stock_analysis_system"
if errorlevel 1 (
  echo [ERROR] stock_analysis_system directory not found.
  exit /b 2
)

if not exist "logs" mkdir logs
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set TIMESTAMP=%%I
if not defined TIMESTAMP set TIMESTAMP=UNKNOWN

set JSON_OUT=logs\youtube_candidate_registered_ip_dry_run_%TIMESTAMP%.json
set LOG_OUT=logs\youtube_candidate_registered_ip_dry_run_%TIMESTAMP%.log
set PYTHON_EXE=C:\Users\jooji\AppData\Local\Programs\Python\Python312\python.exe
set PYTHONUNBUFFERED=1
set TZ=Asia/Seoul

echo ================================================== > "%LOG_OUT%"
echo [YouTube Candidate READ-ONLY exact-flow dry run] >> "%LOG_OUT%"
echo Started: %date% %time% >> "%LOG_OUT%"
echo WorkingDirectory: %CD% >> "%LOG_OUT%"
echo JSON: %JSON_OUT% >> "%LOG_OUT%"
echo Safety: SheetWrite=NO EmailSend=NO PortfolioMutation=NO OrderAPI=NO >> "%LOG_OUT%"
echo ================================================== >> "%LOG_OUT%"

"%PYTHON_EXE%" -m scripts.youtube_candidate_registered_ip_validated_dry_run ^
  --days 35 ^
  --json-out "%JSON_OUT%" >> "%LOG_OUT%" 2>&1
set EXIT_CODE=%ERRORLEVEL%

echo ================================================== >> "%LOG_OUT%"
echo Finished: %date% %time% ExitCode=%EXIT_CODE% >> "%LOG_OUT%"
echo ================================================== >> "%LOG_OUT%"

echo.
echo [YouTube Candidate Dry Run] ExitCode=%EXIT_CODE%
echo Log : %LOG_OUT%
echo JSON: %JSON_OUT%
echo.

exit /b %EXIT_CODE%
