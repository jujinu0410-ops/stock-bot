import sys
import os
import argparse
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np
import re
from typing import Optional, List, Dict, Any, Tuple

# 프로젝트 루트 디렉토리 설정
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from config.settings import EMAIL_RENDER_VERSION
from src.database.db_manager import DatabaseManager
from src.api.kiwoom_api import KiwoomAPIClient
from src.api.dart_api import DartAPIClient
from src.api.real_market_api import RealMarketAPIClient
from src.engine.trading_engine import TradingEngine
from src.engine.portfolio_manager import PortfolioManager
from src.engine.watchlist_manager import WatchlistManager
from src.notifications.gmail_notifier import GmailNotifier
from src.utils.excel_exporter import create_analysis_excel_report
from src.policy.policy_shadow_store import PolicyShadowReader
from src.utils.logger import logger

MASTER_STOCK_MAP = {
    "000490": "대동", "004960": "한신공영", "047770": "코데즈컴바인", "055490": "테이팩스",
    "140670": "알에스오토메이션", "161510": "PLUS 고배당주", "206650": "유바이오로직스",
    "234920": "자이글", "219550": "디와이디", "241520": "DSC인베스트먼트", "267260": "HD현대일렉트릭",
    "348340": "뉴로메카", "490590": "RISE 미국AI밸류체인데일리고정커버드콜",
    "010120": "LS일렉트릭", "010140": "삼성중공업", "207940": "삼성바이오로직스",
    "034020": "두산에너빌리티", "015540": "하이록코리아", "005930": "삼성전자",
    "000660": "SK하이닉스", "035420": "NAVER", "035720": "카카오",
    "011700": "한신기계", "047050": "포스코인터내셔널", "214450": "파마리서치"
}

def sync_master_stock_info(db: DatabaseManager):
    """
    모든 테이블(stock_info 등)의
    종목코드-종목명 단일 마스터 동기화 및 219550(디와이디)/234920(자이글) 불일치 강제 교정
    """
    for code_k, name_v in MASTER_STOCK_MAP.items():
        db.execute_non_query(
            "UPDATE stock_info SET stock_name = ? WHERE stock_code = ?",
            (name_v, code_k)
        )
    # 구버전/오류 코드 정리
    db.execute_non_query("DELETE FROM stock_info WHERE stock_code IN ('088500', '484730')")
    db.execute_non_query("DELETE FROM portfolio_positions WHERE stock_code IN ('088500', '484730')")

def update_market_data_stub(db: DatabaseManager, dart_client: DartAPIClient, watchlist_mgr: WatchlistManager):
    """
    [데이터 수집 레이어] 실제 등록된 모든 보유 종목 및 관심 종목에 대하여
    네이버 금융 실시간 시세 API 및 DART 실시간 재무제표 100% 연동
    """
    sync_master_stock_info(db)
    logger.info("실제 한국주식 시장 실시간 시세 및 DART 재무 데이터 수집 진행 중...")
    
    real_market_client = RealMarketAPIClient()
    all_targets = db.execute_query("SELECT stock_code, stock_name FROM stock_info")

    for target in all_targets:
        code = target["stock_code"]
        name = target["stock_name"]
        
        # 1. 네이버 금융 API를 통해 100% 실제 일봉 시세 데이터 수집
        real_candles = real_market_client.get_real_daily_candles(code, count=60)
        if real_candles:
            db.insert_kiwoom_daily_batch(real_candles)
        
        # 2. DART API를 통해 100% 실제 기업 재무제표 수집
        dart_fin = dart_client.get_financial_statement(code, fiscal_year=2024)
        if dart_fin:
            if "fiscal_year" not in dart_fin:
                dart_fin["fiscal_year"] = 2024
            if "quarter_code" not in dart_fin:
                dart_fin["quarter_code"] = "11011"
            db.upsert_dart_financials(dart_fin)

    logger.info("실시간 실제 시세 및 DART 재무 데이터 최신화 완료")

def _get_git_commit_sha() -> str:
    try:
        import subprocess
        out = subprocess.check_output(["git", "rev-parse", "HEAD"], timeout=3).decode().strip()
        return out[:8] if out else "UNKNOWN"
    except Exception:
        return "UNKNOWN"

