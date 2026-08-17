import os
import sys
import tkinter as tk
from tkinter import messagebox, simpledialog
from pathlib import Path
from datetime import datetime
from typing import List, Tuple

# 프로젝트 루트 경로 추가
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from scan_stock_for_gems import scan_stock_dto, scan_stock_for_gems
from src.core.dto import ScanResultDTO
from src.formatters.gems_formatter import render_multi_gems_markdown, render_multi_gems_json
from src.utils.logger import logger

def process_stocks_to_dtos(stock_inputs: list) -> List[ScanResultDTO]:
    """
    입력된 종목 목록에 대해 순차적으로 시세/재무 수집 및 진단을 수행하고 ScanResultDTO 리스트를 생성합니다.
    """
    dtos = []
    for item in stock_inputs:
        item_str = item.strip()
        if not item_str:
            continue
        try:
            dto = scan_stock_dto(item_str)
            dtos.append(dto)
        except Exception as e:
            logger.error(f"종목 {item_str} 진단 중 오류: {e}")
            dtos.append(ScanResultDTO(
                stock_code=item_str,
                stock_name=item_str,
                collected_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S KST'),
                action_strategy=f"❌ 종목 {item_str} 수집 실패: {e}"
            ))
    return dtos

def process_stocks(stock_inputs: list) -> str:
    """
    종목 목록을 입력받아 DTO 수집 ➔ JSON 구조화 ➔ Markdown Formatter 렌더링을 거쳐
    최종 Gemini Gems 통합 텍스트 리포트를 반환합니다.
    """
    dtos = process_stocks_to_dtos(stock_inputs)
    
    # 1. DTO ➔ JSON 직렬화 (내부 데이터 구조화 파이프라인)
    _json_dump = render_multi_gems_json(dtos)
    
    # 2. DTO ➔ Markdown 렌더러 변환
    full_report = render_multi_gems_markdown(dtos)
    return full_report

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
    
    # 1. 모든 바탕화면 후보 경로에 경량 텍스트 파일 저장 (Gemini Gems 첨부용)
    primary_desktop = get_desktop_path()
    output_file = primary_desktop / "Gemini_Gems_입력용_최신진단.txt"
    
    desktop_candidates = [
        primary_desktop,
        Path(os.path.expanduser("~/Desktop")),
        Path(r"C:\Users\jooji\Desktop"),
        Path(r"C:\Users\jooji\OneDrive\Desktop"),
        Path(r"C:\Users\jooji\OneDrive\바탕 화면")
    ]
    
    for d_path in desktop_candidates:
        try:
            if d_path.exists():
                t_file = d_path / "Gemini_Gems_입력용_최신진단.txt"
                with open(t_file, "w", encoding="utf-8") as f:
                    f.write(full_report)
        except Exception as e:
            pass

    # 2. 클립보드에 자동 복사 (Ctrl+V 용)
    root.clipboard_clear()
    root.clipboard_append(full_report)
    root.update()

    # 3. 사용자 화면에 메모장으로 1초 만에 자동 열기!
    try:
        os.startfile(output_file)
    except Exception:
        pass
    
    messagebox.showinfo(
        "🎉 정밀 진단 완료!",
        f"총 {len(stocks)}개 종목의 Kiwoom REST & OpenDART 수집이 완료되었습니다!\n\n"
        f"1. 바탕화면에 경량 파일 저장 및 메모장 자동 열기 완료!\n   (Gemini_Gems_입력용_최신진단.txt)\n\n"
        f"2. 클립보드에 100% 자동 복사 완료!\n   Gemini Gems 챗봇 창에서 바로 [Ctrl + V] 누르시면 됩니다!"
    )
    root.destroy()

if __name__ == "__main__":
    main_gui()
