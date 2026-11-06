"""One-way, full-session migration. Archived M45 is never co-evaluated."""
from datetime import datetime
from zoneinfo import ZoneInfo
import os

CUTOVER_DAY = "2026-11-05"
CONTRACT = "KRX_M30_V1"

def active(now=None):
    now = now or datetime.now(ZoneInfo("Asia/Seoul"))
    if now.tzinfo is not None: now = now.astimezone(ZoneInfo("Asia/Seoul"))
    return now.date().isoformat() >= CUTOVER_DAY

def install_cutover(legacy_app):
    from flask import jsonify
    from services.youtube_signal_service_m30 import app as new_app
    old_wsgi, new_wsgi = legacy_app.wsgi_app, new_app.wsgi_app
    def contract():
        return jsonify({"ok":True,"contract_version":CONTRACT,
            "cutover_day":CUTOVER_DAY,"active_interval_minutes":30 if active() else 45,
            "target_interval_minutes":30,"bars_per_regular_session":13,
            "first_end":"09:30","last_end":"15:30","closing_auction":"15:20-15:30",
            "last_bar_policy":"PARTIAL_UNTIL_FULL_OHLCV_AND_CONFIRMED_CLOSE",
            "order_api":False,"parallel_evaluation":False})
    legacy_app.add_url_rule('/timeframe-contract','timeframe_contract',contract)
    new_app.add_url_rule('/timeframe-contract','timeframe_contract',contract)
    def dispatch(environ,start_response):
        # Evidence collection is read-only and available for pre-cutover seed
        # verification. Active monitoring remains exclusively on today's basis.
        evidence = environ.get('PATH_INFO') == '/paper-m30-session'
        return (new_wsgi if active() or evidence else old_wsgi)(environ,start_response)
    legacy_app.wsgi_app = dispatch
