"""AI-free rendering of source values. No market calls, calculations or new opinions."""
from __future__ import annotations
import html
import json
import math

NOTICE = (
    "AI 해설은 일시적으로 생략되었습니다. "
    "아래 분석과 가격전략은 V8 및 Python Strategy Engine의 검증된 정량 결과입니다."
)
TITLE = "오늘의 V8 1종목 — AI 해설 일시 생략"
NUMERIC_FIELDS = (
    "current_price", "atr14", "entry_zone_low", "entry_zone_high", "pullback_zone",
    "breakout_trigger", "target_1", "target_2", "stop_price", "rr_target1", "rr_target2",
    "representative_entry",
)
STRATEGY_FIELDS = NUMERIC_FIELDS + (
    "atr_pct", "ma5", "ma20", "ma60", "ma120", "high_20d", "low_20d", "high_60d",
    "low_60d", "recent_support", "recent_resistance", "swing_low", "swing_high",
    "trailing_distance", "risk_pct", "reward_pct", "chase_risk", "chase_reasons",
    "current_price_asof", "daily_ohlcv_asof", "market_symbol",
)
FINANCIAL_FIELDS = ("f_score", "cat_pts", "growth_pts", "cf_pts", "debt_pts", "gov_pts",
                    "revenue", "operating_profit", "ocf", "sanity_flag", "fundamental_state")


def _get(source, name, default=None):
    return source.get(name, default) if isinstance(source, dict) else getattr(source, name, default)


def _pick(source, fields):
    return {key: _get(source, key) for key in fields}


def briefing_data(selector, context, bundle, strategy):
    """Explicit allowlist excludes raw bars and source documents at every entry point."""
    return {
        "stock_name": _get(bundle, "stock_name"), "stock_code": _get(bundle, "stock_code"),
        "selection": _pick(selector, ("decision", "selector_score", "video_count",
            "independent_channel_count", "novelty_score", "yt_evidence_score")),
        "youtube": _pick(context, ("source", "reason")),
        "financial": _pick(_get(bundle, "financial_summary", {}), FINANCIAL_FIELDS),
        "8Q": _get(bundle, "quarterly_8q_table"),
        "financial_trend": _get(bundle, "prev_vs_current_summary"),
        "debt": _pick(bundle, ("debt_ratio", "prev_debt_ratio", "ocf_annual")),
        "valuation": _get(bundle, "valuation_summary", "INSUFFICIENT_DATA (source unavailable)"),
        "research": _get(bundle, "research_summary", "INSUFFICIENT_DATA (source unavailable)"),
        "growth": _pick(bundle, ("order_backlog_summary", "new_orders_summary", "forward_state",
            "capa_summary", "industry_gate", "industry_score", "industry_profile", "exposure_type")),
        "DART_risk": _get(bundle, "dart_risks"),
        "technical": _pick(bundle, ("technical_action", "technical_summary", "daily_adx_di", "t_score", "t_raw")),
        "45m": _pick(bundle, ("intraday_status", "intraday_data_quality", "intraday_adx_di",
            "obv_45m_trend", "intraday_cho_recent2", "intraday_last_timestamp")),
        "strategy": _pick(strategy, STRATEGY_FIELDS),
        # Only source-labelled evidence is placed in Bull/Bear. Neutral evidence stays neutral.
        "Bull evidence": _get(bundle, "bull_evidence", []),
        "Bear evidence": _get(bundle, "bear_evidence", []),
        "financial_evidence": _get(bundle, "fundamental_evidence", []),
        "quality_warnings": _get(bundle, "quality_warnings", []),
        "monitor_plan": {
            "MONITOR_RECOMMENDED": "YES", "MONITOR_TRIGGER_1": _get(strategy, "pullback_zone"),
            "MONITOR_TRIGGER_2": _get(strategy, "breakout_trigger"),
            "DAMAGE_TRIGGER": _get(strategy, "stop_price"), "SOURCE": "QUANT_STRATEGY",
        },
    }


def validate_numeric_strategy(strategy):
    problems = []
    for key in NUMERIC_FIELDS:
        value = _get(strategy, key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            problems.append(key + "_invalid")
    if problems:
        return problems
    s = _pick(strategy, NUMERIC_FIELDS)
    if not s["stop_price"] < s["pullback_zone"] <= s["entry_zone_high"]:
        problems.append("stop_pullback_order")
    if not s["entry_zone_low"] <= s["representative_entry"] <= s["entry_zone_high"]:
        problems.append("entry_order")
    if not s["representative_entry"] < s["target_1"] < s["target_2"]:
        problems.append("target_order")
    risk = s["representative_entry"] - s["stop_price"]
    if risk <= 0:
        problems.append("risk_invalid")
    else:
        for target, rr in (("target_1", "rr_target1"), ("target_2", "rr_target2")):
            if not math.isclose(s[rr], round((s[target] - s["representative_entry"]) / risk, 2), abs_tol=0.011):
                problems.append(rr + "_inconsistent")
    return problems


def render_quant_briefing(selector, context, bundle, strategy):
    problems = validate_numeric_strategy(strategy)
    if problems:
        raise ValueError("QUANT_INVALID:" + ",".join(problems))
    data = briefing_data(selector, context, bundle, strategy)
    if not data["stock_name"] or not data["stock_code"] or not data["8Q"]:
        raise ValueError("QUANT_SOURCE_INCOMPLETE")
    data.update(quality_grade="Q", analyst_model="NONE", notice=NOTICE,
                provenance=_get(bundle, "source_files", []))
    labels = {
        "current_price": "현재가", "atr14": "ATR14", "entry_zone_low": "진입구간 하한",
        "entry_zone_high": "진입구간 상한", "pullback_zone": "눌림목",
        "breakout_trigger": "돌파조건 (가격 도달 시 재평가)", "target_1": "목표1", "target_2": "목표2",
        "stop_price": "훼손가격", "rr_target1": "RR1", "rr_target2": "RR2",
        "representative_entry": "RR 기준 진입가", "chase_risk": "CHASE_RISK",
    }
    lines = [f"# [{TITLE}]", NOTICE, f"{data['stock_name']} / {data['stock_code']}"]
    for key in ("selection", "youtube", "technical", "45m"):
        lines += [f"## {key}", json.dumps(data[key], ensure_ascii=False, default=str)]
    lines += ["## V8 / Python Strategy Engine"]
    for key, label in labels.items():
        value = data["strategy"][key]
        lines.append(f"{label}: {value if value is not None else 'INSUFFICIENT_DATA'} [Quant]")
    for key in ("8Q", "financial", "financial_trend", "debt", "valuation", "DART_risk",
                "research", "growth", "Bull evidence", "Bear evidence", "financial_evidence", "quality_warnings"):
        value = data[key]
        lines += [f"## {key}", json.dumps(value, ensure_ascii=False, default=str)
                  if value not in (None, "", []) else "INSUFFICIENT_DATA (소스에 구조화 근거 없음)"]
    lines += ["## 감시 조건"] + [f"{key}={value}" for key, value in data["monitor_plan"].items()]
    text = "\n\n".join(lines)
    document = ("<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
                f"<title>{TITLE}</title></head><body><pre>{html.escape(text)}</pre></body></html>")
    # No default=str: reject non-JSON / non-finite source values instead of hiding defects.
    json.dumps(data, ensure_ascii=False, allow_nan=False)
    return {"briefing_md": text, "data": data, "html": document}