def verify_pipeline_stock_code_consistency(
    raw_kiwoom_positions: Optional[List[Dict[str, Any]]] = None,
    db: Optional[DatabaseManager] = None,
    held_status: Optional[List[Dict[str, Any]]] = None,
    excel_path: Optional[Path] = None,
    html_report: str = "",
    db_manager: Optional[DatabaseManager] = None,
    disclosures: Optional[List[Dict[str, Any]]] = None
) -> None:
    """
    발송 직전 파이프라인 전 계층의 종목코드 및 대응카드/DART공시 완전성 전수 검증:
    """
    target_db = db if db is not None else db_manager
    if target_db is None:
        raise ValueError("DatabaseManager 인스턴스가 제공되어야 합니다.")
    if held_status is None:
        held_status = []

    import re
    import pandas as pd
    from src.notifications.mobile_renderer_v2 import is_meaningful_action_item

    # 1. 키움 원본 (수집된 경우)
    if raw_kiwoom_positions is not None:
        raw_kiwoom_codes = {str(p.get("stock_code") or p.get("pdno")).strip().zfill(6) for p in raw_kiwoom_positions if int(p.get("quantity", 0)) > 0}
    else:
        raw_kiwoom_codes = set()
    
    # 2. DB 활성 종목
    db_rows = target_db.execute_query("SELECT stock_code FROM portfolio_positions WHERE quantity > 0")
    db_active_codes = {str(r["stock_code"]).strip().zfill(6) for r in db_rows} if db_rows else set()

    # 3. held_status
    held_status_codes = {str(h["stock_code"]).strip().zfill(6) for h in held_status}

    # 4. XLSX 파일 (Fail-Closed: 절대 fallback 없이 엄격 검증)
    if excel_path is None:
        err_msg = (
            f"🛑 [XLSX 무결성 검증 실패 - 발송 차단] excel_path=None, "
            f"실패사유=excel_path가 전달되지 않음, expected_held_status_count={len(held_status_codes)}, "
            f"단계=PATH_SPECIFICATION"
        )
        logger.critical(err_msg)
        raise RuntimeError(err_msg)

    p_excel = Path(excel_path)
    if not p_excel.exists():
        err_msg = (
            f"🛑 [XLSX 무결성 검증 실패 - 발송 차단] excel_path={p_excel}, "
            f"실패사유=지정된 XLSX 파일이 디스크에 존재하지 않음, "
            f"expected_held_status_count={len(held_status_codes)}, 단계=FILE_EXISTENCE"
        )
        logger.critical(err_msg)
        raise RuntimeError(err_msg)

    if not p_excel.is_file():
        err_msg = (
            f"🛑 [XLSX 무결성 검증 실패 - 발송 차단] excel_path={p_excel}, "
            f"실패사유=지정된 경로가 일반 파일이 아님, "
            f"expected_held_status_count={len(held_status_codes)}, 단계=FILE_TYPE_CHECK"
        )
        logger.critical(err_msg)
        raise RuntimeError(err_msg)

    try:
        with pd.ExcelFile(p_excel) as xlsx_file:
            target_sheet = None
            for s_name in xlsx_file.sheet_names:
                if "보유" in s_name or "portfolio" in s_name.lower():
                    target_sheet = s_name
                    break
            if target_sheet is None and len(xlsx_file.sheet_names) > 0:
                target_sheet = xlsx_file.sheet_names[0]
            elif target_sheet is None:
                err_msg = (
                    f"🛑 [XLSX 무결성 검증 실패 - 발송 차단] excel_path={p_excel}, "
                    f"실패사유=워크북에 시트가 존재하지 않음, "
                    f"expected_held_status_count={len(held_status_codes)}, 단계=SHEET_LOOKUP"
                )
                logger.critical(err_msg)
                raise RuntimeError(err_msg)

            try:
                # 1) header=0 기본 시도
                xlsx_df = pd.read_excel(xlsx_file, sheet_name=target_sheet, header=0)
                code_col = None
                for col in xlsx_df.columns:
                    if "종목코드" in str(col) or "code" in str(col).lower():
                        code_col = col
                        break
                # 2) header=1 시도 (startrow=1 배너 행 대응)
                if code_col is None:
                    xlsx_df_h1 = pd.read_excel(xlsx_file, sheet_name=target_sheet, header=1)
                    for col in xlsx_df_h1.columns:
                        if "종목코드" in str(col) or "code" in str(col).lower():
                            code_col = col
                            xlsx_df = xlsx_df_h1
                            break
            except Exception as e_sheet:
                err_msg = (
                    f"🛑 [XLSX 무결성 검증 실패 - 발송 차단] excel_path={p_excel}, "
                    f"실패사유=시트('{target_sheet}') 로드 실패 ({e_sheet}), "
                    f"expected_held_status_count={len(held_status_codes)}, 단계=SHEET_LOAD"
                )
                logger.critical(err_msg)
                raise RuntimeError(err_msg)
    except RuntimeError:
        raise
    except Exception as e_open:
        err_msg = (
            f"🛑 [XLSX 무결성 검증 실패 - 발송 차단] excel_path={p_excel}, "
            f"실패사유=XLSX 파일 열기/파싱 실패 ({e_open}), "
            f"expected_held_status_count={len(held_status_codes)}, 단계=WORKBOOK_OPEN"
        )
        logger.critical(err_msg)
        raise RuntimeError(err_msg)

    if code_col is None:
        err_msg = (
            f"🛑 [XLSX 무결성 검증 실패 - 발송 차단] excel_path={p_excel}, "
            f"실패사유=시트('{target_sheet}')에 '종목코드' 열을 찾을 수 없음 (발견된 열: {list(xlsx_df.columns)}), "
            f"expected_held_status_count={len(held_status_codes)}, 단계=COLUMN_LOOKUP"
        )
        logger.critical(err_msg)
        raise RuntimeError(err_msg)

    def _to_code(val):
        if val is None or pd.isna(val):
            return None
        s = str(val).strip().replace("A", "").split(".")[0]
        if s.isdigit() and len(s) <= 6:
            return s.zfill(6)
        return None

    xlsx_codes = set()
    for c in xlsx_df[code_col]:
        code_str = _to_code(c)
        if code_str:
            xlsx_codes.add(code_str)

    if not xlsx_codes:
        err_msg = (
            f"🛑 [XLSX 무결성 검증 실패 - 발송 차단] excel_path={p_excel}, "
            f"실패사유=XLSX '{code_col}' 열에서 유효한 종목코드를 추출할 수 없음 (0건), "
            f"expected_held_status_count={len(held_status_codes)}, 단계=CODE_EXTRACTION"
        )
        logger.critical(err_msg)
        raise RuntimeError(err_msg)

    # 5. 이메일 감사 메타데이터 (V2: data-held-stock-codes="..." 또는 fallback data-stock-code / 괄호 코드)
    held_codes_match = re.search(r'data-held-stock-codes="([^"]*)"', html_report)
    if held_codes_match and held_codes_match.group(1):
        email_audit_held_codes = set(filter(None, held_codes_match.group(1).split(",")))
    else:
        card_codes_raw = set(re.findall(r'data-stock-code="(\d{6})"', html_report))
        if card_codes_raw:
            email_audit_held_codes = card_codes_raw
        else:
            raw_v1_codes = set(re.findall(r'\((\d{6})\)', html_report))
            email_audit_held_codes = raw_v1_codes.intersection(held_status_codes) if raw_v1_codes else raw_v1_codes

    # 6. 본문 노출 대응카드 완전성 및 중복 검증 (expected == rendered)
    expected_action_codes = {str(h["stock_code"]).strip().zfill(6) for h in held_status if is_meaningful_action_item(h)}
    rendered_action_list = re.findall(r'data-stock-code="(\d{6})"', html_report)
    seen_action_cards = set()
    duplicate_action_cards = set()
    for c in rendered_action_list:
        if c in seen_action_cards:
            duplicate_action_cards.add(c)
        seen_action_cards.add(c)

    if duplicate_action_cards:
        err_dup_card = [
            "🛑 [대응카드 중복 감지 - 발송 차단]",
            f"  • duplicate_action_cards: {sorted(list(duplicate_action_cards))}",
            f"  • rendered_action_list ({len(rendered_action_list)}개): {rendered_action_list}"
        ]
        err_msg = "\n".join(err_dup_card)
        logger.critical(err_msg)
        raise RuntimeError(err_msg)

    rendered_action_codes = set(rendered_action_list)

    # 7. 본문 노출 DART 공시 완전성 검증 (expected == rendered)
    if disclosures is not None:
        expected_disclosure_list = []
        seen_disc_ids = set()
        duplicate_disc_ids = set()
        for idx, d in enumerate(disclosures):
            r_no = d.get("rcept_no")
            if r_no is None or not str(r_no).strip():
                err_empty = f"🛑 [DART 공시 rcept_no 누락 또는 빈 문자열 감지 - 발송 차단] index {idx}, item: {d}"
                logger.critical(err_empty)
                raise RuntimeError(err_empty)
            r_id_str = str(r_no).strip()
            if r_id_str in seen_disc_ids:
                duplicate_disc_ids.add(r_id_str)
            seen_disc_ids.add(r_id_str)
            expected_disclosure_list.append(r_id_str)

        if duplicate_disc_ids:
            err_dup = f"🛑 [DART 공시 rcept_no 중복 감지 - 발송 차단] 중복 ID: {sorted(list(duplicate_disc_ids))}"
            logger.critical(err_dup)
            raise RuntimeError(err_dup)

        expected_disclosure_ids = set(expected_disclosure_list)
        rendered_disclosure_list = re.findall(r'data-disclosure-id="([^"]+)"', html_report)
        rendered_disclosure_ids = set(rendered_disclosure_list)
    else:
        expected_disclosure_ids = None
        rendered_disclosure_ids = set()
        rendered_disclosure_list = []

    logger.info("=" * 50)
    logger.info("🔍 [발송 직전 전 계층 종목코드 무결성 검증]")
    logger.info(f"1. 키움 API 원본 수집 ({len(raw_kiwoom_codes)}개): {sorted(list(raw_kiwoom_codes))}")
    logger.info(f"2. DB 활성 종목 ({len(db_active_codes)}개): {sorted(list(db_active_codes))}")
    logger.info(f"3. held_status 평가 ({len(held_status_codes)}개): {sorted(list(held_status_codes))}")
    logger.info(f"4. XLSX 보유시트 ({len(xlsx_codes)}개): {sorted(list(xlsx_codes))}")
    logger.info(f"5. 이메일 감사 메타데이터 ({len(email_audit_held_codes)}개): {sorted(list(email_audit_held_codes))}")
    logger.info(f"6. 기대 대응카드 ({len(expected_action_codes)}개) vs 노출 카드 ({len(rendered_action_list)}개)")
    if expected_disclosure_ids is not None:
        logger.info(f"7. 기대 DART 공시 ({len(expected_disclosure_ids)}건) vs 노출 공시 ({len(rendered_disclosure_list)}건)")
    logger.info("=" * 50)

    # 5계층 완전 동일성 검증
    missing_in_db = sorted(list(raw_kiwoom_codes - db_active_codes))
    missing_in_evaluation = sorted(list(raw_kiwoom_codes - held_status_codes))
    missing_in_xlsx = sorted(list(raw_kiwoom_codes - xlsx_codes))
    missing_in_email = sorted(list(raw_kiwoom_codes - email_audit_held_codes))
    unexpected_extra_codes = sorted(list((db_active_codes | held_status_codes | xlsx_codes | email_audit_held_codes) - raw_kiwoom_codes))

    has_mismatch = bool(missing_in_db or missing_in_evaluation or missing_in_xlsx or missing_in_email or unexpected_extra_codes)
    if has_mismatch:
        err_report = [
            "🛑 [종목코드 불일치 감지 - 발송 차단]",
            f"  • missing_in_db: {missing_in_db}",
            f"  • missing_in_evaluation: {missing_in_evaluation}",
            f"  • missing_in_xlsx: {missing_in_xlsx}",
            f"  • missing_in_email: {missing_in_email}",
            f"  • unexpected_extra_codes: {unexpected_extra_codes}",
            f"  • raw_kiwoom_codes ({len(raw_kiwoom_codes)}개): {sorted(list(raw_kiwoom_codes))}",
            f"  • db_active_codes ({len(db_active_codes)}개): {sorted(list(db_active_codes))}",
            f"  • held_status_codes ({len(held_status_codes)}개): {sorted(list(held_status_codes))}",
            f"  • xlsx_codes ({len(xlsx_codes)}개): {sorted(list(xlsx_codes))}",
            f"  • email_audit_held_codes ({len(email_audit_held_codes)}개): {sorted(list(email_audit_held_codes))}"
        ]
        err_msg = "\n".join(err_report)
        logger.critical(err_msg)
        raise RuntimeError(err_msg)

    # 대응카드 완전 일치 검증 (expected == rendered)
    missing_action_cards = sorted(list(expected_action_codes - rendered_action_codes))
    unexpected_action_cards = sorted(list(rendered_action_codes - expected_action_codes))
    count_mismatch = len(rendered_action_list) != len(expected_action_codes)
    if missing_action_cards or unexpected_action_cards or count_mismatch:
        err_action = [
            "🛑 [대응카드 완전성 검증 실패 - 발송 차단]",
            f"  • missing_action_cards: {missing_action_cards}",
            f"  • unexpected_action_cards: {unexpected_action_cards}",
            f"  • count_mismatch: expected {len(expected_action_codes)}개 vs rendered {len(rendered_action_list)}개",
            f"  • expected_action_codes ({len(expected_action_codes)}개): {sorted(list(expected_action_codes))}",
            f"  • rendered_action_codes ({len(rendered_action_codes)}개): {sorted(list(rendered_action_codes))}"
        ]
        err_msg = "\n".join(err_action)
        logger.critical(err_msg)
        raise RuntimeError(err_msg)

    # P0/A0 미설정 기존 보유종목 Fail-Closed 차단
    # 신규 매수 판별: 이 게이트 도달 시점에 held_status에 포함된 종목 중
    # quantity > 0이고 p0 <= 0이면 앵커 미설정 기존 보유종목으로 판정
    # (단, SUSPENDED_HOLD 종목은 P0/A0 불필요하므로 제외)
    unanchored_existing = []
    for h in held_status:
        qty = h.get("quantity", 0)
        p0 = float(h.get("anchor_price_p0") or 0)
        a0 = float(h.get("anchor_atr_a0") or 0)
        cycle_id = h.get("position_cycle_id")
        code = str(h.get("stock_code", ""))
        name = h.get("stock_name", code)
        trade_mode = str(h.get("trade_mode", "NORMAL"))
        # SUSPENDED_HOLD 종목(자이글 등)은 P0/A0 불필요 -> 차단 제외
        if trade_mode == "SUSPENDED_HOLD":
            continue
        if qty > 0 and (p0 <= 0 or a0 <= 0):
            unanchored_existing.append((code, name, qty, p0, a0, cycle_id))
    if unanchored_existing:
        lines = []
        for code, name, qty, p0, a0, cycle_id in unanchored_existing:
            lines.append(f"  {name}({code}): qty={qty} p0={p0} a0={a0} cycle={cycle_id}")
        err_unanchored = (
            "기존 보유종목의 V4 앵커 기준이 없습니다.\n"
            "승인된 기준값 마이그레이션 전 자동 앵커링 및 메일 발송을 차단합니다.\n"
            f"해당 종목 ({len(unanchored_existing)}개):\n" + "\n".join(lines)
        )
        logger.critical(f"🛑 [기존 보유종목 앵커 미설정 - 발송 차단]\n{err_unanchored}")
        raise RuntimeError(err_unanchored)

    # 손절 래칫 및 익절 추적선 불변성 검증 (Fail-Closed)
    for h in held_status:
        stk_code = h.get("stock_code")
        stk_name = h.get("stock_name", stk_code)
        is_migrated = h.get("is_migrated_anchor", False)

        cur_stop = h.get("confirmed_stop_price") or h.get("kiwoom_stop_tick_price")
        prev_stop = h.get("prev_confirmed_stop") or h.get("previous_confirmed_stop")
        if isinstance(cur_stop, (int, float)) and isinstance(prev_stop, (int, float)):
            if not is_migrated and prev_stop > 0 and cur_stop < prev_stop:
                err_stop = f"🛑 [손절선 하향 무결성 위반 - 발송 차단] {stk_name}({stk_code}) 금일 확정 손절가({cur_stop}) < 전일 손절가({prev_stop})"
                logger.critical(err_stop)
                raise RuntimeError(err_stop)

        if h.get("profit_activation_status") == "ACTIVE":
            cur_trail = h.get("profit_trail_price") or h.get("profit_trail")
            prev_trail = h.get("previous_profit_trail") or h.get("prev_profit_trail")
            if isinstance(cur_trail, (int, float)) and isinstance(prev_trail, (int, float)):
                if prev_trail > 0 and cur_trail < prev_trail:
                    err_trail = f"🛑 [익절 추적선 하향 무결성 위반 - 발송 차단] {stk_name}({stk_code}) 금일 추적선({cur_trail}) < 전일 추적선({prev_trail})"
                    logger.critical(err_trail)
                    raise RuntimeError(err_trail)

    # DART 공시 완전 일치 검증 (disclosures가 제공된 경우)
    if expected_disclosure_ids is not None:
        missing_disclosures = sorted(list(expected_disclosure_ids - rendered_disclosure_ids))
        unexpected_disclosures = sorted(list(rendered_disclosure_ids - expected_disclosure_ids))
        count_mismatch = len(rendered_disclosure_list) != len(expected_disclosure_list)
        if missing_disclosures or unexpected_disclosures or count_mismatch:
            err_disc = [
                "🛑 [DART 공시 완전성 검증 실패 - 발송 차단]",
                f"  • missing_disclosures: {missing_disclosures}",
                f"  • unexpected_disclosures: {unexpected_disclosures}",
                f"  • count_mismatch: expected {len(expected_disclosure_list)}건 vs rendered {len(rendered_disclosure_list)}건",
                f"  • expected_disclosure_ids ({len(expected_disclosure_ids)}건): {sorted(list(expected_disclosure_ids))}",
                f"  • rendered_disclosure_ids ({len(rendered_disclosure_ids)}건): {sorted(list(rendered_disclosure_ids))}"
            ]
            err_msg = "\n".join(err_disc)
            logger.critical(err_msg)
            raise RuntimeError(err_msg)

    logger.info(f"✅ [무결성 검증 통과] 키움API-DB-held_status-XLSX-이메일 전 계층의 {len(held_status_codes)}개 종목코드 및 대응카드({len(expected_action_codes)}개)/DART공시가 100% 완벽히 일치합니다.")

