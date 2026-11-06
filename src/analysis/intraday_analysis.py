import requests
import math
import threading
import pandas as pd
import numpy as np
import yfinance as yf
from typing import Dict, Any, List, Optional, Tuple
from src.utils.logger import logger

_NAVER_HTTP_LOCAL = threading.local()


def _naver_http_get(url: str, *, headers: Dict[str, str], timeout: float) -> requests.Response:
    if getattr(requests.get, "__module__", "") != "requests.api":
        return requests.get(url, headers=headers, timeout=timeout)
    session = getattr(_NAVER_HTTP_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        _NAVER_HTTP_LOCAL.session = session
    return session.get(url, headers=headers, timeout=timeout)


def _compute_naver_bar_volumes(df: pd.DataFrame, cum_col: str = "AccumulatedVolume") -> pd.Series:
    """Convert Naver cumulative daily volume into per-bar volume safely."""
    if df.empty or cum_col not in df.columns:
        return pd.Series(dtype=float, index=df.index)
    volumes = pd.Series(0.0, index=df.index, dtype=float)
    for trading_day, group in df.groupby(df.index.normalize(), sort=True):
        if group.empty:
            continue
        if len(group) > 1:
            diffs = group.index.to_series().diff().dropna()
            pos_diffs = diffs[diffs > pd.Timedelta(0)]
            expected_step = pos_diffs.min() if not pos_diffs.empty else pd.Timedelta(minutes=1)
        else:
            expected_step = pd.Timedelta(minutes=1)

        day_open = pd.Timestamp(trading_day) + pd.Timedelta(hours=9)
        cum_vals = pd.to_numeric(group[cum_col], errors="coerce").to_numpy(dtype=float)
        stamps = group.index

        first_cum = float(cum_vals[0]) if len(cum_vals) else 0.0
        if stamps[0] <= day_open + expected_step and math.isfinite(first_cum) and first_cum >= 0.0:
            volumes.loc[stamps[0]] = first_cum
        else:
            volumes.loc[stamps[0]] = 0.0

        for idx in range(1, len(group)):
            time_gap = stamps[idx] - stamps[idx - 1]
            prev_val = float(cum_vals[idx - 1])
            curr_val = float(cum_vals[idx])
            raw_diff = curr_val - prev_val
            if (
                not math.isfinite(raw_diff)
                or raw_diff < 0.0
                or time_gap <= pd.Timedelta(0)
                or time_gap > expected_step
            ):
                volumes.loc[stamps[idx]] = 0.0
            else:
                volumes.loc[stamps[idx]] = raw_diff
    return volumes


class Intraday30mAnalyzer:
    """
    Phase 3.1: 30분봉 정밀 수급 및 전술 지표 연산기
    - 최근 3~5영업일(24~40개 30분봉) 데이터를 수집하여 표준 보조지표를 연산합니다:
      1) OBV 9봉 MA (13개 정규장 슬롯; 지표기간 9 유지)
      2) ADX 14 + DMI (+DI / -DI 방향성 결합)
      3) Chaikin Oscillator (13, 26)
      4) 30분봉 일목 구름대 (9, 26, 52)
    - Data Provenance (데이터 출처, 수집 봉 수, 마지막 타임스탬프, 에러 코드)를 완전 추적합니다.
    - 결측 시 [0, 0]이나 0으로 정상값을 위장하지 않고 명시적 None 및 에러 코드를 반환합니다.
    """

    def __init__(self):
        pass

    @staticmethod
    def calculate_canonical_30m_trend(df_30m: pd.DataFrame) -> Dict[str, Any]:
        """Calculate raw 30m ADX/DMI and pure Ichimoku price structure.

        This method intentionally does not include OBV in the cloud-breakdown
        result.  Existing composite fields remain defined by
        ``analyze_30m_indicators`` for backward compatibility.
        """
        if not isinstance(df_30m, pd.DataFrame) or len(df_30m) < 16:
            raise ValueError("INSUFFICIENT_COMPLETED_30M_BARS")
        required = {"High", "Low", "Close"}
        if not required.issubset(df_30m.columns):
            raise ValueError("MALFORMED_30M_OHLCV")

        high = df_30m["High"]
        low = df_30m["Low"]
        close = df_30m["Close"]
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        tr = np.maximum(
            high - low,
            np.maximum((high - close.shift(1)).abs(), (low - close.shift(1)).abs()),
        )
        tr_s = pd.Series(tr, index=df_30m.index).ewm(alpha=1 / 14, adjust=False).mean()
        plus_di_series = (
            100
            * pd.Series(plus_dm, index=df_30m.index).ewm(alpha=1 / 14, adjust=False).mean()
            / (tr_s + 1e-9)
        )
        minus_di_series = (
            100
            * pd.Series(minus_dm, index=df_30m.index).ewm(alpha=1 / 14, adjust=False).mean()
            / (tr_s + 1e-9)
        )
        dx = 100 * (plus_di_series - minus_di_series).abs() / (
            plus_di_series + minus_di_series + 1e-9
        )
        adx_series = dx.ewm(alpha=1 / 14, adjust=False).mean()

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
        cloud_bottom = float(np.minimum(span_a.iloc[-1], span_b.iloc[-1]))
        close_value = float(close.iloc[-1])

        return {
            "adx_14_30m": float(adx_series.iloc[-1]),
            "plus_di_30m": float(plus_di_series.iloc[-1]),
            "minus_di_30m": float(minus_di_series.iloc[-1]),
            "close_30m": close_value,
            "cloud_bottom_30m": cloud_bottom,
            "is_price_below_cloud_30m": bool(close_value < cloud_bottom),
        }

    def _fetch_from_yfinance(self, stock_code: str) -> Optional[Tuple[pd.DataFrame, str]]:
        # Read the original chart OHLCV without yfinance's live-row repair/merge.
        # The metadata watermark and rows come from this same HTTP response.
        for sym in [f"{stock_code}.KS", f"{stock_code}.KQ"]:
            try:
                response = _naver_http_get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=15m&range=5d",
                    headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
                response.raise_for_status()
                chart = response.json()["chart"]["result"][0]
                quote = chart["indicators"]["quote"][0]
                index = pd.to_datetime(chart["timestamp"],unit="s",utc=True).tz_convert("Asia/Seoul")
                frame = pd.DataFrame({key:quote[key.lower()] for key in ["Open","High","Low","Close","Volume"]},index=index).dropna()
                if len(frame)<15:continue
                watermark=chart["meta"].get("regularMarketTime")
                if not isinstance(watermark,(int,float)):continue
                frame.attrs["source_watermark"]=pd.Timestamp(watermark,unit="s",tz="UTC").tz_convert("Asia/Seoul").isoformat()
                frame.attrs["raw_ohlcv_unmodified"]=True
                return frame, f"YFINANCE_15M_RAW_CHART ({sym})"
            except Exception as exc:
                logger.debug(f"[M30] Yahoo raw chart {sym} unavailable: {type(exc).__name__}")
        return None

    def _fetch_from_naver_api(self, stock_code: str) -> Optional[Tuple[pd.DataFrame, str]]:
        """네이버 모바일/실시간 분봉 API를 통한 데이터 수집 (Secondary Fallback)"""
        code = str(stock_code).zfill(6)
        url = f"https://api.stock.naver.com/chart/domestic/item/{code}/minute?range=15m"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        try:
            r = _naver_http_get(url, headers=headers, timeout=5)
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
                            "AccumulatedVolume": float(item.get("accumulatedTradingVolume", 0)),
                        })
                    df = pd.DataFrame(rows).set_index("Datetime")
                    df = df[~df.index.duplicated(keep="last")].sort_index()
                    df["Volume"] = _compute_naver_bar_volumes(df, "AccumulatedVolume")
                    df = df[["Open", "High", "Low", "Close", "Volume"]]
                    return df, "NAVER_MINUTE_API"
        except Exception as e:
            logger.debug(f"[Intraday30mAnalyzer] Naver minute API {code} 수집 실패: {e}")
        return None

    def fetch_canonical_15m_data(self, stock_code: str) -> Tuple[Optional[pd.DataFrame], str, str]:
        from src.analysis.m30_kiwoom import fetch_canonical
        return fetch_canonical(str(stock_code))

    @staticmethod
    def observed_completed_30m_end(raw_df, source, now=None):
        """Return an end only when original source data prove its completion."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from src.runtime.krx_calendar import KRXCalendar
        from src.runtime.m30_contract import kst_datetime
        from src.analysis.m30_bars import prepare_completed_30m_data
        at=kst_datetime(now) if now is not None else datetime.now(ZoneInfo("Asia/Seoul")).replace(tzinfo=None)
        slot=KRXCalendar.get_completed_30m_bar(at)
        if slot is None: return None
        cutoff=at.replace(hour=int(slot[2][:2]),minute=int(slot[2][3:]),second=0,microsecond=0)
        frame,quality=prepare_completed_30m_data(raw_df,source,cutoff,allow_sparse_completed=True)
        if frame is None or quality!="VALID": return None
        return frame.index[-1].strftime("%Y-%m-%d %H:%M:%S")

    def analyze_30m_indicators(self, stock_code: str, asof_dt=None) -> Dict[str, Any]:
        """
        30분봉 정밀 수급 및 기술 지표 연산 + Data Provenance 메타데이터 생성
        """
        default_res = {
            "stock_code": stock_code,
            "has_30m_data": False,
            "completed_30m_timestamp": None,
            "intraday_source": "NONE",
            "intraday_row_count": 0,
            "intraday_last_timestamp": "N/A",
            "intraday_quality": "🔴 INVALID (분봉 데이터 미수집)",
            "intraday_error_code": "NO_INTRADAY_DATA",
            "adx_14_30m": None,
            "plus_di_30m": None,
            "minus_di_30m": None,
            "adx_di_dominance_30m": "N/A (데이터 결측)",
            "close_30m": None,
            "cloud_bottom_30m": None,
            "is_price_below_cloud_30m": None,
            "obv_30m": None,
            "obv_30m_trend": "N/A (데이터 결측)",
            "chaikin_osc_30m": None,
            "chaikin_flow_30m": "N/A (데이터 결측)",
            "intraday_cho_recent2": None,
            "intraday_cho_is_subzero_2bars": False,
            "intraday_cho_note": "",
            "is_30m_breakdown": False,
            "is_obv_dead": False,
            "is_cho_outflow": False,
            "is_30m_bearish_2plus": False,
            "signal_30m_text": "30분봉 데이터 결측",
            "action_30m_recommendation": ""
        }

        # 1. 15분봉 Canonical 데이터 수집
        df_15m, source_name, err_code = self.fetch_canonical_15m_data(stock_code)
        if df_15m is None or len(df_15m) < 15:
            default_res["intraday_source"] = source_name
            default_res["intraday_error_code"] = err_code if err_code != "NONE" else "INSUFFICIENT_BARS"
            default_res["intraday_quality"] = f"🔴 INVALID ({default_res['intraday_error_code']})"
            logger.warning(f"[Intraday30mAnalyzer] {stock_code} 15분봉 데이터 수집 실패 ({default_res['intraday_error_code']})")
            return default_res

        try:
            # 2. 30분봉 리샘플링 (1일 = 약 8~9개 30분봉)
            from src.analysis.m30_bars import completed_frame
            df_30m, frame_quality = completed_frame(df_15m, source_name, asof=asof_dt)
            if df_30m is None:
                default_res["intraday_error_code"] = frame_quality
                default_res["intraday_quality"] = "INVALID_30M_" + frame_quality
                return default_res
            default_res["bar_interval_minutes"] = 30
            default_res["timeframe_contract"] = "KRX_M30_V1"

            if len(df_30m) < 16: # 최소 2거래일(16봉) 이상 필요
                default_res["intraday_source"] = source_name
                default_res["intraday_row_count"] = len(df_30m)
                default_res["intraday_error_code"] = "INSUFFICIENT_BARS"
                default_res["intraday_quality"] = f"🟡 PARTIAL (30분봉 {len(df_30m)}봉 부족 - 최소 16봉 필요)"
                return default_res

            high = df_30m['High']
            low = df_30m['Low']
            close = df_30m['Close']
            vol = df_30m['Volume']

            last_ts = str(df_30m.index[-1])
            observed_end = self.observed_completed_30m_end(df_15m, source_name, now=asof_dt)

            # 3. 30분봉 DMI / ADX (14) 및 순수 가격구조 연산
            trend_metrics = self.calculate_canonical_30m_trend(df_30m)
            latest_adx = trend_metrics["adx_14_30m"]
            latest_plus_di = trend_metrics["plus_di_30m"]
            latest_minus_di = trend_metrics["minus_di_30m"]

            di_dom_str = f"+DI: {latest_plus_di:.1f} / -DI: {latest_minus_di:.1f} (ADX {latest_adx:.1f}, {'+DI우세' if latest_plus_di >= latest_minus_di else '-DI우세'})"

            # 4. 30분봉 OBV (9) 및 데드크로스 연산
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

            # 5. 30분봉 Chaikin Oscillator (13, 26)
            hl_diff = high - low
            mfm = np.where(hl_diff == 0, 0, ((close - low) - (high - close)) / (hl_diff + 1e-9))
            mfv = mfm * vol
            adl_series = pd.Series(mfv, index=df_30m.index).cumsum()
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

            # 6. 기존 breakdown 의미 유지: 순수 구름 하단 이탈 AND 기존 EMA9 OBV dead
            is_price_below_cloud_30m = trend_metrics["is_price_below_cloud_30m"]
            is_30m_breakdown = is_price_below_cloud_30m and obv_dead_flag

            adx_bear_flag = (latest_adx >= 22.0) and (latest_minus_di > latest_plus_di)
            bearish_signals_count = sum([obv_dead_flag, cho_dead_flag, adx_bear_flag])
            is_30m_bearish_2plus = (bearish_signals_count >= 2)

            is_obv_dead = obv_dead_flag and not cho_dead_flag
            is_cho_outflow = cho_dead_flag and not obv_dead_flag

            logger.info(f"[Intraday30mAnalyzer] {stock_code} ({source_name}, {len(df_30m)}봉) 30분봉 정밀분석 완료 - ADX:{latest_adx:.1f}, OBV:{obv_trend_str}, CHO:{intraday_cho_recent2}")

            return {
                "stock_code": stock_code,
                "has_30m_data": True, "bar_interval_minutes": 30, "timeframe_contract": "KRX_M30_V1",
                "intraday_source": source_name,
                "intraday_row_count": len(df_30m),
                "intraday_last_timestamp": last_ts,
                "completed_30m_timestamp": observed_end,
                "intraday_quality": f"🟢 VALID ({len(df_30m)}봉 정상 산출)",
                "intraday_error_code": "NONE",
                "adx_14_30m": round(latest_adx, 1),
                "plus_di_30m": round(latest_plus_di, 1),
                "minus_di_30m": round(latest_minus_di, 1),
                "adx_di_dominance_30m": di_dom_str,
                "close_30m": round(trend_metrics["close_30m"], 2),
                "cloud_bottom_30m": round(trend_metrics["cloud_bottom_30m"], 2),
                "is_price_below_cloud_30m": is_price_below_cloud_30m,
                "obv_30m": int(obv_series.iloc[-1]),
                "obv_30m_trend": obv_trend_str,
                "chaikin_osc_30m": latest_cho,
                "chaikin_flow_30m": cho_flow_str,
                "intraday_cho_recent2": intraday_cho_recent2,
                "intraday_cho_is_subzero_2bars": intraday_cho_is_subzero_2bars,
                "intraday_cho_note": " (일봉+30분봉 이중확인)" if intraday_cho_is_subzero_2bars else "",
                "is_30m_breakdown": is_30m_breakdown,
                "is_obv_dead": is_obv_dead,
                "is_cho_outflow": is_cho_outflow,
                "is_30m_bearish_2plus": is_30m_bearish_2plus,
                "signal_30m_text": f"30m 분석 완료 (ADX {latest_adx:.1f} | CHO {latest_cho:+,d})",
                "action_30m_recommendation": "30m 정상 연산"
            }

        except Exception as e:
            logger.error(f"[Intraday30mAnalyzer] {stock_code} 지표 계산 중 예외: {e}", exc_info=True)
            default_res["intraday_source"] = source_name
            default_res["intraday_error_code"] = "INDICATOR_ERROR"
            default_res["intraday_quality"] = "🔴 INVALID (지표 계산 오류)"
            return default_res
