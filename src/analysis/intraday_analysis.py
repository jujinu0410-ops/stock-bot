import requests
import pandas as pd
import numpy as np
import yfinance as yf
from typing import Dict, Any, List, Optional
from src.utils.logger import logger

class Intraday45mAnalyzer:
    """
    보유 종목의 최근 3~5영업일(24~40개 45분봉) 데이터를 수집하여
    OBV 3일간 데드크로스 미회복, Chaikin 2일 연속 음수 유출(CHO < 0), ADX 하방 강화를 다차원으로 엄격 검증합니다.
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
        최근 3~5영업일 45분봉 ADX, OBV, Chaikin Oscillator 3대 다차원 수급 지표 정밀 검증
        """
        symbol = self.get_symbol_ticker(stock_code)
        default_res = {
            "stock_code": stock_code,
            "has_45m_data": False,
            "adx_14_45m": 0.0,
            "plus_di_45m": 0.0,
            "minus_di_45m": 0.0,
            "obv_45m": 0,
            "obv_45m_trend": "데이터 미수집",
            "chaikin_osc_45m": 0,
            "chaikin_flow_45m": "데이터 미수집",
            "is_45m_breakdown": False,
            "is_45m_weak": False,
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

            # 2. 45분봉 리샘플링
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

            # 4. 45분봉 OBV 및 3일간(최근 16~24봉) 데드크로스/이탈 검증
            obv_series = (np.sign(close.diff()) * vol).fillna(0).cumsum()
            obv_ema10 = obv_series.ewm(span=10, adjust=False).mean()
            
            # 최근 16봉(2영업일) 중 OBV < OBV_EMA10 횟수 및 3일전 대비 하락 검증
            recent_16_obv = obv_series.iloc[-16:]
            recent_16_obv_ema = obv_ema10.iloc[-16:]
            obv_dead_count = (recent_16_obv < recent_16_obv_ema).sum()
            obv_dead_flag = (obv_dead_count >= 10) and (obv_series.iloc[-1] < obv_series.iloc[-8])

            obv_trend_str = "📉 3일 수급이탈 (데드크로스)" if obv_dead_flag else "📈 매집 유지 (상승)"

            # 5. 45분봉 Chaikin Oscillator 및 2일 이상(16봉) 음수 유출 (CHO < 0 필수) 검증
            hl_diff = high - low
            mfm = np.where(hl_diff == 0, 0, ((close - low) - (high - close)) / hl_diff)
            mfv = mfm * vol
            cho_series = pd.Series(mfv, index=df_45m.index).ewm(span=3, adjust=False).mean() - pd.Series(mfv, index=df_45m.index).ewm(span=10, adjust=False).mean()

            latest_cho = int(cho_series.iloc[-1])
            recent_16_cho = cho_series.iloc[-16:]
            cho_negative_count = (recent_16_cho < 0).sum()
            
            # 오동작 방지: Chaikin 오실레이터가 양수(CHO > 0)인 종목(포스코인터내셔널 등)은 절대 이탈 판정 제외!
            cho_dead_flag = (latest_cho < 0) and (cho_negative_count >= 10)

            cho_flow_str = "💸 2일연속 자금유출 (-)" if cho_dead_flag else f"💧 자금 유입 ({latest_cho:+,d})"

            latest_adx = float(adx_series.iloc[-1])
            latest_plus_di = float(plus_di_series.iloc[-1])
            latest_minus_di = float(minus_di_series.iloc[-1])
            latest_obv = int(obv_series.iloc[-1])

            # 6. ADX 하방 추세 강화 조건 (ADX >= 22.0 AND -DI > +DI AND ADX 상승세 AND CHO < 0 필수)
            adx_diff_3 = adx_series.diff().iloc[-3:].sum()
            adx_bear_accel = (latest_adx >= 22.0) and (latest_minus_di > latest_plus_di) and (adx_diff_3 > 0) and (latest_cho < 0)

            # 7. 🔥 [핵심 정밀 로직] 3일간 45분봉 수급이탈/하방강화 최종 조건
            # OBV 데드크로스 미회복 + Chaikin 2일 연속 음수유출(CHO < 0) 필수!
            is_45m_breakdown = obv_dead_flag and (cho_dead_flag or adx_bear_accel)
            is_45m_weak = cho_dead_flag or (obv_dead_flag and not is_45m_breakdown)

            action_rec = ""
            if is_45m_breakdown:
                sig_text = f"🚨 45m 3일 수급이탈 (ADX {latest_adx:.1f} | CHO {latest_cho:+,d})"
                action_rec = "🚨 단기 매도 (45m 3일 수급이탈)"
            elif is_45m_weak:
                sig_text = f"⚠️ 45m 3일 수급약세 (ADX {latest_adx:.1f} | CHO {latest_cho:+,d})"
                action_rec = "⚠️ 분량축소 (45m 3일 수급약세)"
            elif latest_adx >= 25.0 and latest_plus_di > latest_minus_di:
                sig_text = f"🟢 45m 강력 상방추세 (ADX {latest_adx:.1f} | CHO {latest_cho:+,d})"
                action_rec = "🟢 45m 추세유지 (안정보유)"
            elif latest_plus_di > latest_minus_di and latest_cho > 0:
                sig_text = f"🟢 45m 매집 우상향 (ADX {latest_adx:.1f} | CHO {latest_cho:+,d})"
                action_rec = "🟢 45m 수급양호 (안정보유)"
            else:
                sig_text = f"⏸️ 45m 조정/관망 (ADX {latest_adx:.1f} | CHO {latest_cho:+,d})"
                action_rec = "⏸️ 45m 단기조정 (관망)"

            logger.info(f"[Intraday45mAnalyzer] {stock_code} 3~5영업일 45분봉 정밀분석 - ADX:{latest_adx:.1f}, OBV데드:{obv_dead_flag}, CHO유출({latest_cho}):{cho_dead_flag} ➔ 이탈:{is_45m_breakdown}")

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
                "is_45m_breakdown": is_45m_breakdown,
                "is_45m_weak": is_45m_weak,
                "signal_45m_text": sig_text,
                "action_45m_recommendation": action_rec
            }

        except Exception as e:
            logger.error(f"[Intraday45mAnalyzer] {stock_code} 45분봉 분석 예외: {e}", exc_info=True)
            return default_res
