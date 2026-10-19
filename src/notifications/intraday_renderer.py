"""Table/inline-CSS intraday mail. All strategy calculations remain upstream."""

import html
import re

from src.notifications.intraday_data import kst_time, number, valuation
from src.notifications.mobile_renderer_v2 import (
    build_today_action_summary_html, get_latest_add_advisory_entry, sanitize_url,
)

EVENT_MEANINGS = {
    "DOWN_EARLY": "하락 조짐", "DOWN_CONFIRMED": "하락 확인",
    "BUY_SETUP": "매수 조짐", "BUY_CONFIRMED": "매수 확인",
    "SIGNAL_EARLY_UP": "상승 조짐", "SIGNAL_CONF_UP": "상승 확인",
    "SIGNAL_EARLY_DOWN": "하락 조짐", "SIGNAL_CONF_DOWN": "하락 확인",
    "SELL_PREP_WARNING": "매도 준비 경고", "OBV15_WARNING": "OBV 수급 경고",
    "STOP_BREACH": "손절선 이탈", "STOP_BREACH_INITIAL": "초기 손절선 이탈",
    "ATR_DOWN_1_0": "1 ATR 하락", "ATR_UP_1_0": "1 ATR 상승",
    "ATR_UP_0_5": "0.5 ATR 상승", "ATR_DOWN_0_5": "0.5 ATR 하락",
    "BASE_PAIR_DOWN": "가격·수급 하락 전환", "BASE_PAIR_UP": "가격·수급 상승 전환",
    "STOP_RECOVER_CONFIRMED": "손절선 회복 확인",
    "DATA_SUSPENDED": "거래정지", "DATA_STALE": "시세 지연",
    "DATA_RECOVERED": "데이터 회복", "DATA_DATA_REVIEW": "데이터 검토",
    "OBV_TURN_DOWN": "OBV 하락 전환", "OBV_GOLD_CROSS": "OBV 골든크로스",
}