def _normalize_report_asof(report_asof: Optional[datetime]) -> datetime:
    """Normalize an injected Cloud report timestamp to KST while retaining local behavior."""
    if report_asof is None:
        return datetime.now()
    if report_asof.tzinfo is None:
        return report_asof
    from zoneinfo import ZoneInfo
    return report_asof.astimezone(ZoneInfo("Asia/Seoul")).replace(tzinfo=None)


def _resolve_report_session(report_session: Optional[str], report_asof: datetime) -> str:
    """Use the orchestrator's canonical session when one is explicitly supplied."""
    if report_session is not None:
        normalized = str(report_session).strip()
        if normalized not in {"1120", "1335", "1535"}:
            raise ValueError(f"Invalid report_session: {report_session!r}")
        return normalized
    # Preserve direct/local invocation behavior when no orchestrator session exists.
    return "1120" if report_asof.hour < 13 else "1535"


def _resolve_dispatch_tag(session_code: str) -> str:
    """Map canonical report session to user-facing dispatch subject tag."""
    if session_code == "1120":
        return "장중 리포트 1 (11:20)"
    elif session_code == "1335":
        return "장중 리포트 2 (13:35)"
    elif session_code == "1535":
        return "장마감 리포트 (15:35)"
    return f"장중 리포트({session_code})"


