import requests
import pandas as pd
import numpy as np
import yfinance as yf
from typing import Dict, Any, List, Optional
from src.utils.logger import logger

class Intraday45mAnalyzer:
    """
    보유 종목의 최근 3~5영업일(24~40개 45분봉) 데이터를 수집하여
    일목균형표 파동(9, 26) 표준 수치로 통일된 보조지표를 연산합니다.
    - OBV 9봉 MA (1일 거래일 9봉 45분봉 기준)
    - ADX 14 (표준 14일/14봉 DMI/ADX)
    - Chaikin Oscillator (13, 26) (3일 거래일 26봉 및 일목 26 기준선 변곡점 연동)
    """
    def __init__(self):
        pass

    def get_symbol_ticker(self, stock_code: str) -> str:
        """KRX 주식 코드를 yfinance 코드로 변환 (기본 .KS, KOSDAQ 고려)"""
        code = str(stock_code).zfill(6)
        kosdaq_codes = {'047770', '055490', '140670', '206650', '234920', '241520', '348340'}
        if code in kosdaq_codes:
            return f"{code}.KQ"
        return f"{code}.KS"

    def analyze_45m_indicators(self, stock_code: str) -> Dict[str, Any]:
        """
        통일 표준 지표 (OBV 9, ADX 14, Chaikin 13/26) 기반 45분봉 정밀 수급 검증 및 원자값 산출
        """
        symbol = self.get_symbol_ticker(stock_code)
        default_res = {
            "stock_code": stock_code,
            "has_45m_data": False,
            "adx_14_45m": 0.0,
            "plus_di_45m": 0.0,
            "minus_di_45m": 0.0,
            "obv_45m": 0,
            "obv_45m_trend": "데이터 대기",
            "chaikin_osc_45m": 0,
            "chaikin_flow_45m": "데이터 대기",
            "intraday_cho_recent2": [0, 0],
            "intraday_cho_is_subzero_2bars": False,
            "intraday_cho_note": "",
            "is_45m_breakdown": False,
            "is_obv_dead": False,
            "is_cho_outflow": False,
            "is_45m_bearish_2plus": False,
            "signal_45m_text": "45분봉 데이터 대기",
            "action_45m_recommendation": ""
        }

        try:
            # 1. 최근 5영업일치(period="5d") 15분봉 수집 -> 40여개 45분봉 확보
            ticker = yf.Ticker(symbol)
            df_15m = ticker.history(interval="15m", period="5d")
            
            if df_15m.empty or len(df_15m) < 15:
                alt_symbol = f"{stock_code}.KQ" if symbol.endswith(".KS") else f"{stock_code}.KS"
                df_15m = yf.Ticker(alt_symbol).history(interval="15m", period="5d")

            if df_15m.empty or len(df_15m) < 15:
                logger.warning(f"[Intraday45mAnalyzer] {stock_code} 15분봉 데이터 수집 실패")
                return default_res

            # 2. 45분봉 리샘플링 (1일 = 약 9개 캔들)
            df_45m = df_15m.resample('45min').agg({
                'Open': 'first',
                'High': 'max',
                'Low': 'min',
                'Close': 'last',
                'Volume': 'sum'
            }).dropna()

            if len(df_45m) < 16:  # 최소 2영업일치(16봉) 이상 필요
                return default_res

            high = df_45m['High']
            low = df_45m['Low']
            close = df_45m['Close']
            vol = df_45m['Volume']

            # 3. 45분봉 ADX (14) 연산
            up_move = high.diff()
            down_move = -low.diff()
            plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
            minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

            tr = np.maximum(high - low, np.maximum((high - close.shift(1)).abs(), (low - close.shift(1)).abs()))
            tr_s = pd.Series(tr, index=df_45m.index).ewm(alpha=1/14, adjust=False).mean()
            
            plus_di_series = 100 * pd.Series(plus_dm, index=df_45m.index).ewm(alpha=1/14, adjust=False).mean() / (tr_s + 1e-9)
            minus_di_series = 100 * pd.Series(minus_dm, index=df_45m.index).ewm(alpha=1/14, adjust=False).mean() / (tr_s + 1e-9)

            dx = 100 * (plus_di_series - minus_di_series).abs() / (plus_di_series + minus_di_series + 1e-9)
            adx_series = dx.ewm(alpha=1/14, adjust=False).mean()

            # 4. 45분봉 OBV (9) 및 정밀 데드크로스/이탈 연산
            obv_series = (np.sign(close.diff()) * vol).fillna(0).cumsum()
            obv_ema9 = obv_series.ewm(span=9, adjust=False).mean()
            
            recent_9_obv = obv_series.iloc[-9:]
            recent_9_ema = obv_ema9.iloc[-9:]
            
            is_below_ema = obv_series.iloc[-1] < obv_ema9.iloc[-1]
            dead_bars_count = (recent_9_obv < recent_9_ema).sum()
            is_obv_falling = obv_series.iloc[-1] < obv_series.iloc[-9]
            
            obv_dead_flag = is_below_ema and (dead_bars_count >= 5 or is_obv_falling)

            if obv_dead_flag:
                obv_trend_str = "📉 OBV(9) 데드크로스 (이탈)"
            elif is_below_ema:
                obv_trend_str = "⚠️ OBV(9) 조정 약세"
            else:
                obv_trend_str = "📈 OBV(9) 매집 유지 (상승)"

            # 5. 45분봉 Chaikin Oscillator (13, 26) 연산 & 최근 2봉 원자값
            hl_diff = high - low
            mfm = np.where(hl_diff == 0, 0, ((close - low) - (high - close)) / hl_diff)
            mfv = mfm * vol
            cho_series = pd.Series(mfv, index=df_45m.index).ewm(span=13, adjust=False).mean() - pd.Series(mfv, index=df_45m.index).ewm(span=26, adjust=False).mean()

            latest_cho = int(cho_series.iloc[-1])
            intraday_cho_recent2 = [int(cho_series.iloc[-2]), int(cho_series.iloc[-1])]
            intraday_cho_is_subzero_2bars = (intraday_cho_recent2[0] <= 0) and (intraday_cho_recent2[1] <= 0)

            # 라벨 병기용 45분봉 CHO 노트
            if intraday_cho_is_subzero_2bars:
                intraday_cho_note = " (일봉+45분봉 이중확인)"
            else:
                intraday_cho_note = " (45분봉 단기반등 주의)"

            recent_18_cho = cho_series.iloc[-18:]
            cho_negative_count = (recent_18_cho < 0).sum()
            cho_dead_flag = (latest_cho < 0) and (cho_negative_count >= 8)

            if cho_dead_flag:
                cho_flow_str = f"💸 CHO 자금유출 ({latest_cho:+,d})"
            elif latest_cho < 0:
                cho_flow_str = f"⚠️ CHO 자금약세 ({latest_cho:+,d})"
            else:
                cho_flow_str = f"💧 CHO 자금유입 ({latest_cho:+,d})"

            latest_adx = float(adx_series.iloc[-1])
            latest_plus_di = float(plus_di_series.iloc[-1])
            latest_minus_di = float(minus_di_series.iloc[-1])
            latest_obv = int(obv_series.iloc[-1])

            # 6. ADX (14) 하방 추세 하락 신호
            adx_bear_flag = (latest_adx >= 22.0) and (latest_minus_di > latest_plus_di)

            # 7. 45분봉 3개 지표 중 2개 이상 하락신호 여부 (4순위 분할매수 등 판단용)
            bearish_signals_count = sum([obv_dead_flag, cho_dead_flag, adx_bear_flag])
            is_45m_bearish_2plus = (bearish_signals_count >= 2)

            is_45m_breakdown = obv_dead_flag and cho_dead_flag
            is_obv_dead = obv_dead_flag and not cho_dead_flag
            is_cho_outflow = cho_dead_flag and not obv_dead_flag

            if is_45m_breakdown:
                sig_text = f"🚨 45m 이중수급이탈 (ADX {latest_adx:.1f} | CHO {latest_cho:+,d})"
                action_rec = "🚨 단기 조정 (OBV이탈 + CHO유출)"
            elif is_obv_dead:
                sig_text = f"⚠️ 45m OBV(9) 이탈 (ADX {latest_adx:.1f} | CHO {latest_cho:+,d})"
                action_rec = "⚠️ OBV 이탈 (45m OBV 9데드)"
            elif is_cho_outflow:
                sig_text = f"⚠️ 45m CHO(13,26) 유출 (ADX {latest_adx:.1f} | CHO {latest_cho:+,d})"
                action_rec = "⚠️ CHO 유출 (45m CHO 26유출)"
            elif latest_adx >= 25.0 and latest_plus_di > latest_minus_di:
                sig_text = f"🟢 45m 강력 상방추세 (ADX {latest_adx:.1f} | CHO {latest_cho:+,d})"
                action_rec = "🟢 45m 추세유지 (안정보유)"
            elif latest_plus_di > latest_minus_di and latest_cho > 0:
                sig_text = f"🟢 45m 매집 우상향 (ADX {latest_adx:.1f} | CHO {latest_cho:+,d})"
                action_rec = "🟢 45m 수급양호 (안정보유)"
            else:
                sig_text = f"⏸️ 45m 조정/관망 (ADX {latest_adx:.1f} | CHO {latest_cho:+,d})"
                action_rec = "⏸️ 45m 단기조정 (관망)"

            logger.info(f"[Intraday45mAnalyzer] {stock_code} 45분봉 정밀분석 - ADX:{latest_adx:.1f}, OBV9데드:{obv_dead_flag}, CHO26최근2봉:{intraday_cho_recent2} ➔ 2개이상하락:{is_45m_bearish_2plus}")

            return {
                "stock_code": stock_code,
                "has_45m_data": True,
                "adx_14_45m": round(latest_adx, 1),
                "plus_di_45m": round(latest_plus_di, 1),
                "minus_di_45m": round(latest_minus_di, 1),
                "obv_45m": latest_obv,
                "obv_45m_trend": obv_trend_str,
                "chaikin_osc_45m": latest_cho,
                "chaikin_flow_45m": cho_flow_str,
                "intraday_cho_recent2": intraday_cho_recent2,
                "intraday_cho_is_subzero_2bars": intraday_cho_is_subzero_2bars,
                "intraday_cho_note": intraday_cho_note,
                "is_45m_breakdown": is_45m_breakdown,
                "is_obv_dead": is_obv_dead,
                "is_cho_outflow": is_cho_outflow,
                "is_45m_bearish_2plus": is_45m_bearish_2plus,
                "signal_45m_text": sig_text,
                "action_45m_recommendation": action_rec
            }

        except Exception as e:
            logger.error(f"[Intraday45mAnalyzer] {stock_code} 45분봉 분석 예외: {e}", exc_info=True)
            return default_res
