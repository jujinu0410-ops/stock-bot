"""
phase2_5_runner.py — Daily V8 Phase 2.5 실행기

Selector → V8 Bundle → Strategy Engine → Gemini 브리핑 순으로 실행.
Gmail/GCS/push 금지. local commit까지만.
"""

import os
import json
import sys
from datetime import datetime
from pytz import timezone

from daily_v8.selector import run_selector
from daily_v8.v8_runner import run_v8_headless
from daily_v8.strategy_engine import compute_strategy
from daily_v8.ai_analyst import generate_daily_v8_briefing


def run_phase_2_5(target_date=None):
    print("=== Daily V8 One-Stock Briefing Phase 2.5 ===")

    # 1. Selector
    now_kst = datetime.now(timezone("Asia/Seoul"))
    kst_today_str = now_kst.strftime("%Y-%m-%d")
    target_date = target_date or kst_today_str
    using_historical = target_date != kst_today_str

    selector_result = run_selector(
        report_date=target_date,
        kst_today_str=kst_today_str,
        using_historical=using_historical,
        dry_run=True,
        now_kst=now_kst,
    )

    if not selector_result:
        print("No candidates from selector.")
        return

    print(f"Winner: {selector_result.stock_name} ({selector_result.stock_code})")

    # Quality gate
    if "NO_DATA" in selector_result.technical.status:
        print("Quality gate rejected (NO_DATA).")
        return
    if "BROKEN" in selector_result.technical.status:
        print("Quality gate rejected (BROKEN).")
        return
    print("Quality Gate: PASS")

    # 2. V8 Run (count as 1 real run)
    v8_bundle = run_v8_headless(selector_result.stock_code)
    if v8_bundle == "BLOCKED_MISSING_CREDENTIAL":
        print("V8_REAL_RUN=BLOCKED_MISSING_CREDENTIAL")
        return
    if not v8_bundle:
        print("V8 bundle generation failed.")
        return
    print("V8 Bundle: OK")

    # 3. Reuse the single V8 result. Strategy itself fetches daily market data.
    strategy = compute_strategy(selector_result.stock_code, dto=v8_bundle, now=now_kst)
    if strategy:
        print(f"Strategy: ATR14={int(strategy.atr14):,} ENTRY={int(strategy.entry_zone_low):,}~{int(strategy.entry_zone_high):,} TARGET1={int(strategy.target_1):,} STOP={int(strategy.stop_price):,} RR={strategy.risk_reward_ratio} CHASE={strategy.chase_risk}")
    else:
        print("Strategy Engine: FAILED (INSUFFICIENT_DATA)")
        return

    # 4. YouTube context from DB
    import sqlite3
    from daily_v8.selector import DB_PATH
    db_path = DB_PATH
    yt_context = {"reason": "N/A", "source": "N/A"}
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT top_mentioned_stocks FROM daily_intelligence WHERE report_date = ?",
            (target_date,),
        )
        row = cur.fetchone()
        if row and row[0]:
            try:
                stocks = json.loads(row[0])
                for s in stocks:
                    if s.get("stock_code") == selector_result.stock_code:
                        yt_context["reason"] = s.get("reason", "N/A")
                        yt_context["source"] = f"YouTube Intelligence DB ({target_date})"
                        break
            except Exception:
                pass
        conn.close()

    # 5. Gemini Briefing (count as 1 real call)
    result = generate_daily_v8_briefing(
        selector_result, yt_context, v8_bundle, strategy=strategy, max_calls=1
    )
    briefing_md = result.get("briefing_md")
    analyst_model = result.get("analyst_model", "NONE")
    model_degraded = result.get("model_degraded", False)

    if not briefing_md:
        print("Gemini briefing generation failed.")
        return

    print(f"ANALYST_MODEL={analyst_model}  MODEL_DEGRADED={model_degraded}")

    # 6. Save outputs
    date_str = datetime.now().strftime("%Y-%m-%d")
    out_dir = os.path.join("daily_v8", "output")
    os.makedirs(out_dir, exist_ok=True)

    base_name = f"daily_v8_briefing_v25_{date_str}_{selector_result.stock_code}"

    json_path = os.path.join(out_dir, f"{base_name}.json")
    import dataclasses

    def _serial(obj):
        if dataclasses.is_dataclass(obj):
            return dataclasses.asdict(obj)
        return str(obj)

    payload = {
        "stock_code": selector_result.stock_code,
        "stock_name": selector_result.stock_name,
        "analyst_model": analyst_model,
        "model_degraded": model_degraded,
        "strategy": dataclasses.asdict(strategy) if strategy else None,
        "quality_flags": {
            "ENTRY_PRICE_PRESENT": strategy is not None,
            "PULLBACK_PRICE_PRESENT": strategy is not None,
            "ATR14_PRESENT": strategy is not None,
            "TARGET_PRESENT": strategy is not None,
            "DAMAGE_PRICE_PRESENT": strategy is not None,
            "RISK_REWARD_PRESENT": strategy is not None,
            "MONITOR_PLAN_PRESENT": True,
            "8Q_ANALYSIS_PRESENT": bool(v8_bundle.quarterly_8q_table),
            "VALUATION_PRESENT": bool(v8_bundle.financial_summary),
        },
        "briefing": briefing_md,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=_serial)

    html_path = os.path.join(out_dir, f"{base_name}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        body = briefing_md.replace("\n", "<br>")
        f.write(f"<html><head><meta charset='utf-8'><title>V8 브리핑 {date_str}</title></head><body>{body}</body></html>")

    print(f"\n=== PHASE 2.5 COMPLETE ===")
    print(f"OUTPUT_JSON: {json_path}")
    print(f"OUTPUT_HTML: {html_path}")
    if strategy:
        print(f"\nATR14={int(strategy.atr14):,}원")
        print(f"ENTRY_ZONE={int(strategy.entry_zone_low):,}~{int(strategy.entry_zone_high):,}원")
        print(f"PULLBACK_ZONE={int(strategy.pullback_zone):,}원")
        print(f"BREAKOUT_TRIGGER={int(strategy.breakout_trigger):,}원")
        print(f"TARGET_1={int(strategy.target_1):,}원")
        print(f"TARGET_2={int(strategy.target_2):,}원")
        print(f"DAMAGE_PRICE={int(strategy.stop_price):,}원")
        print(f"RISK_REWARD=1:{strategy.risk_reward_ratio:.2f}")
        print(f"CHASE_RISK={strategy.chase_risk}")
    print(f"ANALYST_MODEL={analyst_model}")
    print(f"MODEL_DEGRADED={model_degraded}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Local debug runner; no Gmail or GCS")
    parser.add_argument("--date", help="Explicit historical debug date; default KST today")
    run_phase_2_5(target_date=parser.parse_args().date)