def _apply_bb_atr_overlay(
    held_status: List[Dict[str, Any]],
    report_asof: datetime,
    canonical_session: str,
) -> None:
    """Attach a display-only BB-ATR advisory without blocking the report."""
    from src.analysis.bollinger_atr_strategy import (
        generate_bb_atr_advisory,
        make_no_advisory,
    )

    for item in held_status:
        try:
            item["bb_atr"] = generate_bb_atr_advisory(
                item,
                report_asof,
                canonical_session,
            )
        except Exception as bb_err:
            logger.error(
                "[BB-ATR] Overlay failed for %s: %s",
                item.get("stock_code", "UNKNOWN"),
                bb_err,
                exc_info=True,
            )
            item["bb_atr"] = make_no_advisory(
                item,
                report_asof,
                canonical_session,
                "BB_ATR_OVERLAY_ERROR",
            )


def _ensure_report_add_advisories(
    db: DatabaseManager,
    held_status: List[Dict[str, Any]],
    report_asof: datetime,
    advisory_engine: Any = None,
) -> int:
    """Persist missing report-time 45m sidecars without invoking RuntimeScheduler."""
    from src.analysis.add_advisory_engine import AddAdvisory45mEngine
    from src.runtime.krx_calendar import KRXCalendar

    completed_bar = KRXCalendar.get_completed_45m_bar(report_asof)
    if completed_bar is None:
        logger.info("[Report45m] No completed 45m bar at report as-of; preserving waiting state.")
        return 0

    _, _, bar_end = completed_bar
    trading_date = report_asof.strftime("%Y-%m-%d")
    bar_timestamp = f"{trading_date} {bar_end}:00"
    engine = advisory_engine or AddAdvisory45mEngine()
    inserted = 0

    for holding in held_status or []:
        code = str(holding.get("stock_code", "")).strip().zfill(6)
        if not code or code == "000000":
            continue
        current = db.get_latest_add_advisory_for_stock(
            code,
            trading_date=trading_date,
            max_bar_timestamp=bar_timestamp,
        )
        if current and current.get("bar_timestamp") == bar_timestamp and str(current.get("data_quality", "")).startswith("VALID"):
            continue
        entry = engine.evaluate_stock_advisory(
            stock_code=code,
            trading_date=trading_date,
            bar_timestamp=bar_timestamp,
            technical_state_reference=holding.get("technical_state", "UNKNOWN"),
            latest_previous_advisory=current,
        )
        if db.insert_add_advisory_45m(entry):
            inserted += 1
    logger.info(f"[Report45m] Completed bar={bar_timestamp}; inserted={inserted}")
    return inserted


