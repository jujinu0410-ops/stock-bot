@echo off
cd /d "C:\Users\jooji\.gemini\antigravity\scratch\stock_analysis_system"
"C:\Users\jooji\AppData\Local\Programs\Python\Python312\python.exe" "run_gems_scanner.py"
if errorlevel 1 (
    echo.
    echo ❌ 실행 중 오류가 발생했습니다. 키를 누르면 창이 닫힙니다.
    pause
)
