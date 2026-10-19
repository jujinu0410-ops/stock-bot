"""Offline replay of archived Cloud Run reports; never executes the trading runner.

Usage: python scripts/preview_intraday_cards.py --archive-dir PATH --baseline-dir PATH
Archives: state_1120/, state_1335/, state_1535/, alert_log.json (raw Sheets values).
Private account data and generated previews must not be committed to GitHub.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ["STOCKBOT_TEST_MODE"] = "1"
os.environ["STOCKBOT_CLOUD_MODE"] = "1"
os.environ["KIWOOM_USE_MOCK"] = "True"

from bs4 import BeautifulSoup
import openpyxl
from src.engine.portfolio_reconciler import compute_atr_metrics
from src.notifications.gmail_notifier import GmailNotifier
from src.notifications.intraday_data import select_latest_alerts, prepare_intraday_holdings, valuation
from src.policy.policy_shadow_store import PolicyShadowReader
from main import verify_pipeline_stock_code_consistency


MAPPING = {
    "stock_code":"종목코드", "stock_name":"종목명", "current_price":"현재가(원)",
    "avg_buy_price":"매입평단가(원)", "quantity":"현재 보유수량", "pnl_amount":"평가손익(원)",
    "pnl_pct":"평가손익률(%)", "daily_change_pct":"당일등락률(%)", "trade_mode":"매매모드",
    "action_status":"실전 대응 전략", "kiwoom_stop_tick_price":"금일확정 손절가(원)",
    "kiwoom_target_tick_price":"최종 익절 트레일링 활성가(원)", "profit_trail_delta":"익절 트레일링폭(원)",
    "recommended_order_qty":"실제 권고 주문수량", "order_direction":"권고 주문방향",
    "stop_update_status":"손절선 갱신상태", "prev_confirmed_stop":"전일확정 손절가(원)",
    "confirmed_stop_price":"금일확정 손절가(원)", "buy_rebound_delta":"반등 확인폭(원)",
    "buy_watch_price":"추매 감시가격(원)", "eval_weight_pct":"계좌평가비중(%)",
    "total_invested":"총 매입금액(원)", "eval_amount":"평가금액(원)",
    "profit_activation_price":"최종 익절 트레일링 활성가(원)", "profit_trail_price":"현재 익절 트레일링선(원)",
}


class ReadOnlyDB:
    def __init__(self,path):
        self.path = path

    def execute_query(self, query, params=()):
        with sqlite3.connect(self.path.as_uri()+"?mode=ro",uri=True) as conn:
            conn.row_factory=sqlite3.Row
            return [dict(r) for r in conn.execute(query,params).fetchall()]

    def get_latest_add_advisory_for_stock(self,code,trading_date=None,max_bar_timestamp=None):
        rows=self.execute_query("SELECT * FROM add_advisory_45m WHERE stock_code=? AND trading_date=? AND bar_timestamp<=? ORDER BY bar_timestamp DESC,id DESC LIMIT 1",(code,trading_date,max_bar_timestamp))
        return rows[0] if rows else None


def inputs(folder):
    db=ReadOnlyDB((folder/"data/stock_system.db").resolve())
    raw=json.loads((folder/"config/portfolio_holdings.json").read_text(encoding="utf-8"))
    rawmap={p["stock_code"]:p for p in raw}
    workbook=openpyxl.load_workbook(folder/"prepared_dispatch/report.xlsx",read_only=True,data_only=True)
    rows=list(workbook.worksheets[0].values)
    held=[]
    for row in rows[2:]:
        entry=dict(zip(rows[1],row))
        if not entry.get("종목코드"):
            continue
        h={k:entry.get(v) for k,v in MAPPING.items()}
        code=h["stock_code"]=str(h["stock_code"]).zfill(6)
        stored=db.execute_query("SELECT * FROM portfolio_positions WHERE stock_code=?",(code,))[0]
        h = dict(stored, **h)
        # Full precision At is persisted by the original engine. Reuse its existing
        # display helper, not the XLSX's rounded At or an invented ATR formula.
        atr=compute_atr_metrics(stored["current_completed_atr"],is_suspended=h["trade_mode"]=="SUSPENDED_HOLD")
        h.update(atr)
        h["avg_buy_price"]=rawmap[code]["avg_buy_price"]
        h["is_etf"]="ETF" in str(entry.get("종합점수"))
        for key in ("prev_confirmed_stop","confirmed_stop_price"):
            h[key]=stored["previous_confirmed_stop" if key=="prev_confirmed_stop" else "confirmed_stop_price"]
        held.append(h)
    workbook.close()
    payload=json.loads((folder/"prepared_dispatch/dispatch_payload.json").read_text(encoding="utf-8"))
    saved=payload["html_content"]
    stamp=re.search(r"기준일시: (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})",saved)[1]
    policy=PolicyShadowReader(db_path=str(folder/"data/policy_shadow.db")).build_report_payload(stamp,held)
    disclosures=[]
    soup=BeautifulSoup(saved,"html.parser")
    for node in soup.select("[data-disclosure-id]"):
        link=node.find("a")
        text=node.get_text(" ",strip=True)
        def between(start,end=None):
            if start not in text:return ""
            return text.split(start,1)[1].split(end,1)[0].strip() if end else text.split(start,1)[1].strip()
        disclosures.append(dict(rcept_no=node["data-disclosure-id"],stock_code=node["data-disclosure-code"],stock_name=node.find("span").get_text().split(" (")[0],report_nm=link.get_text(),link=link["href"],summary=between("공시요약", "시장의미").lstrip(": "),impact=between("시장의미","대응가이드").lstrip(": "),guide=between("대응가이드").lstrip(": ")))
    return held,raw,stamp,policy,disclosures,db,payload


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--archive-dir",type=Path,required=True)
    parser.add_argument("--baseline-dir",type=Path,required=True)
    args=parser.parse_args()
    out=args.archive_dir/"previews";out.mkdir(exist_ok=True)
    spec=importlib.util.spec_from_file_location("baseline_notifier",args.baseline_dir/"src/notifications/gmail_notifier.py")
    old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    legacy_file=Path("src/notifications/mobile_renderer_v2.py")
    assert (ROOT/legacy_file).read_bytes()==(args.baseline_dir/legacy_file).read_bytes(),"Legacy renderer changed"
    stats={}
    with patch("smtplib.SMTP_SSL",side_effect=AssertionError("No email permitted")):
        for session in ("1120","1335","1535"):
            folder=args.archive_dir/("state_"+session)
            held,raw,stamp,policy,disclosures,db,payload=inputs(folder)
            before=copy.deepcopy(held)
            kwargs=dict(date_str=stamp,total_count=0,caught_signals=[],all_results=[],held_portfolio=held,disclosures=disclosures,policy_display=policy)
            base=old.GmailNotifier(sender_email="",app_password="").generate_html_report(**kwargs)
            if session=="1535":
                after=GmailNotifier(sender_email="",app_password="").generate_html_report(**kwargs,report_session="1535")
                assert base==after,"Postmarket byte regression"
                (out/"postmarket_1535_before.html").write_text(base,encoding="utf-8")
                (out/"postmarket_1535_after.html").write_text(after,encoding="utf-8")
                stats[session]={"identical":True,"sha256":hashlib.sha256(after.encode()).hexdigest(),"bytes":len(after.encode()),"holdings":len(held)}
            else:
                alerts=select_latest_alerts(json.loads((args.archive_dir/"alert_log.json").read_text(encoding="utf-8"))["values"],stamp)
                display=prepare_intraday_holdings(held,raw)
                after=GmailNotifier(sender_email="",app_password="").generate_html_report(**kwargs,report_session=session,recent_alerts=alerts,intraday_holdings=display,intraday_db_manager=db)
                verify_pipeline_stock_code_consistency(raw,db,held,folder/"prepared_dispatch/report.xlsx",after,disclosures=disclosures)
                for h in display:
                    valuation(h)
                    if h["trade_mode"]!="SUSPENDED_HOLD":
                        expected=compute_atr_metrics(db.execute_query("SELECT current_completed_atr FROM portfolio_positions WHERE stock_code=?",(h["stock_code"],))[0]["current_completed_atr"])
                        assert h["buy_trailing_dist"]==expected["buy_trailing_dist"]
                        assert h["sell_trailing_dist"]==expected["sell_trailing_dist"]
                        node=BeautifulSoup(after,"html.parser").find(attrs={"data-holding-code":h["stock_code"]})
                        from src.notifications.intraday_renderer import money
                        assert money(expected["buy_trailing_dist"]) in node.get_text()
                        assert money(expected["sell_trailing_dist"]) in node.get_text()
                assert set(re.findall(r'data-holding-code="(\d{6})"',after))=={h["stock_code"] for h in held}
                (out/("intraday_"+session+".html")).write_text(after,encoding="utf-8")
                stats[session]={"holdings":len(held),"asof":stamp,"bytes":len(after.encode()),"pnl_verified":len(display),"alerts_matched":sum(h["stock_code"] in alerts["by_code"] for h in held),"alert_source":"ALERT_LOG", "sha256":hashlib.sha256(after.encode()).hexdigest()}
                (out/("input_"+session+".json")).write_text(json.dumps(dict(held=held,alerts=alerts),ensure_ascii=False,indent=2),encoding="utf-8")
            assert held==before,"Renderer mutated input"
            (out/("original_"+session+".html")).write_text(payload["html_content"],encoding="utf-8")
    (out/"validation.json").write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(stats,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
