import requests
import pandas as pd
import numpy as np
import yfinance as yf
from typing import Dict, Any, List, Optional
from src.utils.logger import logger

class Intraday45mAnalyzer:
    """
    보유 종목의 45분봉(Intraday 45-minute) 데이터를 수집하고
    ADX(14), OBV, Chaikin Oscillator 지표를 산출하는 정밀 분석기입니다.
    """
    def __init__(self):
        pass

    def get_symbol_ticker(self, stock_code: str) -> str:
        """KRX 주식 코드를 yfinance 코드로 변환 (기본 .KS, KOSDAQ 고려)"""
        code = str(stock_code).zfill(6)
        # 대표 코스닥 종목 맵핑
        kosdaq_codes = {'047770', '055490', '140670', '206650', '234920', '241520', '348340'}
        if code in kosdaq_codes:
            return f"{code}.KQ"
        return f"{code}.KS"

    def analyze_45m_indicators(self, stock_code: str) -> Dict[str, Any]:
        """
        특정 종목의 45분봉 ADX, OBV, Chaikin Oscillator 지표 수집 및 정밀 분석
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
            "signal_45m_text": "45분봉 데이터 대기"
        }

        try:
            # 1차 시도: yfinance로 15분봉 60일치 수집
            ticker = yf.Ticker(symbol)
            df_15m = ticker.history(interval="15m", period="60d")
            
            # 실패 시 .KQ / .KS 교대 시도
            if df_15m.empty or len(df_15m) < 15:
                alt_symbol = f"{stock_code}.KQ" if symbol.endswith(".KS") else f"{stock_code}.KS"
                df_15m = yf.Ticker(alt_symbol).history(interval="15m", period="60d")

            if df_15m.empty or len(df_15m) < 15:
                logger.warning(f"[Intraday45mAnalyzer] {stock_code} 15분봉 데이터 수집 실패")
                return default_res

            # 2. 15분봉 3개 묶음 ➔ 45분봉 리샘플링 (Open: first, High: max, Low: min, Close: last, Volume: sum)
            df_45m = df_15m.resample('45min').agg({
                'Open': 'first',
                'High': 'max',
                'Low': 'min',
                'Close': 'last',
                'Volume': 'sum'
            }).dropna()

            if len(df_45m) < 14:
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

            # 4. 45분봉 OBV 및 20봉 기울기 연산
            obv_series = (np.sign(close.diff()) * vol).fillna(0).cumsum()
            obv_ma20 = obv_series.rolling(20).mean()
            obv_trend_val = obv_series.iloc[-1] - obv_ma20.iloc[-1] if len(obv_ma20) >= 20 else 0

            if obv_trend_val > 0:
                obv_trend_str = "📈 매집 우위 (상승)"
            elif obv_trend_val < 0:
                obv_trend_str = "📉 이탈 우위 (하락)"
            else:
                obv_trend_str = "⏸️ 관망 (중립)"

            # 5. 45분봉 Chaikin Oscillator (3-EMA of MFV - 10-EMA of MFV)
            hl_diff = high - low
            mfm = np.where(hl_diff == 0, 0, ((close - low) - (high - close)) / hl_diff)
            mfv = mfm * vol
            cho_series = pd.Series(mfv, index=df_45m.index).ewm(span=3, adjust=False).mean() - pd.Series(mfv, index=df_45m.index).ewm(span=10, adjust=False).mean()

            latest_adx = float(adx_series.iloc[-1])
            latest_plus_di = float(plus_di_series.iloc[-1])
            latest_minus_di = float(minus_di_series.iloc[-1])
            latest_obv = int(obv_series.iloc[-1])
            latest_cho = int(cho_series.iloc[-1])

            if latest_cho > 0:
                cho_flow_str = "💧 자금 유입 (+) "
            else:
                cho_flow_str = "💸 자금 유출 (-)"

            # 종합 45분봉 신호 작성
            if latest_adx >= 25.0:
                if latest_plus_di > latest_minus_di:
                    sig_text = f"🟢 45m 강력 상방추세 (ADX {latest_adx:.1f} | CHO {latest_cho:+,d})"
                else:
                    sig_text = f"🚨 45m 강력 하방추세 (ADX {latest_adx:.1f} | CHO {latest_cho:+,d})"
            else:
                if latest_plus_di > latest_minus_di and latest_cho > 0:
                    sig_text = f"🟢 45m 매집 우상향 (ADX {latest_adx:.1f} | CHO {latest_cho:+,d})"
                elif latest_minus_di > latest_plus_di and latest_cho < 0:
                    sig_text = f"⚠️ 45m 조정/약세 (ADX {latest_adx:.1f} | CHO {latest_cho:+,d})"
                else:
                    sig_text = f"⏸️ 45m 횡보 횡보구간 (ADX {latest_adx:.1f} | CHO {latest_cho:+,d})"

            logger.info(f"[Intraday45mAnalyzer] {stock_code} 45분봉 지표 산출 완료 - ADX:{latest_adx:.1f}, OBV:{latest_obv:,}, CHO:{latest_cho:,}")

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
                "signal_45m_text": sig_text
            }

        except Exception as e:
            logger.error(f"[Intraday45mAnalyzer] {stock_code} 45분봉 산출 중 예외: {e}", exc_info=True)
            return default_res
