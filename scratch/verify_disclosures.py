# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.getcwd())
from src.api.dart_api import DartAPIClient
from src.database.db_manager import DatabaseManager
from src.engine.portfolio_manager import PortfolioManager

db = DatabaseManager()
pm = PortfolioManager(db)
eval_list = pm.get_held_portfolio_status()

dart = DartAPIClient()
disclosures = dart.get_recent_disclosures_briefing(eval_list, target_date="20260814")

print(f"=== [DART 공시 브리핑 검증 ({len(disclosures)}건)] ===")
for d in disclosures:
    print(f"\n[{d['stock_name']}({d['stock_code']})] - {d['report_nm']}")
    print(f"• 요약: {d['summary']}")
    print(f"• 시장의미: {d['impact']}")
    print(f"• 대응가이드: {d['guide']}")
