@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

:: 1. 작업 디렉토리 이동
cd /d "C:\Users\jooji\.gemini\antigravity\scratch\stock_analysis_system"

:: 2. 로그 디렉토리 확인
if not exist "logs" mkdir logs

:: 3. 날짜 및 시간 기반 로그 파일명 생성 (YYYYMMDD_HHMMSS)
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value 2^>nul') do set datetime=%%I
if defined datetime (
    set TIMESTAMP=%datetime:~0,8%_%datetime:~8,6%
) else (
    set TIMESTAMP=%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%
)
set TIMESTAMP=%TIMESTAMP: =0%
set TIMESTAMP=%TIMESTAMP::=%
set TIMESTAMP=%TIMESTAMP:/=%
set TIMESTAMP=%TIMESTAMP:-=%

set LOG_FILE=logs\scheduled_run_%TIMESTAMP%.log

echo ================================================== >> "%LOG_FILE%"
echo [Windows Task Scheduler] 정기 리포트 실행 시작: %date% %time% >> "%LOG_FILE%"
echo [Run Config] Python: C:\Users\jooji\AppData\Local\Programs\Python\Python312\python.exe >> "%LOG_FILE%"
echo [Run Config] Script: C:\Users\jooji\.gemini\antigravity\scratch\stock_analysis_system\main.py >> "%LOG_FILE%"
echo ================================================== >> "%LOG_FILE%"

:: 4. Python main.py 실행 (정기 스케줄에는 --force 미사용)
"C:\Users\jooji\AppData\Local\Programs\Python\Python312\python.exe" "C:\Users\jooji\.gemini\antigravity\scratch\stock_analysis_system\main.py" >> "%LOG_FILE%" 2>&1
set EXIT_CODE=%ERRORLEVEL%

echo ================================================== >> "%LOG_FILE%"
echo [Windows Task Scheduler] 실행 종료 (Exit Code: %EXIT_CODE%): %date% %time% >> "%LOG_FILE%"
echo ================================================== >> "%LOG_FILE%"

exit /b %EXIT_CODE%
