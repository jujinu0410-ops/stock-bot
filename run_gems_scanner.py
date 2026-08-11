import os
import sys
import tkinter as tk
from tkinter import messagebox, simpledialog
from pathlib import Path
from datetime import datetime

# 프로젝트 루트 경로 추가
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from scan_stock_for_gems import scan_stock_for_gems
from src.utils.logger import logger

def process_stocks(stock_inputs: list) -> str:
    results = []
    header = f"""================================================================================
🤖 [Gemini Gems 전용] 관심 종목 {len(stock_inputs)}개 정밀 진단 통합 리포트
수집 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S KST')}
================================================================================
"""
    results.append(header)
    for idx, item in enumerate(stock_inputs, 1):
        item_str = item.strip()
        if not item_str:
            continue
        try:
            report = scan_stock_for_gems(item_str)
            results.append(f"\n--- [종목 {idx}/{len(stock_inputs)}: {item_str}] ---\n" + report)
        except Exception as e:
            logger.error(f"종목 {item_str} 진단 중 오류: {e}")
            results.append(f"\n❌ 종목 {item_str} 수집 실패: {e}\n")
            
    footer = """================================================================================
💡 사용 방법:
이 파일 내용 전체를 복사하여 구글 Gemini Gems 챗봇 질문창에 붙여넣으신 후
"위 종목들의 매수 승인 여부와 6대 안전조건 충족 평가를 각각 요약해 줘" 라고 질문하세요!
================================================================================
"""
    results.append(footer)
    return "\n".join(results)

def get_desktop_path() -> Path:
    """Windows 레지스트리 기반 OneDrive/한글 바탕화면 동적 탐색"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
        raw_path, _ = winreg.QueryValueEx(key, "Desktop")
        return Path(os.path.expandvars(raw_path))
    except Exception:
        return Path(os.path.expanduser("~/Desktop"))

def main_gui():
    root = tk.Tk()
    root.withdraw()  # 메인 윈도우 숨김
    
    # 팝업 입력 대화상자
    user_input = simpledialog.askstring(
        "Gemini Gems 관심종목 정밀 진단기",
        "진단할 종목명 또는 종목코드를 쉼표(,)로 구분하여 입력하세요 (최대 5개):\n\n예: 대동, 한신공영, 삼성전자, 현대차, HD현대일렉트릭",
        initialvalue="대동, 한신공영, 삼성전자"
    )
    
    if not user_input or not user_input.strip():
        messagebox.showinfo("안내", "종목 입력이 취소되었습니다.")
        return
        
    stocks = [s.strip() for s in user_input.replace(";", ",").split(",") if s.strip()][:5]
    if not stocks:
        messagebox.showwarning("경고", "올바른 종목을 입력해 주세요.")
        return
        
    full_report = process_stocks(stocks)
    
    # 1. 바탕화면에 경량 텍스트 파일 저장 (Gemini Gems 첨부용)
    desktop_path = get_desktop_path()
    output_file = desktop_path / "Gemini_Gems_입력용_최신진단.txt"
    
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(full_report)
        
    # 2. 클립보드에 자동 복사 (Ctrl+V 용)
    root.clipboard_clear()
    root.clipboard_append(full_report)
    root.update()
    
    messagebox.showinfo(
        "🎉 정밀 진단 완료!",
        f"총 {len(stocks)}개 종목의 Kiwoom REST & OpenDART 수집이 완료되었습니다!\n\n"
        f"1. 바탕화면에 경량 파일 저장 완료:\n   {output_file.name}\n\n"
        f"2. 클립보드에 100% 자동 복사 완료!\n   Gemini Gems 챗봇 창에서 바로 [Ctrl + V] 누르시면 됩니다!"
    )
    root.destroy()

if __name__ == "__main__":
    main_gui()
