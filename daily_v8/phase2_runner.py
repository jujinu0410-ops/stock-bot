import os
import json
import sys
from datetime import datetime
from daily_v8.selector import run_selector
from daily_v8.v8_runner import run_v8_headless
from daily_v8.ai_analyst import generate_daily_v8_briefing

def run_phase_2(target_date=None):
    print("=== Daily V8 One-Stock Briefing Phase 2 ===")
    
    from pytz import timezone
    now_kst = datetime.now(timezone("Asia/Seoul"))
    kst_today_str = now_kst.strftime("%Y-%m-%d")
    target_date = target_date or kst_today_str
    using_historical = target_date != kst_today_str
    
    selector_result = run_selector(
        report_date=target_date,
        kst_today_str=kst_today_str,
        using_historical=using_historical,
        dry_run=True,
        now_kst=now_kst
    )
    
    if not selector_result:
        print("No candidates from selector.")
        return
        
    print(f"Winner: {selector_result.stock_name} ({selector_result.stock_code})")
    
    if "NO_DATA" in selector_result.technical.status or "NO_DATA" in selector_result.intraday.status:
        print("Quality gate rejected (NO_DATA).")
        return
    if "BROKEN" in selector_result.technical.status or "BAD" in selector_result.intraday.status:
        print("Quality gate rejected (BROKEN/BAD).")
        return
        
    print("Quality Gate Passed.")
    
    v8_bundle = run_v8_headless(selector_result.stock_code)
    if v8_bundle == "BLOCKED_MISSING_CREDENTIAL":
        print("V8 Run blocked: Missing credentials.")
        return
    if not v8_bundle:
        print("V8 Run failed to produce bundle.")
        return
        
    print("V8 Bundle generated successfully.")
    
    import sqlite3
    from daily_v8.selector import DB_PATH
    db_path = DB_PATH
    yt_context = {"reason": "N/A", "source": "N/A"}
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT top_mentioned_stocks FROM daily_intelligence WHERE report_date = ?", (target_date,))
        row = cur.fetchone()
        if row and row[0]:
            try:
                stocks = json.loads(row[0])
                for s in stocks:
                    if s.get("stock_code") == selector_result.stock_code:
                        yt_context["reason"] = s.get("reason", "N/A")
                        yt_context["source"] = f"YouTube Intelligence on {target_date}"
                        break
            except Exception as e:
                pass
        conn.close()
        
    result = generate_daily_v8_briefing(selector_result, yt_context, v8_bundle, max_calls=1)
    briefing_md = result.get("briefing_md")
    if not briefing_md:
        print("Failed to generate AI briefing.")
        return
        
    date_str = datetime.now().strftime("%Y-%m-%d")
    out_dir = os.path.join("daily_v8", "output")
    os.makedirs(out_dir, exist_ok=True)
    
    base_name = f"daily_v8_briefing_{date_str}_{selector_result.stock_code}"
    
    json_path = os.path.join(out_dir, f"{base_name}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "stock_code": selector_result.stock_code,
            "stock_name": selector_result.stock_name,
            "briefing": briefing_md
        }, f, ensure_ascii=False, indent=2)
        
    html_path = os.path.join(out_dir, f"{base_name}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        html_content = briefing_md.replace("\n", "<br>")
        f.write(f"<html><head><meta charset='utf-8'></head><body>{html_content}</body></html>")
        
    print(f"Outputs saved to {json_path} and {html_path}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Local debug runner; no Gmail or GCS")
    parser.add_argument("--date", help="Explicit historical debug date; default KST today")
    run_phase_2(target_date=parser.parse_args().date)