def run_post_market_analysis(
    add_code: Optional[str] = None,
    add_name: Optional[str] = None,
    remove_code: Optional[str] = None,
    force: bool = False,
    skip_email: bool = False,
    report_asof: Optional[datetime] = None,
    report_session: Optional[str] = None,
    report_run_id: Optional[str] = None,
) -> Any:
    """
    실시간 계좌 보유 종목 평가 ➔ 관심 종목 스캔 ➔ 이메일 리포트 발송 실행 함수
    """
    now = _normalize_report_asof(report_asof)
    canonical_session = _resolve_report_session(report_session, now)
    canonical_run_id = report_run_id or f"{now.strftime('%Y%m%d')}_REPORT_{canonical_session}"
    today_str = now.strftime("%Y-%m-%d %H:%M:%S")
    date_str = now.strftime("%Y%m%d")

    commit_sha = os.getenv("GITHUB_SHA") or _get_git_commit_sha()
    event_name = os.getenv("GITHUB_EVENT_NAME", "local_cli")
    render_version = os.getenv("EMAIL_RENDER_VERSION", EMAIL_RENDER_VERSION)

    logger.info("=" * 50)
    logger.info(f" [내 계좌 보유 종목 중심 실시간 정밀 평가 & 리포트 발송] - {today_str}")
    logger.info(f" [Run Metadata] Commit SHA: {commit_sha} | Event Name: {event_name} | Force: {force} | EMAIL_RENDER_VERSION: {render_version}")
    logger.info("=" * 50)

    # 1. 초기화
    current_step = "INITIALIZATION"
    db = DatabaseManager()
    sync_master_stock_info(db)
    kiwoom_client = KiwoomAPIClient()
    dart_client = DartAPIClient()
    engine = TradingEngine(db)
    portfolio_mgr = PortfolioManager(db, kiwoom_client)
    watchlist_mgr = WatchlistManager(db)
    notifier = GmailNotifier()

    # CLI / 옵션 처리 (관심종목 추가/제거)
    if add_code and add_name:
        watchlist_mgr.add_stock(add_code, add_name)
    if remove_code:
        watchlist_mgr.remove_stock(remove_code)

    # 2. 내 계좌 보유 종목 키움 API 실시간 동기화
    current_step = "PORTFOLIO_SYNC"
    raw_kiwoom_positions = None
    try:
        raw_kiwoom_positions = portfolio_mgr.sync_portfolio_from_kiwoom()
    except Exception as e_sync:
        logger.error(f"보유 계좌 동기화 중 오류 발생: {e_sync}", exc_info=True)
        raise

    # 3. 시세 및 재무 데이터 최신화
    current_step = "MARKET_DATA_UPDATE"
    try:
        update_market_data_stub(db, dart_client, watchlist_mgr)
    except Exception as e:
        logger.error(f"데이터 최신화 오류 (기존 데이터로 진행): {e}", exc_info=True)

    # 4. 🔥 [핵심] 내 계좌 보유 종목 정밀 평가
    current_step = "PORTFOLIO_EVALUATION"
    held_status = []
    try:
        held_status = portfolio_mgr.get_held_portfolio_status(engine, live_positions=raw_kiwoom_positions)
        _apply_bb_atr_overlay(held_status, now, canonical_session)
        held_codes = [str(h.get("stock_code")).zfill(6) for h in held_status]
        logger.info(f"[Portfolio Metadata] 키움 전체 보유종목 수: {len(held_status)}개 | 종목코드 목록: {held_codes}")
        if held_status:
            logger.info(f"=== [내 계좌 보유 종목 정밀 평가 결과] (F+T 종합점수 순위순 / 총 {len(held_status)}개) ===")
            for item in held_status:
                rank = item.get("rank", 0)
                code = item["stock_code"]
                name = item["stock_name"]
                pnl_pct = item["pnl_pct"]
                pnl_amt = item["pnl_amount"]
                f_sc = item.get("f_score", 0.0)
                t_sc = item.get("t_score", 0.0)
                fin_sc = item.get("final_score", 0.0)
                act_st = item["action_status"]

                logger.info(
                    f"• [{rank}] {name}({code}) - F점수:{f_sc:.1f} | T점수:{t_sc:.1f} | 종합점수:{fin_sc:.1f} | "
                    f"평가손익률:{pnl_pct:.2f}% ({pnl_amt:,}원) | 대응전략: [{act_st}]"
                )
    except Exception as e_port:
        logger.error(f"보유 종목 평가 중 예외 발생: {e_port}", exc_info=True)
        raise

    # Cloud reports do not run RuntimeScheduler.  Ensure the report's completed
    # 45m sidecar exists before Policy observation and renderer lookup.
    _ensure_report_add_advisories(db, held_status, now)

    # 5. 관심 종목 스캔 및 신규 매매 신호 추출
    current_step = "WATCHLIST_SCAN"
    caught_signals = []
    all_results = []
    try:
        sync_master_stock_info(db)
        stock_rows = db.execute_query("SELECT stock_code, stock_name FROM stock_info")
        total_count = len(stock_rows) if stock_rows else 0

        # 보유 종목 코드 및 종목명 세트 (보유 중인 종목은 신규 매수 신호 리스트에서 100% 원천 예외 처리)
        held_db_rows = db.execute_query("SELECT p.stock_code, s.stock_name FROM portfolio_positions p LEFT JOIN stock_info s ON p.stock_code = s.stock_code WHERE p.quantity > 0")
        held_codes_set = {str(r['stock_code']).strip().zfill(6) for r in held_db_rows} if held_db_rows else set()
        held_names_set = {str(r['stock_name']).strip() for r in held_db_rows if r['stock_name']} if held_db_rows else set()

        if held_status:
            for h in held_status:
                held_codes_set.add(str(h['stock_code']).strip().zfill(6))
                held_names_set.add(str(h['stock_name']).strip())

        for idx, row in enumerate(stock_rows, start=1):
            code = str(row['stock_code']).strip().zfill(6)
            raw_name = str(row['stock_name']).strip()
            name = MASTER_STOCK_MAP.get(code, raw_name if raw_name != code else code)

            try:
                result = engine.analyze_stock(code)
                if result:
                    result['stock_name'] = name
                    all_results.append(result)
                    # 이미 계좌에 보유 중인 종목은 '신규 매수 포착 리스트'에서 100% 제외
                    is_held = (code in held_codes_set) or (name in held_names_set) or any(n in name for n in held_names_set if len(n) > 2)
                    if result['signal_type'] != "관망" and not is_held:
                        caught_signals.append(result)
            except Exception as e_stock:
                logger.error(f"[{name}({code})] 개별 에러: {e_stock}", exc_info=True)

        logger.info("==================================================")
        logger.info(f" [통합 평가 완료] 내 보유종목: {len(held_status)}개 | 매매 신호 포착: {len(caught_signals)}개")
        logger.info("==================================================")

        # 🔥 [안전장치] 보유종목 평가 유효성 검증: DB에 보유종목이 있는데 평가 결과가 0개인 경우 메일 발송 차단 및 에러 종료
        if not held_status or len(held_status) == 0:
            if held_db_rows and len(held_db_rows) > 0:
                logger.critical(f"🛑 [발송 차단] 보유종목 평가 실패(0개 검출, DB상 {len(held_db_rows)}개 존재). 불완전 데이터 발송 방지를 위해 작업을 중단합니다.")
                raise RuntimeError(f"보유종목 평가 실패: DB상 {len(held_db_rows)}개 존재하나 평가 결과 0개 검출")

        # 6. 📊 실제 분석 데이터 종합 엑셀파일(.xlsx) 생성 및 지메일 첨부 발송
        current_step = "REPORT_EXPORT_AND_DISPATCH"
        now_dt = now
        date_str_file = now_dt.strftime("%Y-%m-%d %H:%M")
        report_timestamp_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")

        # 리포트 시점 Policy Shadow 1회 관측 수행
        policy_obs_failed = False
        try:
            from src.policy.policy_shadow_observer import observe_report_policy_shadow
            dispatch_id = f"{now_dt.strftime('%Y%m%d')}_{canonical_session}"
            observe_report_policy_shadow(db, held_status, run_id=dispatch_id, asof_dt=now_dt)
        except Exception as e_obs:
            policy_obs_failed = True
            logger.warning(f"[PolicyShadow] 리포트 시점 관측 중 경고 (V4 리포트 계속 진행): {e_obs}")

        try:
            if policy_obs_failed:
                policy_display = {"status": "UNAVAILABLE", "rows": [], "transition_rows": [], "reason": "OBSERVATION_FAILED"}
            else:
                policy_display = PolicyShadowReader().build_report_payload(now_dt, held_status)
        except Exception as e_read:
            logger.warning(f"[PolicyShadow] 리포트 페이로드 구성 중 오류: {e_read}")
            policy_display = {"status": "UNAVAILABLE", "rows": [], "transition_rows": [], "reason": str(e_read)}
        
        excel_path = create_analysis_excel_report(
            date_str=date_str_file,
            held_portfolio=held_status,
            all_results=all_results,
            db_manager=db,
            policy_display=policy_display
        )

        csv_held_path = excel_path.parent / excel_path.name.replace('stock_analysis_', 'portfolio_monitoring_').replace('.xlsx', '.csv')
        csv_summary_path = excel_path.parent / excel_path.name.replace('stock_analysis_', 'stock_summary_').replace('.xlsx', '.csv')

        now_dt = now
        hour_now = now_dt.hour
        session_code = canonical_session
        dispatch_id = f"{now_dt.strftime('%Y%m%d')}_{session_code}"
        dispatch_tag = _resolve_dispatch_tag(session_code)

        subject = f"[{dispatch_tag}] {now_dt.month}월 {now_dt.day}일 V4-PILOT-C 주요 대응 및 보유종목 공시"
        
        # 6. 🔥 보유 종목 당일 DART 주요 신규 공시 및 1~3줄 브리핑 수집
        disclosures = []
        try:
            disclosures = dart_client.get_recent_disclosures_briefing(held_status, target_date=date_str)
        except Exception as e_disc:
            logger.warning(f"DART 공시 브리핑 수집 중 오류: {e_disc}")

        html_report = notifier.generate_html_report(
            date_str=report_timestamp_str,
            total_count=len(all_results),
            caught_signals=caught_signals,
            all_results=all_results,
            held_portfolio=held_status,
            disclosures=disclosures,
            policy_display=policy_display
        )

        render_version = getattr(notifier, "render_version", EMAIL_RENDER_VERSION)
        if render_version != "V2" or getattr(notifier, "fallback_occurred", False):
            logger.critical("🛑 [V2 렌더러 실패] 운영 기본 렌더러는 반드시 V2여야 하며 V1 Fallback은 금지됩니다. 발송을 차단합니다.")
            raise RuntimeError("EMAIL_RENDER_VERSION=V2 미준수 또는 V1 fallback 발생으로 발송 차단")

        # 🔥 [발송 전 HTML 구조 게이트]
        REQUIRED_HTML_STRINGS = [
            'data-render-version="V2"',
            'data-held-stock-codes=',
            '전체 보유종목 현황',
            '45M ADD ADVISORY',
            'DART 주요 공시'
        ]

        FORBIDDEN_HTML_STRINGS = [
            '5단계 매매 대응전략 매트릭스',
            '일봉/45분봉 수급 원자값 연동 표',
            '내 계좌 보유 종목 정밀 평가',
            '관심 종목 리포트',
            '신규 매수 신호',
            'data-render-version="V1"'
        ]

        missing_req = [s for s in REQUIRED_HTML_STRINGS if s not in html_report]
        found_forbid = [s for s in FORBIDDEN_HTML_STRINGS if s in html_report]

        if missing_req or found_forbid:
            err_gate = [
                "🛑 [HTML 구조 게이트 위반 - 발송 차단]",
                f"  • 누락된 필수 문자열: {missing_req}",
                f"  • 발견된 금지 문자열: {found_forbid}"
            ]
            err_msg = "\n".join(err_gate)
            logger.critical(err_msg)
            raise RuntimeError(err_msg)

        # 🔥 [발송 직전 전수 검증] 키움 API 원본 - DB - held_status - XLSX - 이메일 감사 메타데이터 간 종목코드 100% 동일성 검증
        verify_pipeline_stock_code_consistency(raw_kiwoom_positions, db, held_status, excel_path, html_report, disclosures=disclosures)

        import hashlib
        balance_signature = "_".join(sorted([f"{h['stock_code']}:{h.get('quantity',0)}:{h.get('current_price',0)}" for h in held_status]))
        dispatch_fingerprint = hashlib.md5(f"{date_str}_{session_code}_{balance_signature}".encode()).hexdigest()[:12]
        fingerprint_id = f"FP_{dispatch_fingerprint}"

        # 🔥 skip_email 모드: CloudRunner 등 외부 오케스트레이터가 발송 제어할 때 사용
        if skip_email:
            report_payload = {
                "excel_path": excel_path,
                "csv_held_path": csv_held_path,
                "csv_summary_path": csv_summary_path,
                "html_report": html_report,
                "subject": subject,
                "dispatch_id": dispatch_id,
                "dispatch_tag": dispatch_tag,
                "notifier": notifier,
                "attachments": [excel_path, csv_held_path, csv_summary_path],
                "render_version": render_version,
                "policy_display": policy_display,
                "fingerprint_id": fingerprint_id,
            }
            return held_status, caught_signals, report_payload

        # 🔥 중복 발송 방지 검사 (동일 날짜/회차 or 동일 잔고 해시 메일 이미 발송 시 건너뛰기)
        if not force and (db.is_dispatch_already_sent(dispatch_id) or db.is_dispatch_already_sent(fingerprint_id)):
            logger.info(f"🛑 [메일 발송 결과: 중복 건너뜀] {dispatch_id} (Fingerprint: {dispatch_fingerprint}) 리포트가 오늘 이미 성공적으로 발송되었습니다. (강제 재발송 필요 시 --force 옵션 사용)")
        else:
            sent_success = notifier.send_email(
                subject=subject,
                html_content=html_report,
                attachments=[excel_path, csv_held_path, csv_summary_path]
            )
            if sent_success:
                db.record_dispatch_success(dispatch_id, dispatch_tag, notifier.recipient_email, subject)
                db.record_dispatch_success(fingerprint_id, dispatch_tag, notifier.recipient_email, subject)
                logger.info(f"✅ [메일 발송 결과: 발송 성공] 내 종목 정밀 평가 지메일 리포트 성공 발송 및 발송 기록 완료! [식별자: {dispatch_id}, FP: {dispatch_fingerprint}, 수신인: {notifier.recipient_email}, 보유종목수: {len(held_status)}개, 버전: {render_version}]")
            else:
                logger.critical(f"❌ [메일 발송 결과: 발송 실패] 지메일 발송에 실패했습니다. (수신인: {notifier.recipient_email})")
                raise RuntimeError(f"지메일 발송 실패: recipient={notifier.recipient_email}, subject={subject}")

    except Exception as e:
        logger.critical(f"시스템 실행 중 예외 발생: {e}", exc_info=True)
        try:
            now_dt = datetime.now()
            session_name = "11:20 장중" if now_dt.hour < 13 else "15:35 장마감"
            date_korean = f"{now_dt.month}월 {now_dt.day}일"
            error_reason = str(e)
            if "notifier" in locals() and notifier is not None:
                notifier.send_failure_alert(
                    session_name=session_name,
                    failed_step=current_step,
                    error_reason=error_reason,
                    date_str_korean=date_korean
                )
        except Exception as e_fail_alert:
            logger.error(f"[FAILURE_ALERT_ERROR] 실패 알림 메일 발송 실패: {e_fail_alert}", exc_info=True)
        raise

    return held_status, caught_signals

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stock Analysis System CLI")
    parser.add_argument("--add-code", type=str, help="관심종목 추가 코드")
    parser.add_argument("--add-name", type=str, help="관심종목 추가 종목명")
    parser.add_argument("--remove-code", type=str, help="관심종목 제거 코드")
    parser.add_argument("--add-holding", nargs=4, metavar=('CODE', 'NAME', 'QTY', 'PRICE'), help="실제 보유 종목 추가 (예: --add-holding 005930 삼성전자 10 70000)")
    parser.add_argument("--clear-holdings", action="store_true", help="기존 테스트/모의 보유 종목 모두 삭제")
    parser.add_argument("--force", action="store_true", help="중복 발송 방지 검사를 우회하여 메일 강제 재발송")
    args = parser.parse_args()

    db_init = DatabaseManager()
    p_mgr = PortfolioManager(db_init)

    if args.clear_holdings:
        p_mgr.clear_all_holdings()

    if args.add_holding:
        h_code, h_name, h_qty, h_price = args.add_holding
        p_mgr.add_holding(h_code, h_name, int(h_qty), float(h_price))

    run_post_market_analysis(
        add_code=args.add_code,
        add_name=args.add_name,
        remove_code=args.remove_code,
        force=args.force
    )
