import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, Any, List, Optional
from pathlib import Path
from config.settings import GMAIL_USER, GMAIL_APP_PASSWORD, RECIPIENT_EMAIL, LOG_DIR
from src.utils.logger import logger

class GmailNotifier:
    """
    지메일(Gmail) SMTP SSL/TLS를 통해 프리미엄 HTML 일일 주식 분석 보고서를 발송하는 알림 모듈입니다.
    """
    def __init__(self,
                 gmail_user: Optional[str] = GMAIL_USER,
                 gmail_password: Optional[str] = GMAIL_APP_PASSWORD,
                 recipient_email: Optional[str] = RECIPIENT_EMAIL):
        self.gmail_user = gmail_user
        self.gmail_password = gmail_password
        self.recipient_email = recipient_email or gmail_user

    def send_email(self, subject: str, html_content: str, attachments: Optional[List[Path]] = None) -> bool:
        """
        HTML 메일 및 엑셀 분석자료 첨부파일 발송 수행
        """
        try:
            report_path = LOG_DIR / "latest_email_report.html"
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(html_content)
            logger.info(f"[Gmail] 최신 리포트를 로컬 로그에 저장했습니다: {report_path}")
        except Exception as e_save:
            logger.error(f"[Gmail] 로컬 HTML 저장 실패: {e_save}")

        if not self.gmail_user or not self.gmail_password:
            logger.warning("[Gmail] GMAIL_USER 또는 GMAIL_APP_PASSWORD 설정이 없어 실제 이메일 발송을 건너뜁니다.")
            return False

        try:
            msg = MIMEMultipart()
            msg["From"] = self.gmail_user
            msg["To"] = self.recipient_email
            msg["Subject"] = subject

            # HTML 본문 추가
            html_part = MIMEText(html_content, "html", "utf-8")
            msg.attach(html_part)

            # 첨부 파일 추가 (엑셀 분석 자료 등)
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
                except Exception as e_smtp:
                    logger.warning(f"[Gmail] SMTP 발송 시도 {attempt}/{max_retries} 실패: {e_smtp}")
                    if attempt == max_retries:
                        raise e_smtp

            return False

        except Exception as e:
            logger.error(f"[Gmail] 이메일 발송 실패: {e}", exc_info=True)
            return False

    @staticmethod
    def generate_html_report(date_str: str,
                             total_count: int,
                             caught_signals: List[Dict[str, Any]],
                             all_results: List[Dict[str, Any]],
                             held_portfolio: Optional[List[Dict[str, Any]]] = None) -> str:
        """
        내 보유 종목 현황을 (F+T) 합산 종합점수 높은 순->낮은 순으로 순차 배열하고,
        하단 종목 우선 비중축소 배지를 시각적으로 강조한 프리미엄 HTML 리포트 생성
        """
        action_count = len(caught_signals)
        held_count = len(held_portfolio) if held_portfolio else 0

        # --- 1. 내 보유 종목 섹션 HTML ---
        held_section_html = ""
        if held_portfolio:
            held_rows_html = ""
            total_eval_inv = sum(h.get("total_invested", 0) for h in held_portfolio)
            total_eval_val = sum(h.get("eval_amount", 0) for h in held_portfolio)
            total_pnl_amt = total_eval_val - total_eval_inv
            total_pnl_pct = round((total_pnl_amt / total_eval_inv) * 100.0, 2) if total_eval_inv > 0 else 0.0
            pnl_color_total = "#10B981" if total_pnl_amt >= 0 else "#EF4444"
            pnl_sign_total = "+" if total_pnl_amt >= 0 else ""

            # 합산 종합점수 내림차순 정렬 보장
            held_portfolio_sorted = sorted(held_portfolio, key=lambda x: x.get("final_score", 0.0), reverse=True)
            profit_count = sum(1 for h in held_portfolio if h.get("pnl_pct", 0.0) >= 0)
            loss_count = sum(1 for h in held_portfolio if h.get("pnl_pct", 0.0) < 0)

            # 의미있는 주가변동(0.8 ATR 이상 변동 또는 2.5% 이상 변동 발생 종목) 선별
            meaningful_items = []
            for h in held_portfolio_sorted:
                daily_chg = h.get('daily_change_pct', 0.0)
                atr_pct = h.get('atr_pct', 3.0)
                if abs(daily_chg) >= max(2.5, atr_pct * 0.8):
                    meaningful_items.append(h)

            for rank_idx, h in enumerate(held_portfolio_sorted, start=1):
                code = h.get('stock_code')
                name = h.get('stock_name')
                qty = h.get('quantity')
                avg_p = h.get('avg_buy_price')
                cur_p = h.get('current_price')
                daily_chg = h.get('daily_change_pct', 0.0)
                pnl_pct = h.get('pnl_pct', 0.0)
                pnl_amt = h.get('pnl_amount', 0)
                f_sc = h.get('f_score', 0.0)
                t_sc = h.get('t_score', 0.0)
                final_sc = h.get('final_score', 0.0)
                action_st = h.get('action_status', '보유')

                pnl_color = "#10B981" if pnl_pct >= 0 else "#EF4444"
                pnl_sign = "+" if pnl_pct >= 0 else ""

                chg_color = "#10B981" if daily_chg >= 0 else "#EF4444"
                chg_sign = "+" if daily_chg >= 0 else ""

                # 순위: 깔끔한 아라비아 숫자 1, 2, 3...
                rank_badge = f"<span style='font-weight:bold; font-size:12px; color:#0F172A;'>{rank_idx}</span>"

                atr_v = h.get('atr_14', 0.0)
                is_etf = h.get('is_etf', False)
                f_confirmed = h.get('f_score_confirmed', True)

                # 한 칸 세 줄(종합점수 80.2점 / F: 100.0 / T: 85.0 - 점수 단위 제거 후 숫자만 간략 표기)
                if is_etf:
                    score_combined_cell = f"<strong style='font-size:12px; color:#6D28D9;'>{t_sc:.1f}점</strong><br><span style='font-size:9.5px; color:#0284C7;'>(ETF T점수)</span>"
                elif not f_confirmed:
                    score_combined_cell = f"<strong style='font-size:12px; color:#D97706;'>{final_sc:.1f}점</strong><br><span style='font-size:9.5px; color:#D97706;'>F: {f_sc:.1f}(잠정)</span><br><span style='font-size:9.5px; color:#1D4ED8;'>T: {t_sc:.1f}</span>"
                else:
                    score_combined_cell = f"<strong style='font-size:12px; color:#6D28D9;'>{final_sc:.1f}점</strong><br><span style='font-size:9.5px; color:#047857;'>F: {f_sc:.1f}</span><br><span style='font-size:9.5px; color:#1D4ED8;'>T: {t_sc:.1f}</span>"

                atr_display = f"{atr_v:,.0f}원"

                # 대응전략: 확정순위 제거, 핵심 전략 키워드 및 목:금액 / 트:금액 / 손:금액 간략 표기 (데이터 미확정 시 안전 보류)
                import re
                clean_strategy = re.sub(r'^(확정|잠정|ETF)\s*\d+위\s*', '', action_st).strip('()[] ')
                if not f_confirmed or "재무" in clean_strategy or "미확정" in clean_strategy:
                    badge_bg, badge_border, badge_color = "#FFFBEB", "#FCD34D", "#B45309"
                    action_kw = "⚠️ DART 재무미확정 (보류)"
                elif "비중과다" in clean_strategy:
                    badge_bg, badge_border, badge_color = "#FFFBEB", "#FCD34D", "#B45309"
                    action_kw = "비중과다 보유"
                elif "익절" in clean_strategy or "차익" in clean_strategy:
                    badge_bg, badge_border, badge_color = "#ECFDF5", "#6EE7B7", "#065F46"
                    action_kw = "익절/차익실현"
                elif "추매" in clean_strategy or "분할매수" in clean_strategy:
                    badge_bg, badge_border, badge_color = "#EFF6FF", "#93C5FD", "#1D4ED8"
                    action_kw = "분할매수"
                elif "분할매도" in clean_strategy or "비중축소" in clean_strategy:
                    badge_bg, badge_border, badge_color = "#FEF2F2", "#FCA5A5", "#991B1B"
                    action_kw = "분할매도"
                elif "손절" in clean_strategy:
                    badge_bg, badge_border, badge_color = "#FEF2F2", "#FCA5A5", "#991B1B"
                    action_kw = "손절"
                else:
                    badge_bg, badge_border, badge_color = "#F0F9FF", "#BAE6FD", "#0369A1"
                    action_kw = "안정 보유"

                t_target = h.get('kiwoom_target_tick_price', 0) or h.get('target_profit_price', 0)
                t_stop = h.get('confirmed_stop_price', 0) or h.get('kiwoom_stop_tick_price', 0)
                d_delta = h.get('drop_delta', int(atr_v * 0.8))
                r_delta = h.get('rebound_delta', int(atr_v * 0.5))

                if t_stop <= 0 or not f_confirmed or "보류" in action_kw:
                    target_str = "목: - (데이터보류)"
                    tr_text = "트: - (데이터보류)"
                    stop_str = "손: - (데이터보류)"
                else:
                    target_str = f"목: {t_target:,}원"
                    if "매수" in action_kw:
                        tr_text = f"트: +{r_delta:,}원"
                    else:
                        tr_text = f"트: -{d_delta:,}원"
                    stop_str = f"손: {t_stop:,}원"

                strategy_cell_html = f"""
                <span style="background:{badge_bg}; border:1px solid {badge_border}; color:{badge_color}; padding:2px 5px; border-radius:5px; font-weight:bold; font-size:9.5px; display:inline-block; margin-bottom:2px;">{action_kw}</span><br>
                <span style="font-size:9px; color:#1D4ED8; font-weight:bold;">{target_str}</span><br>
                <span style="font-size:9px; color:#D97706;">{tr_text}</span><br>
                <span style="font-size:9px; color:#DC2626;">{stop_str}</span>
                """

                # 9자 초과 긴 종목명 단어 내부 임의 공백 분단 오타 방지 줄바꿈 처리
                if len(name) > 9 and " " in name:
                    parts = name.split(" ", 1)
                    name_formatted = f"{parts[0]}<br>{parts[1]}"
                else:
                    name_formatted = name

                held_rows_html += f"""
                <tr style="border-bottom:1px solid #E2E8F0; font-size:11px;">
                    <td style="padding:6px 3px; text-align:center;">{rank_badge}</td>
                    <td style="padding:6px 3px; font-weight:bold; color:#0F172A; max-width:85px; word-break:break-word; line-height:1.25;">{name_formatted}<br><span style="font-size:9.5px; color:#64748B; font-weight:normal;">({code})</span></td>
                    <td style="padding:6px 3px; text-align:center; background:#F5F3FF;">{score_combined_cell}</td>
                    <td style="padding:6px 3px; font-weight:bold;">{cur_p:,}원<br><span style="font-size:9.5px; color:{chg_color};">({chg_sign}{daily_chg:.2f}%)</span></td>
                    <td style="padding:6px 3px; color:#475569; font-weight:bold;">{atr_display}</td>
                    <td style="padding:6px 3px; font-weight:bold; color:{pnl_color};">{pnl_sign}{pnl_pct:.2f}%<br><span style="font-size:9.5px; font-weight:normal;">({pnl_sign}{pnl_amt:,}원)</span></td>
                    <td style="padding:6px 3px;">{strategy_cell_html}</td>
                </tr>
                """

            # 의미있는 변동 종목 구체적 대응 섹션 구축 (ATR 트레일링 기준 반영)
            meaningful_cards_html = ""
            if meaningful_items:
                for sw in meaningful_items:
                    s_name = sw.get('stock_name')
                    s_code = sw.get('stock_code')
                    s_chg = sw.get('daily_change_pct', 0.0)
                    s_f = sw.get('f_score', 0.0)
                    s_t = sw.get('t_score', 0.0)
                    s_act = sw.get('action_status', '보유')
                    s_atr = sw.get('atr_14', 0.0)
                    s_tbuy = sw.get('trailing_buy_price', 0) or sw.get('kiwoom_buy_tick_price', 0)
                    s_tstop = sw.get('confirmed_stop_price', 0) or sw.get('trailing_stop_price', 0) or sw.get('kiwoom_stop_tick_price', 0)
                    s_ttarget = sw.get('target_profit_price', 0) or sw.get('trailing_target_price', 0) or sw.get('kiwoom_target_tick_price', 0)
                    s_rdelta = sw.get('rebound_delta', int(s_atr * 0.5))
                    s_ddelta = sw.get('drop_delta', int(s_atr * 0.8))
                    s_buy_trigger = s_tbuy + s_rdelta if s_tbuy > 0 else 0
                    s_sell_trigger = s_ttarget - s_ddelta if s_ttarget > 0 else 0
                    s_color = "#10B981" if s_chg >= 0 else "#EF4444"
                    s_sign = "+" if s_chg >= 0 else ""

                    if "비중과다" in s_act or "추매금지" in s_act:
                        advice_detail = f"당일 {s_sign}{s_chg:.2f}% 변동. 단일 비중 20% 초과 집중 위험으로 추매가 금지됩니다. (2.5 ATR 목표가 <strong>{s_ttarget:,}원</strong> 도달 후 최고가 대비 -{s_ddelta:,}원 하락 시 1차 50% 차익실현 | 2.0 ATR 손절가 <strong>{s_tstop:,}원</strong> 하향 이탈 시 100% 손절)"
                    elif "추매" in s_act and s_tbuy > 0:
                        advice_detail = f"당일 {s_sign}{s_chg:.2f}% 조정. 1.5 ATR 눌림목 감시가 <strong>{s_tbuy:,}원</strong> 도달 후 최저가 대비 +{s_rdelta:,}원 반등하여 <strong>{s_buy_trigger:,}원</strong> 도달 시 1차 50% 분할추매 고려 (손절가 <strong>{s_tstop:,}원</strong>)"
                    elif ("매도" in s_act or "반등" in s_act or "익절" in s_act) and s_ttarget > 0:
                        advice_detail = f"기술적 반등/익절 진행 중. 2.5 ATR 목표가 <strong>{s_ttarget:,}원</strong> 도달 후 최고가 대비 -{s_ddelta:,}원 하락하여 <strong>{s_sell_trigger:,}원</strong> 도달 시 1차 50% 분할매도/차익실현."
                    elif "손절" in s_act and s_tstop > 0:
                        advice_detail = f"기술 추세 붕괴 위험. 2.0 ATR 손절가 <strong>{s_tstop:,}원</strong> 이하 하향 이탈 시 손절선 재설정 없이 100% 전량 손절 실행."
                    else:
                        advice_detail = f"당일 {s_sign}{s_chg:.2f}% 변동. 2.5 ATR 목표가 <strong>{s_ttarget:,}원</strong> (최고가 대비 -{s_ddelta:,}원 하락 시 50% 차익실현) 및 2.0 ATR 손절가 <strong>{s_tstop:,}원</strong> (하향 이탈 시 100% 손절) 감시 유지."

                    meaningful_cards_html += f"""
                    <div style="background:#FFFFFF; border-left:5px solid {s_color}; border-radius:8px; padding:10px 14px; margin-bottom:10px; box-shadow:0 2px 6px rgba(0,0,0,0.03);">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <strong style="font-size:13px; color:#0F172A;">{s_name} ({s_code})</strong>
                            <span style="font-weight:bold; color:{s_color}; font-size:13px;">당일 등락: {s_sign}{s_chg:.2f}% | 14일 ATR: {s_atr:,.0f}원</span>
                        </div>
                        <div style="font-size:11px; color:#334155; margin-top:6px; background:#F8FAFC; padding:8px; border-radius:6px; line-height:1.5;">
                            🎯 <strong>키움 트레일링 매매 가격 가이드:</strong> {advice_detail}
                        </div>
                    </div>
                    """
            else:
                meaningful_cards_html = """
                <div style="font-size:12px; color:#64748B; background:#F8FAFC; padding:10px; border-radius:8px; text-align:center;">
                    당일 거래량 폭증, ATR 이상 변동 등 의미있는 급변동이 발생한 보유 종목이 없습니다.
                </div>
                """

            swing_section_html = f"""
            <div style="background:#FFFBEB; border:2px solid #F59E0B; border-radius:10px; padding:14px; margin-bottom:20px; box-shadow:0 4px 12px rgba(245,158,11,0.08);">
                <h3 style="margin-top:0; color:#B45309; font-size:15px; margin-bottom:8px;">
                    ⚡ 의미있는 주가변동 포착 종목 ATR 트레일링 대응 지침 (총 {len(meaningful_items)}종목)
                </h3>
                {meaningful_cards_html}
            </div>
            """

            held_section_html = f"""
            {swing_section_html}
            <div style="background:#FFFFFF; border:1px solid #CBD5E1; border-radius:10px; padding:14px; margin-bottom:20px; box-shadow:0 4px 12px rgba(0,0,0,0.03);">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; border-bottom:2px solid #E2E8F0; padding-bottom:8px;">
                    <div>
                        <h2 style="margin:0; font-size:15px; color:#0F172A;">💼 내 계좌 보유 종목 정밀 평가 ({held_count}종목)</h2>
                        <div style="font-size:11px; color:#64748B; margin-top:2px;">※ 계좌비중 20% 초과 종목 추매금지 및 14일 ATR 기반 트레일링 가격 산출 연동</div>
                    </div>
                    <div style="font-size:13px; font-weight:bold; color:{pnl_color_total}; text-align:right;">
                        총 평가손익: {pnl_sign_total}{total_pnl_pct:.2f}%<br><span style="font-size:11px;">({pnl_sign_total}{total_pnl_amt:,}원)</span>
                    </div>
                </div>
                <div class="table-responsive">
                <table style="width:100%; border-collapse:collapse;">
                    <thead>
                        <tr style="background:#F8FAFC; font-size:11px; color:#64748B; text-align:left; border-bottom:1px solid #E2E8F0;">
                            <th style="padding:6px 3px; text-align:center;">순위</th>
                            <th style="padding:6px 3px;">종목명</th>
                            <th style="padding:6px 3px; background:#EDE9FE; color:#5B21B6; text-align:center;">종합점수<br><span style="font-size:9px; font-weight:normal; color:#64748B;">(F / T점수)</span></th>
                            <th style="padding:6px 3px;">현재가(등락률)</th>
                            <th style="padding:6px 3px; color:#D97706;">14일 ATR</th>
                            <th style="padding:6px 3px;">평가손익(률)</th>
                            <th style="padding:6px 3px;">대응 전략</th>
                        </tr>
                    </thead>
                    <tbody>
                        {held_rows_html}
                    </tbody>
                </table>
                </div>
                <div style="font-size:11px; color:#475569; background:#F8FAFC; border:1px solid #E2E8F0; padding:10px 12px; border-radius:8px; margin-top:12px; line-height:1.5;">
                    💡 <strong>변동성 캡 적용 동적 ATR 트레일링 모델 (손익비 2.0:1 보장):</strong><br>
                    • <strong>정상 / 수익 종목 (손실률 -25% 초과 & T점수 50이상):</strong> 손익비 2.0:1 보장을 위해 목표가 <code>+3.0 ATR (최대 +35% 캡)</code>, 트레일링 <code>-0.8 ATR</code>, 손절가 <code>-1.5 ATR (최소 -6%, 최대 -15% 캡)</code> 적용<br>
                    • <strong>대형 손실 / 기술 약세 종목 (손실률 -25% 이하 OR T점수 50미만):</strong> 노이즈 털림 방지를 위해 손절가 <code>-1.0 ATR 최소거리 보장 (최대 -15% 캡)</code>, 목표가 <code>+1.5 ATR (최대 +20% 캡)</code>, 트레일링 <code>-0.5 ATR</code> 적용
                </div>
            </div>
            """
        else:
            held_section_html = """
            <div style="background:#FEF2F2; border:2px solid #EF4444; border-radius:12px; padding:18px; margin-bottom:24px; box-shadow:0 4px 12px rgba(239,68,68,0.1);">
                <div style="display:flex; align-items:center; margin-bottom:8px;">
                    <span style="font-size:22px; margin-right:10px;">⚠️</span>
                    <h3 style="margin:0; font-size:16px; color:#991B1B; font-weight:bold;">
                        [경고] 보유현황 미동기화 / 매매 판단 전면 보류
                    </h3>
                </div>
                <div style="font-size:13px; color:#7F1D1D; line-height:1.6; background:#FFFFFF; padding:12px; border-radius:8px; border:1px solid #FCA5A5;">
                    • <strong>키움 계좌 보유종목 수집 결과가 0개로 표시되었습니다.</strong> (키움 OpenAPI 미접속 또는 로그인 미연동 상태)<br>
                    • <strong>[안전 수칙 적용]</strong> 실제 계좌 보유 현황이 동기화되지 않은 상태에서는 매도·추매·비중조절 매매 판단이 왜곡될 수 있으므로 <strong>보유종목 매매 조언이 전면 보류(중지)</strong>됩니다.<br>
                    • 아래 표출된 종목 신호(하이록코리아, 두산에너빌리티, 유바이오로직스, HD현대일렉트릭 등)는 보유종목 분석이 아닌 <strong>'관심 종목 신규매수 후보 신호'로만 구별 취급</strong>하십시오.
                </div>
            </div>
            """

        # --- 2. 신규 포착 신호 카드가 구성되는 부분 ---
        signal_cards_html = ""
        if caught_signals:
            for sig in caught_signals:
                code = sig.get('stock_code', '000000')
                name = sig.get('stock_name', '미상')
                sig_type = sig.get('signal_type', '신호')
                rec_amount = sig.get('recommended_amount', '0만 원')
                f_score = sig.get('f_score', 0.0)
                t_raw = sig.get('t_score_raw', 0.0)
                t_conv = sig.get('t_score_converted', 0.0)
                final_score = sig.get('final_score', 0.0)
                completeness = sig.get('data_completeness', 0.0)
                close_p = sig.get('latest_close', 0)
                stop_p = sig.get('stop_loss_price', 0)
                loss_pct = sig.get('expected_loss_pct', 0.0)
                reason = sig.get('reason', '사유 미기재')

                badge_color = "#10B981" if "1차" in sig_type else ("#3B82F6" if "추매" in sig_type or "불타기" in sig_type else "#EF4444")
                t_raw_sign = "+" if t_raw > 0 else ""

                signal_cards_html += f"""
                <div style="background:#ffffff; border-radius:12px; padding:20px; margin-bottom:20px; border-left:6px solid {badge_color}; box-shadow:0 4px 12px rgba(0,0,0,0.05);">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                        <h3 style="margin:0; font-size:20px; color:#111827;">{name} <span style="font-size:14px; color:#6B7280;">({code})</span></h3>
                        <span style="background:{badge_color}; color:#ffffff; font-weight:bold; padding:6px 14px; border-radius:20px; font-size:14px;">{sig_type} ({rec_amount})</span>
                    </div>
                    <table style="width:100%; border-collapse:collapse; margin-bottom:12px; font-size:14px; color:#374151;">
                        <tr style="background:#F9FAFB;">
                            <th style="padding:8px 12px; text-align:left; border-bottom:1px solid #E5E7EB;">종가</th>
                            <th style="padding:8px 12px; text-align:left; border-bottom:1px solid #E5E7EB; color:#059669;">F점수 (100만점)</th>
                            <th style="padding:8px 12px; text-align:left; border-bottom:1px solid #E5E7EB; color:#2563EB;">T점수 (100만점)</th>
                            <th style="padding:8px 12px; text-align:left; border-bottom:1px solid #E5E7EB; color:#7C3AED;">종합점수 (100만점)</th>
                            <th style="padding:8px 12px; text-align:left; border-bottom:1px solid #E5E7EB;">손절가(손절폭)</th>
                        </tr>
                        <tr>
                            <td style="padding:10px 12px; font-weight:bold; color:#111827;">{close_p:,}원</td>
                            <td style="padding:10px 12px; color:#059669; font-weight:bold;">{f_score:.1f}점</td>
                            <td style="padding:10px 12px; color:#2563EB; font-weight:bold;">{t_conv:.1f}점</td>
                            <td style="padding:10px 12px; font-weight:bold; color:#7C3AED; font-size:16px;">{final_score:.1f}점</td>
                            <td style="padding:10px 12px; color:#DC2626; font-weight:bold;">{stop_p:,}원 ({loss_pct:.1f}%)</td>
                        </tr>
                    </table>
                    <div style="background:#F3F4F6; border-radius:8px; padding:12px; font-size:13px; color:#4B5563;">
                        <strong>💡 분석 근거:</strong> {reason} (완성도: {completeness:.0f}%)
                    </div>
                </div>
                """
        else:
            signal_cards_html = """
            <div style="background:#ffffff; border-radius:12px; padding:20px; text-align:center; color:#6B7280; font-size:14px; border:1px dashed #D1D5DB;">
                오늘 신규 매수/매도 조건에 포착된 관심 종목이 없습니다.
            </div>
            """

        # 전체 종목 요약 테이블
        summary_rows_html = ""
        for r in all_results:
            name = r.get('stock_name', '미상')
            code = r.get('stock_code', '000000')
            stype = r.get('signal_type', '관망')
            f_sc = r.get('f_score', 0.0)
            t_sc = r.get('t_score_converted', 0.0)
            fin_sc = r.get('final_score', 0.0)
            bg = "#ECFDF5" if "매수" in stype else "#FFFFFF"
            
            summary_rows_html += f"""
            <tr style="background:{bg}; border-bottom:1px solid #F3F4F6; font-size:13px;">
                <td style="padding:8px 12px; font-weight:bold;">{name} ({code})</td>
                <td style="padding:8px 12px;">{stype}</td>
                <td style="padding:8px 12px; color:#059669; font-weight:bold;">{f_sc:.1f}점</td>
                <td style="padding:8px 12px; color:#2563EB; font-weight:bold;">{t_sc:.1f}점</td>
                <td style="padding:8px 12px; font-weight:bold; color:#7C3AED;">{fin_sc:.1f}점</td>
            </tr>
            """

        summary_table_html = f"""
        <div style="background:#FFFFFF; border:1px solid #E5E7EB; border-radius:12px; padding:20px; margin-top:20px;">
            <h3 style="margin-top:0; font-size:16px; color:#111827; margin-bottom:12px;">📋 전체 분석 대상 종목 점수 요약 (모두 100점 만점 기준)</h3>
            <table style="width:100%; border-collapse:collapse;">
                <thead>
                    <tr style="background:#F9FAFB; font-size:12px; color:#6B7280; text-align:left;">
                        <th style="padding:8px 12px;">종목명(코드)</th>
                        <th style="padding:8px 12px;">매매신호</th>
                        <th style="padding:8px 12px; color:#059669;">기본 F점수 (100만점)</th>
                        <th style="padding:8px 12px; color:#2563EB;">기술 T점수 (100만점)</th>
                        <th style="padding:8px 12px; color:#7C3AED;">종합점수 (100만점)</th>
                    </tr>
                </thead>
                <tbody>
                    {summary_rows_html}
                </tbody>
            </table>
        </div>
        """

        # 메일 본문 삽입용 기계 읽기 가능 1차 기준 [RAW MONITORING DATA CSV] 구축
        csv_lines = ["종목코드,종목명,보유수량,평균단가,현재가,계좌비중(%),F점수,T점수,종합점수,14일ATR,손절가,익절가,추매가,매매대응전략"]
        if held_portfolio:
            for h in held_portfolio:
                code = h.get('stock_code', '')
                name = h.get('stock_name', '')
                qty = h.get('quantity', 0)
                avg_p = h.get('avg_buy_price', 0.0)
                cur_p = h.get('current_price', 0)
                w_pct = h.get('eval_weight_pct', 0.0)
                f_sc = h.get('f_score', 0.0)
                t_sc = h.get('t_score', 0.0)
                final_sc = h.get('final_score', 0.0)
                atr_v = h.get('atr_14', 0.0)
                stop_p = h.get('confirmed_stop_price', 0)
                target_p = h.get('trailing_target_price', 0)
                buy_p = h.get('trailing_buy_price', 0)
                act_st = h.get('action_status', '보유')
                f_conf = h.get('f_score_confirmed', True)

                # 자동매매 오발동 완벽 차단: 재무미확정/보류 종목은 CSV에 수치 대신 'HOLD' 문자열을 강제 배치
                if not f_conf or stop_p == 0 or "보류" in str(act_st) or "미확정" in str(act_st):
                    stop_str = "HOLD"
                    target_str = "HOLD"
                    buy_str = "HOLD"
                else:
                    stop_str = f"{int(stop_p)}"
                    target_str = f"{int(target_p)}"
                    buy_str = f"{int(buy_p)}"

                csv_lines.append(f"{code},{name},{qty},{avg_p:.1f},{cur_p},{w_pct:.1f},{f_sc:.1f},{t_sc:.1f},{final_sc:.1f},{atr_v:.1f},{stop_str},{target_str},{buy_str},{act_st}")
        raw_csv_block_text = "\n".join(csv_lines)

        html_template = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
            <style>
                body {{ margin:0; padding:0; background-color:#F3F4F6; font-family:'Apple SD Gothic Neo', 'Malgun Gothic', Arial, sans-serif; -webkit-text-size-adjust:100%; }}
                .container {{ max-width:600px; width:100%; margin:10px auto; background:#FFFFFF; border-radius:12px; overflow:hidden; box-shadow:0 4px 16px rgba(0,0,0,0.06); }}
                .table-responsive {{ width:100%; overflow-x:auto; -webkit-overflow-scrolling:touch; }}
                table {{ width:100%; border-collapse:collapse; font-size:11px; }}
                th, td {{ padding:6px 4px; text-align:left; }}
            </style>
        </head>
        <body style="margin:0; padding:4px; background-color:#F3F4F6;">
            <div class="container">
                <!-- Header -->
                <div style="background:linear-gradient(135deg, #1E1E2E 0%, #2A2D3E 100%); color:#FFFFFF; padding:18px 14px; text-align:left;">
                    <div style="font-size:11px; text-transform:uppercase; letter-spacing:1px; color:#A78BFA; font-weight:bold; margin-bottom:4px;">Kiwoom REST & DART Integrated Analysis</div>
                    <h1 style="margin:0; font-size:17px; font-weight:800; color:#FFFFFF;">📊 내 종목 정밀 평가 및 수치 종합점수 리포트</h1>
                    <div style="margin-top:4px; font-size:11px; color:#9CA3AF;">기준일시: {date_str}</div>
                </div>

                <!-- Content Body -->
                <div style="padding:10px;">
                    <!-- Overview Summary Box -->
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

                    <!-- 1. Held Portfolio Section -->
                    {held_section_html}

                    <!-- 4. Raw CSV Data Section (Primary Audit Data for Automated API Inspection) -->
                    <div style="margin-top:32px; background:#0F172A; color:#E2E8F0; padding:16px; border-radius:10px; font-family:Consolas, monospace; font-size:10px; line-height:1.5; overflow-x:auto;">
                        <div style="color:#38BDF8; font-weight:bold; font-size:11px; margin-bottom:6px;">📋 [RAW DATA CSV - 메일 본문 감시용 1차 기준 데이터]</div>
                        <div style="color:#94A3B8; font-size:9.5px; margin-bottom:10px;">※ 첨부파일 다운로드 실패 시에도 이 본문 CSV 데이터를 1차 감시 기준 자료로 100% 자동 사용합니다.</div>
                        <pre style="margin:0; font-family:monospace; font-size:10px; color:#F1F5F9;">{raw_csv_block_text}</pre>
                    </div>
                </div>

                <!-- Footer -->
                <div style="background:#F8FAFC; padding:16px; text-align:center; font-size:12px; color:#94A3B8; border-top:1px solid #E2E8F0;">
                    키움 REST API & DART API 연동 스윙 투자 자동 분석 및 보유 포트폴리오 관리 엔진
                </div>
            </div>
        </body>
        </html>
        """
        return html_template
