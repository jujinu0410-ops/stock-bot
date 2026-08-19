@echo off
chcp 65001 > nul
setlocal

:: 1. 작업 디렉토리 이동
cd /d "C:\Users\jooji\.gemini\antigravity\scratch\stock_analysis_system"
if errorlevel 1 exit /b 2

:: 2. 로그 디렉토리 확인
if not exist "logs" mkdir logs

:: 3. 고정 bootstrap 로그 즉시 기록 (외부 명령 실행 전)
echo ================================================== >> "logs\scheduler_bootstrap.log"
echo [BOOT] %date% %time% Task Scheduler batch entered >> "logs\scheduler_bootstrap.log"
echo [BOOT] User=%USERNAME% WorkingDirectory=%CD% >> "logs\scheduler_bootstrap.log"

:: 4. PowerShell 기반 타임스탬프 생성 (YYYYMMDD_HHMMSS)
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set TIMESTAMP=%%I
if not defined TIMESTAMP set TIMESTAMP=UNKNOWN

set LOG_FILE=logs\scheduled_run_%TIMESTAMP%.log
set PYTHONUNBUFFERED=1

echo ================================================== >> "%LOG_FILE%"
echo [Windows Task Scheduler] 정기 리포트 실행 시작: %date% %time% >> "%LOG_FILE%"
echo [Run Config] Python: C:\Users\jooji\AppData\Local\Programs\Python\Python312\python.exe >> "%LOG_FILE%"
echo [Run Config] Script: C:\Users\jooji\.gemini\antigravity\scratch\stock_analysis_system\main.py >> "%LOG_FILE%"
echo ================================================== >> "%LOG_FILE%"

:: 5. Python main.py 실행 (정기 스케줄에는 --force 미사용)
"C:\Users\jooji\AppData\Local\Programs\Python\Python312\python.exe" "C:\Users\jooji\.gemini\antigravity\scratch\stock_analysis_system\main.py" >> "%LOG_FILE%" 2>&1
set EXIT_CODE=%ERRORLEVEL%

echo ================================================== >> "%LOG_FILE%"
echo [Windows Task Scheduler] 실행 종료 (Exit Code: %EXIT_CODE%): %date% %time% >> "%LOG_FILE%"
echo ================================================== >> "%LOG_FILE%"

echo [BOOT] %date% %time% Execution finished with ExitCode=%EXIT_CODE% >> "logs\scheduler_bootstrap.log"

exit /b %EXIT_CODE%
