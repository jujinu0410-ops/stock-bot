import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Dict, Any, List, Optional
import re
from config.settings import GMAIL_USER, GMAIL_APP_PASSWORD, EMAIL_RENDER_VERSION
from src.notifications.mobile_renderer_v2 import generate_mobile_html_report_v2
from src.utils.logger import logger

def format_cho_array_html(arr):
    """
    채킨 오실레이터 수치를 양수(붉은색) / 음수(파란색)로 시각화하고,
    2봉간 상승/하락 추세 화살표 (상승: 붉은색 ▲, 하락: 파란색 ▼) 표기
    """
    if not arr or len(arr) < 2:
        return "[0, 0]"
    
    v1 = int(arr[0])
    v2 = int(arr[1])

    c1_html = f'<span style="color:#DC2626; font-weight:bold;">+{v1:,}</span>' if v1 >= 0 else f'<span style="color:#1D4ED8; font-weight:bold;">{v1:,}</span>'
    c2_html = f'<span style="color:#DC2626; font-weight:bold;">+{v2:,}</span>' if v2 >= 0 else f'<span style="color:#1D4ED8; font-weight:bold;">{v2:,}</span>'

    if v2 > v1:
        arrow_html = ' <span style="color:#DC2626; font-size:12px; font-weight:bold;">▲</span>'
    elif v2 < v1:
        arrow_html = ' <span style="color:#1D4ED8; font-size:12px; font-weight:bold;">▼</span>'
    else:
        arrow_html = ''

    return f"[{c1_html}, {c2_html}]{arrow_html}"

def format_adx_di_dominance_html(di_dom_str):
    """ADX +DI/-DI 우세방향을 +DI우세(붉은색), -DI우세(파란색)로 시각화"""
    if not di_dom_str or di_dom_str == "-":
        return "-"
    if "-DI우세" in di_dom_str:
        return f'<span style="color:#1D4ED8; font-weight:bold;">{di_dom_str}</span>'
    elif "+DI우세" in di_dom_str:
        return f'<span style="color:#DC2626; font-weight:bold;">{di_dom_str}</span>'
    else:
        return f'<span style="color:#475569;">{di_dom_str}</span>'

def format_strategy_action_html(raw_action):
    """대응전략 텍스트 정제 (확정 순위 등 제거하고 핵심 전략만 간략 표기) 및 컬러링"""
    clean = raw_action
    clean = re.sub(r'\[[🟢🔄⚠️🚨⚫]\s*(?:확정|잠정|ETF|순위제외)\s*\d*위?\s*', '', clean)
    clean = re.sub(r'\s*\((?:확정|잠정|ETF|순위제외)\s*\d*위?\)', '', clean)
    clean = re.sub(r'\s*\(5순위\)', '', clean)
    clean = clean.replace('[', '').replace(']', '').strip()
    clean = re.sub(r'^\s*\(|\)\s*$', '', clean).strip()

    if "거래정지" in clean or "SUSPENDED" in clean:
        return '<span style="color:#475569; font-weight:bold; background:#F1F5F9; padding:2px 6px; border-radius:4px; border:1px solid #94A3B8;">⚫ 거래정지 보류/공시감시</span>'
    elif "매도" in clean and "추매금지" not in clean:
        return f'<span style="color:#1D4ED8; font-weight:bold;">{clean}</span>'
    elif "매수" in clean:
        return f'<span style="color:#DC2626; font-weight:bold;">{clean}</span>'
    elif "비중과다" in clean or "보류" in clean or "미확정" in clean or "이상 급등" in clean:
        return f'<span style="color:#B45309; font-weight:bold;">{clean}</span>'
    elif "보유" in clean or "홀딩" in clean:
        return f'<span style="color:#059669; font-weight:bold;">{clean}</span>'
    else:
        return f'<span style="color:#334155; font-weight:bold;">{clean}</span>'

