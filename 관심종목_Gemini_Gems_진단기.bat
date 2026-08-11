@echo off
chcp 65001 > nul
title 관심종목 Gemini Gems 정밀 진단기
cd /d "C:\Users\jooji\.gemini\antigravity\scratch\stock_analysis_system"
python run_gems_scanner.py
