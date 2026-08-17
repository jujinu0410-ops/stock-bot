import requests
import pandas as pd
import numpy as np
import yfinance as yf
from typing import Dict, Any, List, Optional, Tuple
from src.utils.logger import logger

class Intraday45mAnalyzer:
    """
    Phase 3.1: 45분봉 정밀 수급 및 전술 지표 연산기
    - 최근 3~5영업일(24~40개 45분봉) 데이터를 수집하여 표준 보조지표를 연산합니다:
      1) OBV 9봉 MA (1거래일 9개 45분봉 기준)
      2) ADX 14 + DMI (+DI / -DI 방향성 결합)
      3) Chaikin Oscillator (13, 26)
      4) 45분봉 일목 구름대 (9, 26, 52)
    - Data Provenance (데이터 출처, 수집 봉 수, 마지막 타임스탬프, 에러 코드)를 완전 추적합니다.
    - 결측 시 [0, 0]이나 0으로 정상값을 위장하지 않고 명시적 None 및 에러 코드를 반환합니다.
    """

    def __init__(self):
        pass

    def _fetch_from_yfinance(self, stock_code: str) -> Optional[Tuple[pd.DataFrame, str]]:
        """yfinance를 통한 15분봉 데이터 수집 (KS / KQ 순차 시도)"""
        code = str(stock_code).zfill(6)
        candidates = [f"{code}.KS", f"{code}.KQ"]

        for sym in candidates:
            try:
                ticker = yf.Ticker(sym)
                df_15m = ticker.history(interval="15m", period="5d")
                if not df_15m.empty and len(df_15m) >= 15:
                    df_clean = df_15m[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
                    if len(df_clean) >= 15:
                        return df_clean, f"YFINANCE_15M ({sym})"
            except Exception as e:
                logger.debug(f"[Intraday45mAnalyzer] yfinance {sym} 수집 실패: {e}")
        return None

    def _fetch_from_naver_api(self, stock_code: str) -> Optional[Tuple[pd.DataFrame, str]]:
        """네이버 모바일/실시간 분봉 API를 통한 데이터 수집 (Secondary Fallback)"""
        code = str(stock_code).zfill(6)
        url = f"https://api.stock.naver.com/chart/domestic/item/{code}/minute?range=15m"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        try:
            r = requests.get(url, headers=headers, timeout=5)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and len(data) >= 15:
                    rows = []
                    for item in data:
                        dt_str = item.get("localDateTime")
                        dt = pd.to_datetime(dt_str, format="%Y%m%d%H%M%S")
                        rows.append({
                            "Datetime": dt,
                            "Open": float(item.get("openPrice")),
                            "High": float(item.get("highPrice")),
                            "Low": float(item.get("lowPrice")),
                            "Close": float(item.get("currentPrice")),
                            "Volume": float(item.get("accumulatedTradingVolume", 0))
                        })
                    df = pd.DataFrame(rows).set_index("Datetime").sort_index()
                    return df, "NAVER_MINUTE_API"
        except Exception as e:
            logger.debug(f"[Intraday45mAnalyzer] Naver minute API {code} 수집 실패: {e}")
        return None

    def fetch_canonical_15m_data(self, stock_code: str) -> Tuple[Optional[pd.DataFrame], str, str]:
        """
        다중 소스(yfinance -> Naver API)로부터 표준 15분봉 OHLCV 데이터를 수집합니다.
        Returns: (df_15m, source_name, error_code)
        """
        # 1차 시도: yfinance
        res = self._fetch_from_yfinance(stock_code)
        if res is not None:
            return res[0], res[1], "NONE"

        # 2차 시도: Naver API
        res = self._fetch_from_naver_api(stock_code)
        if res is not None:
            return res[0], res[1], "NONE"

        return None, "NONE", "NO_INTRADAY_DATA"

    def analyze_45m_indicators(self, stock_code: str) -> Dict[str, Any]:
        """
        45분봉 정밀 수급 및 기술 지표 연산 + Data Provenance 메타데이터 생성
        """
        default_res = {
            "stock_code": stock_code,
            "has_45m_data": False,
            "intraday_source": "NONE",
            "intraday_row_count": 0,
            "intraday_last_timestamp": "N/A",
            "intraday_quality": "🔴 INVALID (분봉 데이터 미수집)",
            "intraday_error_code": "NO_INTRADAY_DATA",
            "adx_14_45m": None,
            "plus_di_45m": None,
            "minus_di_45m": None,
            "adx_di_dominance_45m": "N/A (데이터 결측)",
            "obv_45m": None,
            "obv_45m_trend": "N/A (데이터 결측)",
            "chaikin_osc_45m": None,
            "chaikin_flow_45m": "N/A (데이터 결측)",
            "intraday_cho_recent2": None,
            "intraday_cho_is_subzero_2bars": False,
            "intraday_cho_note": "",
            "is_45m_breakdown": False,
            "is_obv_dead": False,
            "is_cho_outflow": False,
            "is_45m_bearish_2plus": False,
            "signal_45m_text": "45분봉 데이터 결측",
            "action_45m_recommendation": ""
        }

        # 1. 15분봉 Canonical 데이터 수집
        df_15m, source_name, err_code = self.fetch_canonical_15m_data(stock_code)
        if df_15m is None or len(df_15m) < 15:
            default_res["intraday_source"] = source_name
            default_res["intraday_error_code"] = err_code if err_code != "NONE" else "INSUFFICIENT_BARS"
            default_res["intraday_quality"] = f"🔴 INVALID ({default_res['intraday_error_code']})"
            logger.warning(f"[Intraday45mAnalyzer] {stock_code} 15분봉 데이터 수집 실패 ({default_res['intraday_error_code']})")
            return default_res

        try:
            # 2. 45분봉 리샘플링 (1일 = 약 8~9개 45분봉)
            df_45m = df_15m.resample('45min').agg({
                'Open': 'first',
                'High': 'max',
                'Low': 'min',
                'Close': 'last',
                'Volume': 'sum'
            }).dropna()

            if len(df_45m) < 16: # 최소 2거래일(16봉) 이상 필요
                default_res["intraday_source"] = source_name
                default_res["intraday_row_count"] = len(df_45m)
                default_res["intraday_error_code"] = "INSUFFICIENT_BARS"
                default_res["intraday_quality"] = f"🟡 PARTIAL (45분봉 {len(df_45m)}봉 부족 - 최소 16봉 필요)"
                return default_res

            high = df_45m['High']
            low = df_45m['Low']
            close = df_45m['Close']
            vol = df_45m['Volume']

            last_ts = str(df_45m.index[-1])

            # 3. 45분봉 DMI / ADX (14) 연산
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

            latest_adx = float(adx_series.iloc[-1])
            latest_plus_di = float(plus_di_series.iloc[-1])
            latest_minus_di = float(minus_di_series.iloc[-1])

            di_dom_str = f"+DI: {latest_plus_di:.1f} / -DI: {latest_minus_di:.1f} (ADX {latest_adx:.1f}, {'+DI우세' if latest_plus_di >= latest_minus_di else '-DI우세'})"

            # 4. 45분봉 OBV (9) 및 데드크로스 연산
            obv_series = (np.sign(close.diff()) * vol).fillna(0).cumsum()
            obv_ema9 = obv_series.ewm(span=9, adjust=False).mean()

            recent_9_obv = obv_series.iloc[-9:]
            recent_9_ema = obv_ema9.iloc[-9:]

            is_below_ema = bool(obv_series.iloc[-1] < obv_ema9.iloc[-1])
            dead_bars_count = int((recent_9_obv < recent_9_ema).sum())
            is_obv_falling = bool(obv_series.iloc[-1] < obv_series.iloc[-9])

            obv_dead_flag = is_below_ema and (dead_bars_count >= 5 or is_obv_falling)

            if obv_dead_flag:
                obv_trend_str = "📉 OBV(9) 데드크로스 (이탈)"
            elif is_below_ema:
                obv_trend_str = "⚠️ OBV(9) 조정 약세"
            else:
                obv_trend_str = "📈 OBV(9) 매집 유지 (상승)"

            # 5. 45분봉 Chaikin Oscillator (13, 26)
            hl_diff = high - low
            mfm = np.where(hl_diff == 0, 0, ((close - low) - (high - close)) / (hl_diff + 1e-9))
            mfv = mfm * vol
            adl_series = pd.Series(mfv, index=df_45m.index).cumsum()
            cho_series = adl_series.ewm(span=13, adjust=False).mean() - adl_series.ewm(span=26, adjust=False).mean()

            latest_cho = int(cho_series.iloc[-1])
            intraday_cho_recent2 = [int(cho_series.iloc[-2]), int(cho_series.iloc[-1])]
            intraday_cho_is_subzero_2bars = (intraday_cho_recent2[0] <= 0) and (intraday_cho_recent2[1] <= 0)

            recent_18_cho = cho_series.iloc[-18:]
            cho_negative_count = (recent_18_cho < 0).sum()
            cho_dead_flag = (latest_cho < 0) and (cho_negative_count >= 8)

            if cho_dead_flag:
                cho_flow_str = f"💸 CHO 자금유출 ({latest_cho:+,d})"
            elif latest_cho < 0:
                cho_flow_str = f"⚠️ CHO 자금약세 ({latest_cho:+,d})"
            else:
                cho_flow_str = f"💧 CHO 자금유입 ({latest_cho:+,d})"

            # 6. 45분봉 일목균형표 구름대 (9, 26, 52)
            high_9 = high.rolling(window=9).max()
            low_9 = low.rolling(window=9).min()
            tenkan = (high_9 + low_9) / 2.0

            high_26 = high.rolling(window=26).max()
            low_26 = low.rolling(window=26).min()
            kijun = (high_26 + low_26) / 2.0

            span_a = (tenkan + kijun) / 2.0
            high_52 = high.rolling(window=52).max() if len(high) >= 52 else high.expanding().max()
            low_52 = low.rolling(window=52).min() if len(low) >= 52 else low.expanding().min()
            span_b = (high_52 + low_52) / 2.0

            cloud_bottom = np.minimum(span_a.iloc[-1], span_b.iloc[-1])
            is_45m_breakdown = bool(close.iloc[-1] < cloud_bottom) and obv_dead_flag

            adx_bear_flag = (latest_adx >= 22.0) and (latest_minus_di > latest_plus_di)
            bearish_signals_count = sum([obv_dead_flag, cho_dead_flag, adx_bear_flag])
            is_45m_bearish_2plus = (bearish_signals_count >= 2)

            is_obv_dead = obv_dead_flag and not cho_dead_flag
            is_cho_outflow = cho_dead_flag and not obv_dead_flag

            logger.info(f"[Intraday45mAnalyzer] {stock_code} ({source_name}, {len(df_45m)}봉) 45분봉 정밀분석 완료 - ADX:{latest_adx:.1f}, OBV:{obv_trend_str}, CHO:{intraday_cho_recent2}")

            return {
                "stock_code": stock_code,
                "has_45m_data": True,
                "intraday_source": source_name,
                "intraday_row_count": len(df_45m),
                "intraday_last_timestamp": last_ts,
                "intraday_quality": f"🟢 VALID ({len(df_45m)}봉 정상 산출)",
                "intraday_error_code": "NONE",
                "adx_14_45m": round(latest_adx, 1),
                "plus_di_45m": round(latest_plus_di, 1),
                "minus_di_45m": round(latest_minus_di, 1),
                "adx_di_dominance_45m": di_dom_str,
                "obv_45m": int(obv_series.iloc[-1]),
                "obv_45m_trend": obv_trend_str,
                "chaikin_osc_45m": latest_cho,
                "chaikin_flow_45m": cho_flow_str,
                "intraday_cho_recent2": intraday_cho_recent2,
                "intraday_cho_is_subzero_2bars": intraday_cho_is_subzero_2bars,
                "intraday_cho_note": " (일봉+45분봉 이중확인)" if intraday_cho_is_subzero_2bars else "",
                "is_45m_breakdown": is_45m_breakdown,
                "is_obv_dead": is_obv_dead,
                "is_cho_outflow": is_cho_outflow,
                "is_45m_bearish_2plus": is_45m_bearish_2plus,
                "signal_45m_text": f"45m 분석 완료 (ADX {latest_adx:.1f} | CHO {latest_cho:+,d})",
                "action_45m_recommendation": "45m 정상 연산"
            }

        except Exception as e:
            logger.error(f"[Intraday45mAnalyzer] {stock_code} 지표 계산 중 예외: {e}", exc_info=True)
            default_res["intraday_source"] = source_name
            default_res["intraday_error_code"] = "INDICATOR_ERROR"
            default_res["intraday_quality"] = "🔴 INVALID (지표 계산 오류)"
            return default_res