def esc(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def plain(value):
    return esc(re.sub(r"<[^>]*>", "", html.unescape(str(value or ""))))


def money(value, signed=False):
    value = number(value)
    if value is None:
        return "자료 없음"
    # Preserve source precision; ATR values are already rounded by the engine.
    raw = format(value, ",f").rstrip("0").rstrip(".") if value % 1 else format(value, ",.0f")
    return ("+" if signed and value > 0 else "") + raw + "원"


def status(item):
    action = str(item.get("action_status") or "")
    mode = item.get("trade_mode")
    if item.get("is_suspended") or mode == "SUSPENDED_HOLD" or "거래정지" in action:
        return "거래정지", "#CBD5E1"
    if item.get("strategy_status") == "REVIEW_REQUIRED" or "재검토" in action:
        return "전략 재검토", "#FBBF24"
    if item.get("is_stop_breached") or "침범" in action:
        return "손절선 이탈", "#FDA4AF"
    if mode in ("RECOVERY", "EMERGENCY", "HOLD", "CONCENTRATION_RISK", "USER_OVERRIDE") or any(t in action for t in ("매도", "손절", "주의", "보류", "축소")):
        return "주의", "#FBBF24"
    return "정상", "#6EE7B7"


def signal_html(code, alerts, cutoff):
    if not alerts or alerts.get("status") != "AVAILABLE":
        return "최근 알림 조회 불가"
    event = alerts.get("by_code", {}).get(code)
    if not event:
        return "오늘 신규 신호 없음"
    stamp = kst_time(event["occurred_at"])
    if event.get("source") != "ALERT_LOG" or stamp.date() != cutoff.date() or stamp > cutoff:
        raise ValueError("Invalid actual-alert provenance or cutoff")
    keys = event["event_key"].split("|")
    # One actual ALERT_LOG row can contain several simultaneous event codes.
    meanings = [EVENT_MEANINGS.get(k, "") for k in keys]
    meaning = " · ".join(m for m in meanings if m)
    if not meaning:
        meaning = str(event.get("message") or "실제 알림 기록").split(" | ")[0][:100]
    return f'<span style="color:#CBD5E1">{stamp:%H:%M}</span> {esc(meaning)}<br><span style="font-size:11px;color:#94A3B8;word-break:break-all">{esc(event["event_key"])}</span>'


def box(label, value, color="#F1F5F9"):
    return f'<td width="50%" valign="top" bgcolor="#17243B" style="width:50%;padding:10px 8px;border:3px solid #101D32;border-radius:8px;overflow-wrap:anywhere;word-break:break-word"><div style="font-size:11px;color:#A9B8CE;margin-bottom:5px">{label}</div><div style="font-size:16px;font-weight:bold;color:{color}">{value}</div></td>'


def card(item, alerts, cutoff):
    code = str(item["stock_code"]).zfill(6)
    label, accent = status(item)
    price, avg, amount, pct = valuation(item)
    pnl_color = "#FDA4AF" if amount is not None and amount > 0 else "#93C5FD" if amount is not None and amount < 0 else "#F1F5F9"
    percentage = "자료 없음" if pct is None else f"{pct:+.2f}%" if pct else "0.00%"
    suspended = label == "거래정지"
    buy = None if suspended else item.get("buy_trailing_dist")
    sell = None if suspended else item.get("sell_trailing_dist")
    return f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#101D32" data-holding-code="{esc(code)}" style="width:100%;table-layout:fixed;border:1px solid #30425E;border-top:3px solid {accent};border-radius:12px;margin-bottom:12px"><tr><td style="padding:15px 12px;word-break:break-word;overflow-wrap:anywhere">
<div style="font-size:18px;font-weight:bold;color:#F8FAFC;line-height:1.45">{esc(item.get('stock_name') or code)}</div>
<div style="font-size:12px;color:#A9B8CE;margin:4px 0 12px">{esc(code)} &nbsp; <span style="color:{accent}">● {label}</span></div>
<div style="font-size:11px;color:#A9B8CE">현재가{' · 거래정지 전 가격' if suspended else ''}</div><div style="font-size:28px;font-weight:bold;color:#FFFFFF;line-height:1.5;margin-bottom:10px">{money(price)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;table-layout:fixed"><tr>{box('평균 매입단가', money(avg))}{box('평가손익금 · 손익률',money(amount, True)+'<br><span style="font-size:14px">'+percentage+'</span>',pnl_color)}</tr>
<tr>{box('0.6 ATR · 추적매수 반등폭',money(buy))}{box('0.4 ATR · 최고가 하락폭',money(sell))}</tr></table>
<div style="border-top:1px solid #30425E;margin-top:10px;padding-top:10px;font-size:13px;line-height:1.6;color:#E2E8F0"><span style="font-size:11px;color:#A9B8CE">최근 실제 알림</span><br>{signal_html(code,alerts,cutoff)}</div></td></tr></table>'''


def disclosures_html(disclosures):
    groups, seen = {}, set()
    for item in disclosures or []:
        receipt = str(item.get("rcept_no") or "").strip()
        if not receipt:
            raise ValueError("DART receipt number missing")
        if receipt in seen:
            continue
        seen.add(receipt)
        title = str(item.get("report_nm") or "")
        # Group only explicit original receipt IDs or exact titles after correction prefix.
        base = re.sub(r"^\s*\[(?:기재정정|첨부정정|정정|발행조건확정)\]\s*", "", title)
        group = (str(item.get("stock_code")).zfill(6), item.get("original_rcept_no") or base)
        groups.setdefault(group, []).append(item)
    result = '<h2 style="font-size:17px;color:#F8FAFC">관련 DART 주요 공시</h2>'
    if not groups:
        return result + '<p style="color:#CBD5E1;font-size:13px">오늘 확인된 주요 신규 공시 없음</p>'
    for items in groups.values():
        result += '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="table-layout:fixed;border:1px solid #30425E;margin-bottom:10px"><tr><td style="padding:12px;font-size:13px;line-height:1.65;word-break:break-word;overflow-wrap:anywhere">'
        for item in items:
            corrected = bool(re.search(r"\[.*정정.*\]", str(item.get("report_nm") or "")))
            result += f'<div data-disclosure-id="{esc(item["rcept_no"])}" data-disclosure-code="{esc(str(item.get("stock_code")).zfill(6))}" style="margin-bottom:10px"><b>{esc(item.get("stock_name"))}</b> · {"정정공시" if corrected else "원공시"}<br><a href="{sanitize_url(item.get("link"))}" style="color:#93C5FD">{esc(item.get("report_nm"))}</a><br><b>공시 내용(원천 요약)</b>: {plain(item.get("summary"))}'
            if item.get("impact"):
                result += f'<br><span style="color:#A9B8CE">투자 영향 해석: {plain(item["impact"])}</span>'
            if item.get("guide"):
                result += f'<br><span style="color:#A9B8CE">참고 판단: {plain(item["guide"])}</span>'
            result += '</div>'
        result += '</td></tr></table>'
    return result


def action_html(held, policy, cutoff, db_manager):
    safe = [dict(h, stock_name=esc(h.get("stock_name") or h["stock_code"])) for h in held]
    safe_policy = dict(policy or {}, rows=[{k: esc(v) if isinstance(v, str) else v for k,v in p.items()} for p in (policy or {}).get("rows", [])])
    summary = build_today_action_summary_html(safe, safe_policy)
    palette = {"#F8FAFC":"#17243B", "#CBD5E1":"#30425E", "#0F172A":"#F8FAFC", "#1E293B":"#E2E8F0"}
    summary = re.sub(r"#[0-9A-Fa-f]{6}", lambda m: palette.get(m[0], m[0]), summary)
    add = []
    meanings = {"ADD_STRONG":"추가매수 검토", "ADD_WATCH":"관찰", "ADD_BLOCKED":"추가매수 금지", "NEUTRAL":"추가매수 보류"}
    for h in held:
        entry = get_latest_add_advisory_entry(str(h["stock_code"]).zfill(6), target_date=cutoff.strftime("%Y-%m-%d"), max_bar_timestamp=cutoff.strftime("%Y-%m-%d %H:%M:%S"), db_manager=db_manager)
        state = str(entry.get("add_advisory_state") or "UNKNOWN")
        meaning = meanings.get(state, "당일 완성봉 대기" if entry.get("data_quality") == "WAITING_TODAY_BAR" else "데이터 확인")
        add.append(f'<div style="margin:5px 0">{esc(h.get("stock_name") or h["stock_code"])}: {esc(state)} · {meaning}</div>')
    # The existing decision content is preserved; the 45m summary stays in the same final section.
    return '<section data-section="actions">'+summary+'<div style="font-size:12px;line-height:1.6;color:#CBD5E1"><b>45M ADD ADVISORY · 완성 45분봉 판단</b>'+''.join(add)+'</div></section>'


def generate_intraday_html_report(date_str, held_portfolio, disclosures=None, policy_display=None, recent_alerts=None, db_manager=None):
    cutoff = kst_time(date_str)
    held = held_portfolio or []
    codes = [str(h["stock_code"]).zfill(6) for h in held]
    if len(codes) != len(set(codes)):
        raise ValueError("Duplicate intraday holdings")
    cards = ''.join(card(h, recent_alerts, cutoff) for h in held)
    dart = disclosures_html(disclosures)
    actions = action_html(held, policy_display, cutoff, db_manager)
    return f'''<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="dark"><meta name="supported-color-schemes" content="dark"><title>보유종목 장중 모니터링</title></head>
<body bgcolor="#081326" style="margin:0;padding:0;background-color:#081326;color:#E2E8F0;font-family:Arial,'Malgun Gothic',sans-serif;-webkit-text-size-adjust:100%"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#081326" style="width:100%;table-layout:fixed"><tr><td align="center" style="padding:16px 8px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" data-render-version="V2" data-intraday-layout="cards" data-held-stock-codes="{esc(','.join(sorted(codes)))}" style="width:100%;max-width:600px;table-layout:fixed"><tr><td style="padding:8px 6px 18px"><div style="color:#93C5FD;font-size:11px;letter-spacing:1px">STOCKBOT V4-PILOT-C</div><h1 style="font-size:22px;color:#FFFFFF;line-height:1.4;margin:8px 0">보유종목 장중 모니터링</h1><div style="font-size:13px;color:#A9B8CE">{cutoff:%Y-%m-%d %H:%M} KST 기준 · 보유 {len(held)}종목</div></td></tr>
<tr><td data-section="holdings"><div style="font-size:12px;color:#A9B8CE;margin:0 4px 12px">전체 보유종목 현황 · ATR은 목표주가가 아닌 가격 변동폭</div>{cards}</td></tr><tr><td data-section="disclosures" style="padding:4px 6px 16px">{dart}</td></tr><tr><td style="padding:4px 6px 20px">{actions}</td></tr></table></td></tr></table></body></html>'''
