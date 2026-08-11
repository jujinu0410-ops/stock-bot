@echo off
chcp 65001 > nul
echo ==================================================
echo [스윙 투자 자동 분석 시스템] 장 마감(15:35) 리포트 실행 중...
echo ==================================================

cd /d C:\Users\jooji\.gemini\antigravity\scratch\stock_analysis_system
"C:\Users\jooji\AppData\Local\Programs\Python\Python312\python.exe" main.py

echo ==================================================
echo 리포트 발송 완료!
echo ==================================================
