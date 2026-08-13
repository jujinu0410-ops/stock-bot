import sys
import json
sys.path.insert(0, '.')
from src.analysis.intraday_analysis import Intraday45mAnalyzer

with open('config/portfolio_holdings.json', encoding='utf-8') as f:
    holdings = json.load(f)

analyzer = Intraday45mAnalyzer()
print("=== [보유 15개 종목 45분봉 ADX·OBV·채킨오실레이터 산출 검증] ===")
for h in holdings:
    res = analyzer.analyze_45m_indicators(h['stock_code'])
    print(f"• {h['stock_name']}({h['stock_code']}) ➔ ADX(45m): {res['adx_14_45m']} (+DI:{res['plus_di_45m']}/-DI:{res['minus_di_45m']}) | OBV: {res['obv_45m_trend']} | Chaikin_Osc: {res['chaikin_osc_45m']:,} | 신호: {res['signal_45m_text']}")