class GmailNotifier:
    """
    분석 리포트 및 트레일링 가격 가이드를 HTML 지메일로 발송하는 알림 모듈
    """
    def __init__(self, sender_email: str = GMAIL_USER, app_password: str = GMAIL_APP_PASSWORD):
        self.gmail_user = sender_email
        self.gmail_password = app_password
        self.recipient_email = sender_email

    def send_email(self, subject: str, html_content: str, attachments: List[Path] = None) -> bool:
        if not self.gmail_user or not self.gmail_password:
            logger.warning("[Gmail] GMAIL_SENDER_EMAIL 또는 GMAIL_APP_PASSWORD가 설정되지 않았습니다.")
            return False

        try:
            msg = MIMEMultipart()
            msg["From"] = f"Stock Bot <{self.gmail_user}>"
            msg["To"] = self.recipient_email
            msg["Subject"] = subject

            html_part = MIMEText(html_content, "html", "utf-8")
            msg.attach(html_part)

            if attachments:
                from email.mime.application import MIMEApplication
                for att_path in attachments:
                    if att_path and Path(att_path).exists():
                        p_path = Path(att_path)
                        with open(p_path, "rb") as f:
                            ext = p_path.suffix.lower()
                            subtype = "vnd.openxmlformats-officedocument.spreadsheetml.sheet" if ext == ".xlsx" else "octet-stream"
                            part = MIMEApplication(f.read(), _subtype=subtype)
                            part.add_header('Content-Disposition', 'attachment', filename=p_path.name)
                            msg.attach(part)
                            logger.info(f"[Gmail] 첨부 파일 추가 완료 (MIME: application/{subtype}): {p_path.name}")

            logger.info(f"[Gmail] Gmail SMTP 서버 접속 시도 중... ({self.gmail_user} -> {self.recipient_email})")
            
            max_retries = 3
            for attempt in range(1, max_retries + 1):
                try:
                    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
                        server.login(self.gmail_user, self.gmail_password)
                        server.sendmail(self.gmail_user, [self.recipient_email], msg.as_string())
                    logger.info(f"[Gmail] 이메일 및 첨부 파일 발송 성공! (제목: {subject})")
                    return True
                except Exception as e_send:
                    logger.warning(f"[Gmail] 이메일 발송 실패 (시도 {attempt}/{max_retries}): {e_send}")
                    if attempt == max_retries:
                        raise e_send
        except Exception as e:
            logger.error(f"[Gmail] 이메일 발송 최종 예외 발생: {e}", exc_info=True)
            return False

    def generate_html_report(
        self,
        date_str: str,
        total_count: int,
        caught_signals: List[Dict[str, Any]],
        all_results: List[Dict[str, Any]],
        held_portfolio: List[Dict[str, Any]] = None,
        disclosures: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        환경변수 EMAIL_RENDER_VERSION에 따라 V1 또는 V2 렌더러로 라우팅 (V2 예외 시 V1으로 자동 Fallback)
        """
        render_ver = getattr(self, "render_version", EMAIL_RENDER_VERSION)
        if render_ver not in ("V1", "V2"):
            logger.warning(f"[GmailNotifier] 유효하지 않은 EMAIL_RENDER_VERSION='{render_ver}'. 기본 V1 렌더러로 안전하게 복구합니다.")
            render_ver = "V1"

        if render_ver == "V2":
            try:
                return generate_mobile_html_report_v2(
                    date_str=date_str,
                    total_count=total_count,
                    caught_signals=caught_signals,
                    all_results=all_results,
                    held_portfolio=held_portfolio,
                    disclosures=disclosures
                )
            except Exception as e:
                logger.error(f"[GmailNotifier] V2 모바일 렌더러 실행 중 예외 발생, V1 렌더러로 안전 복구(Fallback): {e}", exc_info=True)
                return self.generate_html_report_v1(
                    date_str=date_str,
                    total_count=total_count,
                    caught_signals=caught_signals,
                    all_results=all_results,
                    held_portfolio=held_portfolio,
                    disclosures=disclosures
                )
        else:
            return self.generate_html_report_v1(
                date_str=date_str,
                total_count=total_count,
                caught_signals=caught_signals,
                all_results=all_results,
                held_portfolio=held_portfolio,
                disclosures=disclosures
            )

    def generate_html_report_v1(
        self,
        date_str: str,
        total_count: int,
        caught_signals: List[Dict[str, Any]],
        all_results: List[Dict[str, Any]],
        held_portfolio: List[Dict[str, Any]] = None,
        disclosures: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        [V1 렌더러 - 불변 보존] 보유 종목 중심 정밀 평가 리포트 HTML 생성 (2개 매트릭스 표 + DART 공시 브리핑 글 박스)
        """
        held_count = len(held_portfolio) if held_portfolio else 0
        profit_count = 0
        loss_count = 0

        # --- 0. 🔥 보유 종목 신규 DART 주요 공시 브리핑 글 박스 생성 ---
        disclosure_box_html = ""
        if disclosures and len(disclosures) > 0:
            items_html = ""
            for d in disclosures:
                s_name = d.get('stock_name', '')
                s_code = d.get('stock_code', '')
                r_name = d.get('report_nm', '')
                d_link = d.get('link', '#')
                d_sum = d.get('summary', '')
                d_imp = d.get('impact', '')
                d_guide = d.get('guide', '')
                
                items_html += f"""
                <div style="margin-bottom:10px; padding:10px 12px; background:#FFFFFF; border:1px solid #E2E8F0; border-radius:6px;">
                    <div style="font-weight:bold; font-size:12.5px; color:#0F172A; margin-bottom:4px;">
                        📌 <span style="color:#1E3A8A;">{s_name}({s_code})</span> - <a href="{d_link}" target="_blank" style="color:#2563EB; text-decoration:underline;">{r_name}</a>
                    </div>
                    <div style="font-size:11.5px; color:#334155; line-height:1.5;">
                        • <b>공시 요약</b>: {d_sum}<br>
                        • <b>시장 의미</b>: {d_imp}<br>
                        • <b>대응 가이드</b>: {d_guide}
                    </div>
                </div>
                """

            disclosure_box_html = f"""
            <div style="background:#F8FAFC; border:1px solid #94A3B8; border-left:4px solid #2563EB; border-radius:8px; padding:14px; margin-bottom:20px; box-shadow:0 2px 8px rgba(0,0,0,0.03);">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; border-bottom:1px solid #CBD5E1; padding-bottom:6px;">
                    <h3 style="margin:0; font-size:14px; color:#1E3A8A; font-weight:bold;">
                        📢 보유 종목 최신 DART 주요 공시 & 브리핑 (총 {len(disclosures)}건)
                    </h3>
                    <span style="font-size:10.5px; color:#64748B;">클릭 시 전자공시 원문 이동</span>
                </div>
                {items_html}
            </div>
            """

        # --- 1. 내 보유 종목 섹션 HTML ---
        held_section_html = ""
        if held_portfolio:
            def _get_portfolio_sort_key(item):
                action_st = item.get("action_status", "")
                trade_mode = item.get("trade_mode", "NORMAL")
                needs_sell_or_risk_action = (
                    trade_mode in ("RECOVERY", "EMERGENCY", "HOLD") or
                    "매도" in action_st or "손절" in action_st or "축소" in action_st or 
                    "보류" in action_st or "미확정" in action_st or "이상 급등" in action_st
                )
                priority_group = 0 if needs_sell_or_risk_action else 1
                final_score = item.get("final_score", 0.0)
                return (priority_group, final_score)

            held_portfolio_sorted = sorted(held_portfolio, key=_get_portfolio_sort_key)
            total_eval_inv = sum(h.get("total_invested", 0) for h in held_portfolio)
            total_eval_val = sum(h.get("eval_amount", 0) for h in held_portfolio)
            total_pnl_amt = total_eval_val - total_eval_inv
            total_pnl_pct = round((total_pnl_amt / total_eval_inv) * 100.0, 2) if total_eval_inv > 0 else 0.0
            pnl_color_total = "#10B981" if total_pnl_amt >= 0 else "#EF4444"
            pnl_sign_total = "+" if total_pnl_amt >= 0 else ""

            profit_count = sum(1 for h in held_portfolio if h.get("pnl_pct", 0.0) >= 0)
            loss_count = sum(1 for h in held_portfolio if h.get("pnl_pct", 0.0) < 0)

            held_rows_html = ""
            meaningful_items = []
            for h in held_portfolio_sorted:
                daily_chg = h.get('daily_change_pct', 0.0)
                atr_pct = h.get('atr_pct', 3.0)
                action_st = h.get('action_status', '')
                trade_mode = h.get('trade_mode', 'NORMAL')
                if "계속 보유" not in action_st or abs(daily_chg) >= max(2.5, atr_pct * 0.8) or trade_mode in ("EMERGENCY", "RECOVERY", "HOLD"):
                    meaningful_items.append(h)

            for rank_idx, h in enumerate(held_portfolio_sorted, start=1):
                code = h.get('stock_code')
                name = h.get('stock_name')
                cur_p = h.get('current_price')
                daily_chg = h.get('daily_change_pct', 0.0)
                pnl_pct = h.get('pnl_pct', 0.0)
                pnl_amt = h.get('pnl_amount', 0)
                f_sc = h.get('f_score', 0.0)
                t_sc = h.get('t_score', 0.0)
                final_sc = h.get('final_score', 0.0)
                action_st = h.get('action_status', '보유')
                is_etf = h.get('is_etf', False)
                f_confirmed = h.get('f_score_confirmed', True)

                pnl_color = "#DC2626" if pnl_pct >= 0 else "#2563EB"
                pnl_sign = "+" if pnl_pct >= 0 else ""

                if daily_chg > 0:
                    price_color = "#DC2626"
                    price_display_html = f'<span style="color:{price_color}; font-weight:bold; font-size:12px;">{cur_p:,}원</span><br><span style="font-size:9.5px; color:{price_color}; font-weight:bold;">( +{daily_chg:.2f}%)</span>'
                elif daily_chg < 0:
                    price_color = "#2563EB"
                    price_display_html = f'<span style="color:{price_color}; font-weight:bold; font-size:12px;">{cur_p:,}원</span><br><span style="font-size:9.5px; color:{price_color}; font-weight:bold;">({daily_chg:.2f}%)</span>'
                else:
                    price_color = "#475569"
                    price_display_html = f'<span style="color:{price_color}; font-weight:bold; font-size:12px;">{cur_p:,}원</span><br><span style="font-size:9.5px; color:{price_color};">(0.00%)</span>'

                if trade_mode == "SUSPENDED_HOLD" or code == "234920":
                    score_combined_cell = "<span style='font-weight:bold; color:#64748B; font-size:12px;'>N/A</span><br><span style='font-size:9.5px; color:#64748B;'>(거래정지)</span>"
                elif is_etf:
                    score_combined_cell = f"<span style='font-weight:bold; color:#4338CA; font-size:13px;'>{t_sc:.1f}점</span><br><span style='font-size:9.5px; color:#64748B;'>(ETF T점수)</span>"
                elif not f_confirmed:
                    score_combined_cell = f"<span style='font-weight:bold; color:#D97706; font-size:13px;'>{final_sc:.1f}점</span><br><span style='font-size:9.5px; color:#D97706;'>(F:{f_sc:.1f} / T:{t_sc:.1f})</span>"
                else:
                    score_combined_cell = f"<span style='font-weight:bold; color:#059669; font-size:13px;'>{final_sc:.1f}점</span><br><span style='font-size:9.5px; color:#64748B;'>(F:{f_sc:.1f} / T:{t_sc:.1f})</span>"

                atr_v = h.get('atr_14', 0.0)
                if trade_mode == "SUSPENDED_HOLD" or code == "234920":
                    atr_display = "N/A (거래정지)"
                else:
                    atr_display = f"{atr_v:,.0f}원 ({h.get('atr_pct', 0.0):.1f}%)" if atr_v > 0 else "-"

                clean_strategy = action_st
                if trade_mode == "SUSPENDED_HOLD" or code == "234920":
                    badge_bg, badge_border, badge_color = "#F1F5F9", "#94A3B8", "#475569"
                    action_kw = "⚫ 거래정지 보류/공시감시"
                elif "수동" in clean_strategy or "USER_OVERRIDE" in clean_strategy or code == "348340":
                    badge_bg, badge_border, badge_color = "#FFFBEB", "#FCD34D", "#B45309"
                    action_kw = "⚠️ DART미확정 (수동감시 31주)"
                elif not f_confirmed or "재무" in clean_strategy or "미확정" in clean_strategy:
                    badge_bg, badge_border, badge_color = "#FFFBEB", "#FCD34D", "#B45309"
                    action_kw = "⚠️ DART 재무미확정 (보류)"
                elif "이상 급등" in clean_strategy:
                    badge_bg, badge_border, badge_color = "#FFFBEB", "#FCD34D", "#B45309"
                    action_kw = "⚠️ NATR 이상 급등 (보류)"
                elif "초과" in clean_strategy and "축소" in clean_strategy:
                    badge_bg, badge_border, badge_color = "#FEF2F2", "#FCA5A5", "#DC2626"
                    action_kw = "⚠️ 20%초과 분할축소"
                elif "비중과다" in clean_strategy and "CHO유출" in clean_strategy:
                    badge_bg, badge_border, badge_color = "#FFFBEB", "#FCD34D", "#B45309"
                    action_kw = "⚠️ 비중과다 + CHO유출"
                elif "비중과다" in clean_strategy and "OBV이탈" in clean_strategy:
                    badge_bg, badge_border, badge_color = "#FFFBEB", "#FCD34D", "#B45309"
                    action_kw = "⚠️ 비중과다 + OBV이탈"
                elif "손실축소" in clean_strategy or "RECOVERY" in clean_strategy:
                    badge_bg, badge_border, badge_color = "#EFF6FF", "#93C5FD", "#1D4ED8"
                    action_kw = "🔄 손실축소 분할매도"
                elif "단기 매도" in clean_strategy or "단기매도" in clean_strategy or "매도" in clean_strategy:
                    badge_bg, badge_border, badge_color = "#EFF6FF", "#93C5FD", "#1D4ED8"
                    action_kw = "🚨 매도 대응"
                elif "분할매수" in clean_strategy or "매수" in clean_strategy:
                    badge_bg, badge_border, badge_color = "#FEF2F2", "#FCA5A5", "#DC2626"
                    action_kw = "🎯 분할매수"
                elif "비중과다" in clean_strategy:
                    badge_bg, badge_border, badge_color = "#FFFBEB", "#FCD34D", "#B45309"
                    action_kw = "⚠️ 비중과다 보유"
                elif "익절" in clean_strategy or "차익" in clean_strategy:
                    badge_bg, badge_border, badge_color = "#ECFDF5", "#6EE7B7", "#065F46"
                    action_kw = "익절/차익실현"
                else:
                    badge_bg, badge_border, badge_color = "#F0F9FF", "#BAE6FD", "#0369A1"
                    action_kw = "🟢 계속 보유/홀딩"

                strategy_cell_html = f'<span style="background:{badge_bg}; border:1px solid {badge_border}; color:{badge_color}; padding:3px 6px; border-radius:5px; font-weight:bold; font-size:10px; display:inline-block;">{action_kw}</span>'

                if len(name) > 9 and " " in name:
                    parts = name.split(" ", 1)
                    name_formatted = f"{parts[0]}<br>{parts[1]}"
                else:
                    name_formatted = name

                adx_45m = h.get('adx_14_45m', 0.0)
                if trade_mode == "SUSPENDED_HOLD" or code == "234920":
                    atr_display_html = f'<span style="font-weight:bold; color:#64748B;">N/A (거래정지)</span><br><span style="font-size:9px; color:#64748B;">45m: N/A</span>'
                else:
                    atr_display_html = f'<span style="font-weight:bold; color:#334155;">{atr_display}</span><br><span style="font-size:9px; color:#4338CA; font-weight:bold;">45m ADX: {adx_45m:.1f}</span>'

                held_rows_html += f"""
                <tr style="border-bottom:1px solid #E2E8F0; font-size:11px;">
                    <td style="padding:6px 4px; background:#F8FAFC;">{strategy_cell_html}</td>
                    <td style="padding:6px 3px; font-weight:bold; color:#0F172A; max-width:85px; word-break:break-word; line-height:1.25;">{name_formatted}<br><span style="font-size:9.5px; color:#64748B; font-weight:normal;">({code})</span></td>
                    <td style="padding:6px 3px; text-align:center; background:#F5F3FF;">{score_combined_cell}</td>
                    <td style="padding:6px 3px; text-align:center;">{price_display_html}</td>
                    <td style="padding:6px 3px; color:#475569; text-align:center;">{atr_display_html}</td>
                    <td style="padding:6px 3px; font-weight:bold; color:{pnl_color}; text-align:center;">{pnl_sign}{pnl_pct:.2f}%<br><span style="font-size:9.5px; font-weight:normal;">({pnl_sign}{pnl_amt:,}원)</span></td>
                </tr>
                """

            summary_matrix_rows_html = ""
            for rank_idx, h in enumerate(held_portfolio_sorted, start=1):
                code = h.get('stock_code', '')
                name = h.get('stock_name', '')
                action_st = h.get('action_status', '보유')
                trade_mode = h.get('trade_mode', 'NORMAL')
                
                if trade_mode == "SUSPENDED_HOLD" or code == "234920":
                    obv_daily_html = '<span style="color:#64748B; font-weight:bold;">N/A</span>'
                    obv_45m_html = '<span style="color:#64748B; font-weight:bold;">N/A</span>'
                    daily_cho_html = '<span style="color:#64748B;">[N/A, N/A]</span>'
                    intra_cho_html = '<span style="color:#64748B;">[N/A, N/A]</span>'
                    di_dom_html = '<span style="color:#64748B;">N/A (거래정지)</span>'
                    colored_strategy_html = '<span style="color:#475569; font-weight:bold; background:#F1F5F9; padding:2px 6px; border-radius:4px; border:1px solid #94A3B8;">⚫ 거래정지 보류/공시감시</span>'
                else:
                    obv_d_date = h.get('obv_dead_date', 'N/A')
                    obv_d_days = h.get('obv_dead_elapsed_days', 0)
                    obv_daily_html = f'<span style="color:#1D4ED8; font-weight:bold;">{obv_d_date} ({obv_d_days}일차)</span>' if obv_d_date != "N/A" and "상승" not in obv_d_date and obv_d_days >= 1 else '<span style="color:#DC2626; font-size:14px; font-weight:bold;">▲</span>'

                    is_45m_obv_dead = h.get('is_obv_dead', False) or (h.get('obv_45m_trend', '') and '데드' in h.get('obv_45m_trend', ''))
                    obv_45m_html = '<span style="color:#1D4ED8; font-weight:bold;">45m 이탈</span>' if is_45m_obv_dead else '<span style="color:#DC2626; font-size:14px; font-weight:bold;">▲</span>'

                    daily_cho_html = format_cho_array_html(h.get('daily_cho_recent2', [0, 0]))
                    intra_cho_html = format_cho_array_html(h.get('intraday_cho_recent2', [0, 0]))
                    di_dom_html = format_adx_di_dominance_html(h.get('adx_di_dominance', '-'))
                    colored_strategy_html = format_strategy_action_html(action_st)

                summary_matrix_rows_html += f"""
                <tr style="border-bottom:1px solid #E2E8F0; font-size:11px;">
                    <td style="padding:6px 6px; background:#EFF6FF; text-align:center;">{colored_strategy_html}</td>
                    <td style="padding:6px 4px; font-weight:bold; color:#0F172A;">{name} <span style="font-size:9.5px; color:#64748B; font-weight:normal;">({code})</span></td>
                    <td style="padding:6px 4px; text-align:center;">{obv_daily_html}</td>
                    <td style="padding:6px 4px; text-align:center;">{obv_45m_html}</td>
                    <td style="padding:6px 4px; text-align:center;">{daily_cho_html}</td>
                    <td style="padding:6px 4px; text-align:center;">{intra_cho_html}</td>
                    <td style="padding:6px 4px; color:#475569;">{di_dom_html}</td>
                </tr>
                """

            meaningful_cards_html = ""
            for sw in meaningful_items:
                s_name, s_code = sw.get('stock_name'), sw.get('stock_code')
                s_chg, s_act = sw.get('daily_change_pct', 0.0), sw.get('action_status', '보유')
                s_mode = sw.get('trade_mode', 'NORMAL')
                s_ttarget = sw.get('kiwoom_target_tick_price', 'HOLD')
                s_tstop = sw.get('kiwoom_stop_tick_price', 'HOLD')
                s_at = sw.get('current_completed_atr', sw.get('atr_14', 0))
                s_trail_delta = abs(sw.get('profit_trail_delta', int(s_at * 0.8)))
                s_color = "#DC2626" if s_chg >= 0 else "#2563EB"
                s_sign = "+" if s_chg >= 0 else ""
                clean_act_display = format_strategy_action_html(s_act)
                
                if s_code == "234920" or s_mode == "SUSPENDED_HOLD":
                    target_str = "HOLD (거래정지)"
                    stop_str = "HOLD (거래정지)"
                    trail_str = "HOLD (비활성)"
                    sizing_str = f"보유 {sw.get('quantity', 0):,}주 / 권고: 0주 (상장적격성 실질심사 매매거래정지 [공시감시])"
                elif s_code == "348340" or sw.get("user_override_flag", False):
                    target_str = "24,450원 (수동활성)"
                    stop_str = "HOLD (주문대기)"
                    trail_str = "700원 (수동추적)"
                    sizing_str = f"보유 {sw.get('quantity', 0):,}주 | <b>수동주문: 31주 미체결</b> (DART미확정 신규주문금지)"
                elif s_mode == "HOLD" or sw.get("data_validity_flag", 1) == 0:
                    target_str = "HOLD (주문대기)"
                    stop_str = "HOLD (주문대기)"
                    trail_str = "HOLD (비활성)"
                    sizing_str = f"보유 {sw.get('quantity', 0):,}주 / 권고: 0주 (데이터 검증 대기)"
                elif s_mode == "CONCENTRATION_RISK":
                    target_str = f"{s_ttarget:,}원" if isinstance(s_ttarget, (int, float)) and s_ttarget > 0 else str(s_ttarget)
                    stop_str = f"{s_tstop:,}원" if isinstance(s_tstop, (int, float)) and s_tstop > 0 else str(s_tstop)
                    trail_str = f"최고가 대비 {s_trail_delta:,}원 하락 시"
                    w_ex = sw.get('weight_excess_qty', 0)
                    r_qty = sw.get('recommended_order_qty', 0)
                    sizing_str = f"위험목표 {sw.get('risk_target_qty', 0):,}주 | 20%초과 {w_ex:,}주 | <b>권고: {sw.get('order_direction', '매도')} ({r_qty:,}주)</b>"
                else:
                    target_str = f"{s_ttarget:,}원" if isinstance(s_ttarget, (int, float)) and s_ttarget > 0 else str(s_ttarget)
                    stop_str = f"{s_tstop:,}원" if isinstance(s_tstop, (int, float)) and s_tstop > 0 else str(s_tstop)
                    trail_str = f"최고가 대비 {s_trail_delta:,}원 하락 시"
                    sizing_str = f"위험목표 {sw.get('risk_target_qty', 0):,}주 | 현재 {sw.get('quantity', 0):,}주 | <b>권고: {sw.get('order_direction', '보유')} ({sw.get('recommended_order_qty', 0):,}주)</b>"
                
                meaningful_cards_html += f"""
                <div style="background:#FFFFFF; border-left:5px solid {s_color}; border-radius:8px; padding:10px 14px; margin-bottom:10px; box-shadow:0 2px 6px rgba(0,0,0,0.03);">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                        <strong style="font-size:13px; color:#0F172A;">{s_name} ({s_code}) / <span style="color:#2563EB;">{s_mode}</span> / {clean_act_display}</strong>
                        <span style="font-weight:bold; color:{s_color}; font-size:13px;">등락: {s_sign}{s_chg:.2f}%</span>
                    </div>
                    <div style="font-size:11.5px; color:#334155; background:#F8FAFC; padding:8px 10px; border-radius:6px; line-height:1.6;">
                        • <strong>목표/손절:</strong> <span style="color:#059669; font-weight:bold;">{target_str}</span> / <span style="color:#DC2626; font-weight:bold;">{stop_str}</span><br>
                        • <strong>트레일링 폭:</strong> {trail_str}<br>
                        • <strong>포지션 사이징:</strong> {sizing_str}
                    </div>
                </div>
                """

            swing_section_html = f'<div style="background:#FFFBEB; border:2px solid #F59E0B; border-radius:10px; padding:14px; margin-bottom:20px;"><h3>⚡ V4-PILOT-C 주요 대응 지침</h3>{meaningful_cards_html}</div>' if meaningful_items else ""

            held_section_html = f"""
            {swing_section_html}
            <div style="background:#FFFFFF; border:1px solid #CBD5E1; border-radius:10px; padding:14px; margin-bottom:20px; box-shadow:0 4px 12px rgba(0,0,0,0.03);">
                <h3 style="margin-top:0; color:#4338CA; font-size:14px; margin-bottom:8px;">⏱️ 5단계 매매 대응전략 매트릭스 및 일봉/45분봉 수급 원자값 연동 표</h3>
                <div style="font-size:11px; color:#64748B; margin-bottom:10px;">※ 매도대응 및 위험관리 종목 우선 배치 ➔ 종합점수 오름차순(낮은 순) 정렬</div>
                <table style="width:100%; border-collapse:collapse; font-size:11px;">
                    <thead>
                        <tr style="background:#F1F5F9; color:#334155; text-align:left; border-bottom:2px solid #CBD5E1;">
                            <th style="padding:6px 6px; background:#EFF6FF; color:#1E40AF; text-align:center;">대응 전략</th>
                            <th style="padding:6px 4px;">종목명 (코드)</th>
                            <th style="padding:6px 4px; text-align:center;">OBV (데드발생일자)</th>
                            <th style="padding:6px 4px; text-align:center;">45m OBV</th>
                            <th style="padding:6px 4px; text-align:center;">일봉 Chaikin 최근2봉</th>
                            <th style="padding:6px 4px; text-align:center;">45m Chaikin 최근2봉</th>
                            <th style="padding:6px 4px;">ADX +DI/-DI 우세방향</th>
                        </tr>
                    </thead>
                    <tbody>{summary_matrix_rows_html}</tbody>
                </table>
            </div>

            <div style="background:#FFFFFF; border:1px solid #CBD5E1; border-radius:10px; padding:14px; margin-bottom:20px; box-shadow:0 4px 12px rgba(0,0,0,0.03);">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; border-bottom:2px solid #E2E8F0; padding-bottom:8px;">
                    <div>
                        <h2 style="margin:0; font-size:15px; color:#0F172A;">💼 내 계좌 보유 종목 정밀 평가 ({held_count}종목)</h2>
                        <div style="font-size:11px; color:#64748B; margin-top:2px;">※ 매도대응 우선 정렬 / 계좌비중 20% 초과 종목 추매금지 및 14일 ATR 기반 트레일링 가격 산출 연동</div>
                    </div>
                    <div style="font-size:13px; font-weight:bold; color:{pnl_color_total}; text-align:right;">
                        총 평가손익: {pnl_sign_total}{total_pnl_pct:.2f}%<br><span style="font-size:11px;">({pnl_sign_total}{total_pnl_amt:,}원)</span>
                    </div>
                </div>
                <div class="table-responsive">
                <table style="width:100%; border-collapse:collapse;">
                    <thead>
                        <tr style="background:#F8FAFC; font-size:11px; color:#64748B; text-align:left; border-bottom:1px solid #E2E8F0;">
                            <th style="padding:6px 6px; background:#F0FDF4; color:#166534; text-align:center;">대응 전략</th>
                            <th style="padding:6px 4px;">종목명 (코드)</th>
                            <th style="padding:6px 4px; background:#EDE9FE; color:#5B21B6; text-align:center;">종합점수<br><span style="font-size:9px; font-weight:normal; color:#64748B;">(F / T점수)</span></th>
                            <th style="padding:6px 4px; text-align:center;">현재가(등락률)</th>
                            <th style="padding:6px 4px; color:#D97706; text-align:center;">14일 ATR</th>
                            <th style="padding:6px 4px; text-align:center;">평가손익(률)</th>
                        </tr>
                    </thead>
                    <tbody>
                        {held_rows_html}
                    </tbody>
                </table>
                </div>
            </div>

            <!-- 🔥 [글 박스] 보유 종목 최신 DART 주요 공시 & 브리핑 -->
            {disclosure_box_html}
            """
        else:
            held_section_html = f"""
            <div style="background:#FEF2F2; border:2px solid #EF4444; border-radius:12px; padding:18px; margin-bottom:24px; box-shadow:0 4px 12px rgba(239,68,68,0.1);">
                <div style="display:flex; align-items:center; margin-bottom:8px;">
                    <span style="font-size:22px; margin-right:10px;">⚠️</span>
                    <h3 style="margin:0; font-size:16px; color:#991B1B; font-weight:bold;">
                        [경고] 보유현황 미동기화 / 매매 판단 전면 보류
                    </h3>
                </div>
            </div>
            {disclosure_box_html}
            """

        html_template = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif; background-color: #F4F6F9; margin: 0; padding: 0; }}
                .container {{ max-width: 720px; margin: 0 auto; background: #FFFFFF; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 15px rgba(0,0,0,0.05); }}
                .table-responsive {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div style="background:linear-gradient(135deg, #1E1E2E 0%, #2A2D3E 100%); color:#FFFFFF; padding:18px 14px; text-align:left;">
                    <div style="font-size:11px; text-transform:uppercase; letter-spacing:1px; color:#A78BFA; font-weight:bold; margin-bottom:4px;">Kiwoom REST & DART Integrated Analysis</div>
                    <h1 style="margin:0; font-size:17px; font-weight:800; color:#FFFFFF;">📊 내 종목 정밀 평가 및 수치 종합점수 리포트</h1>
                    <div style="margin-top:4px; font-size:11px; color:#9CA3AF;">기준일시: {date_str}</div>
                </div>
                <div style="padding:10px;">
                    <div style="display:flex; justify-content:space-around; background:#F8FAFC; border:1px solid #E2E8F0; border-radius:12px; padding:14px; margin-bottom:20px; text-align:center;">
                        <div>
                            <div style="font-size:12px; color:#64748B; margin-bottom:4px;">내 계좌 보유 종목</div>
                            <div style="font-size:18px; font-weight:bold; color:#0F172A;">{held_count}개</div>
                        </div>
                        <div style="border-left:1px solid #CBD5E1; padding-left:20px;">
                            <div style="font-size:12px; color:#64748B; margin-bottom:4px;">수익 종목</div>
                            <div style="font-size:18px; font-weight:bold; color:#10B981;">{profit_count}개</div>
                        </div>
                        <div style="border-left:1px solid #CBD5E1; padding-left:20px;">
                            <div style="font-size:12px; color:#64748B; margin-bottom:4px;">손실 종목</div>
                            <div style="font-size:18px; font-weight:bold; color:#EF4444;">{loss_count}개</div>
                        </div>
                    </div>

                    {held_section_html}
                </div>
            </div>
        </body>
        </html>
        """
        return html_template
