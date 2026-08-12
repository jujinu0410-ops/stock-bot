import pandas as pd
import numpy as np
from typing import Dict, Any, Tuple
from src.utils.logger import logger

def adjust_krx_tick_size(price: float, direction: str = "down") -> int:
    """
    한국거래소(KRX) 주식 호가단위 규칙에 맞게 가격을 보정합니다.
    - 2,000원 미만: 1원 단위
    - 2,000원 이상 ~ 5,000원 미만: 5원 단위
    - 5,000원 이상 ~ 20,000원 미만: 10원 단위
    - 20,000원 이상 ~ 50,000원 미만: 50원 단위
    - 50,000원 이상 ~ 200,000원 미만: 100원 단위
    - 200,000원 이상 ~ 500,000원 미만: 500원 단위
    - 500,000원 이상: 1,000원 단위
    """
    p = float(price)
    if p <= 0:
        return 0

    if p < 2000:
        unit = 1
    elif p < 5000:
        unit = 5
    elif p < 20000:
        unit = 10
    elif p < 50000:
        unit = 50
    elif p < 200000:
        unit = 100
    elif p < 500000:
        unit = 500
    else:
        unit = 1000

    if direction == "down":
        return int((p // unit) * unit)
    elif direction == "up":
        return int(((p + unit - 1) // unit) * unit)
    else:
        return int(round(p / unit) * unit)

class TechnicalAnalysis:
    """
    일봉 데이터를 기반으로 주요 보조지표를 산출하고 최근 3거래일 흐름을 종합하여 T_raw (-100~+100) 및 환산점수 T (0~100)를 산출합니다.
    """
    def __init__(self, daily_df: pd.DataFrame):
        """
        :param daily_df: 'stk_date', 'open_price', 'high_price', 'low_price', 'close_price', 'volume',
                         'foreign_net_buy', 'inst_net_buy' 컬럼을 포함하는 DataFrame (날짜 오름차순 정렬 필수)
        """
        self.df = daily_df.copy()
        if not self.df.empty:
            self.df['stk_date'] = pd.to_datetime(self.df['stk_date'])
            self.df = self.df.sort_values('stk_date').reset_index(drop=True)

    def calculate_indicators(self) -> pd.DataFrame:
        """주요 보조지표 연산 (일목균형표 9/20/50, VWAP 9/26, BB 26/1.7, DMI/ADX 14, OBV, Chaikin 13/26)"""
        df = self.df.copy()
        if len(df) < 10:
            logger.warning("일봉 데이터 수량이 부족하여 보조지표 연산을 정밀히 수행할 수 없습니다.")
            return df

        # 1. 일목균형표 (9, 20, 50)
        high_9 = df['high_price'].rolling(window=9).max()
        low_9 = df['low_price'].rolling(window=9).min()
        df['tenkan_sen'] = (high_9 + low_9) / 2.0  # 전환선

        high_20 = df['high_price'].rolling(window=20).max()
        low_20 = df['low_price'].rolling(window=20).min()
        df['kijun_sen'] = (high_20 + low_20) / 2.0  # 기준선

        df['senkou_span_a'] = (df['tenkan_sen'] + df['kijun_sen']) / 2.0  # 선행스팬1
        high_50 = df['high_price'].rolling(window=50).max()
        low_50 = df['low_price'].rolling(window=50).min()
        df['senkou_span_b'] = (high_50 + low_50) / 2.0  # 선행스팬2

        # 2. VWAP (9, 26)
        typical_price = (df['high_price'] + df['low_price'] + df['close_price']) / 3.0
        tp_vol = typical_price * df['volume']
        df['vwap_9'] = tp_vol.rolling(window=9).sum() / (df['volume'].rolling(window=9).sum() + 1e-9)
        df['vwap_26'] = tp_vol.rolling(window=26).sum() / (df['volume'].rolling(window=26).sum() + 1e-9)

        # 3. 볼린저 밴드 (26, 1.7)
        ma_26 = df['close_price'].rolling(window=26).mean()
        std_26 = df['close_price'].rolling(window=26).std()
        df['bb_middle'] = ma_26
        df['bb_upper'] = ma_26 + (1.7 * std_26)
        df['bb_lower'] = ma_26 - (1.7 * std_26)

        # 4. DMI / ADX (14)
        up_move = df['high_price'].diff()
        down_move = -df['low_price'].diff()

        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        tr1 = df['high_price'] - df['low_price']
        tr2 = (df['high_price'] - df['close_price'].shift(1)).abs()
        tr3 = (df['low_price'] - df['close_price'].shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        tr_smooth = tr.rolling(window=14).sum()
        plus_di = 100 * (pd.Series(plus_dm).rolling(window=14).sum() / (tr_smooth + 1e-9))
        minus_di = 100 * (pd.Series(minus_dm).rolling(window=14).sum() / (tr_smooth + 1e-9))
        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9))

        df['plus_di'] = plus_di
        df['minus_di'] = minus_di
        df['adx'] = dx.rolling(window=14).mean()

        # 5. OBV (On Balance Volume)
        price_diff = df['close_price'].diff()
        obv_direction = np.where(price_diff > 0, 1, np.where(price_diff < 0, -1, 0))
        df['obv'] = (obv_direction * df['volume']).cumsum()
        df['obv_ma20'] = df['obv'].rolling(window=20).mean()

        # 6. 채킨 오실레이터 (13, 26)
        mfm = ((df['close_price'] - df['low_price']) - (df['high_price'] - df['close_price'])) / (df['high_price'] - df['low_price'] + 1e-9)
        mfv = mfm * df['volume']
        adl = mfv.cumsum()
        chaikin_fast = adl.ewm(span=13, adjust=False).mean()
        chaikin_slow = adl.ewm(span=26, adjust=False).mean()
        df['chaikin_osc'] = chaikin_fast - chaikin_slow

        # 7. ATR (Average True Range, 14일 - 키움 HTS/MTS 영웅문 표준 Wilder's EWM 적용)
        tr1 = df['high_price'] - df['low_price']
        tr2 = (df['high_price'] - df['close_price'].shift(1)).abs()
        tr3 = (df['low_price'] - df['close_price'].shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['atr_14'] = tr.ewm(alpha=1.0/14.0, adjust=False).mean()
        df['atr_pct'] = (df['atr_14'] / (df['close_price'] + 1e-9)) * 100.0

        return df

    def evaluate_signals(self) -> Dict[str, Any]:
        """
        최근 3거래일 데이터를 평가하여 T_raw (-100~+100), T 점수 (0~100) 및 ATR 트레일링 지표 산출
        """
        if len(self.df) < 5:
            logger.warning("기술적 분석을 위한 일봉 데이터 수가 극히 부족합니다 (최소 5봉 이상 필요).")
            return {
                "t_raw": 0.0,
                "t_score": 50.0,
                "tech_completeness": 0.0,
                "atr_14": 0.0,
                "atr_pct": 0.0,
                "trailing_buy_price": 0,
                "trailing_stop_price": 0,
                "trailing_target_price": 0,
                "reason": "일봉 데이터 부족 (5봉 미만)"
            }

        df_calc = self.calculate_indicators()
        recent_3 = df_calc.tail(3)
        last_row = recent_3.iloc[-1]
        prev_row = recent_3.iloc[-2] if len(recent_3) >= 2 else last_row

        item_weights = {
            "ichimoku": 20.0,
            "vwap": 15.0,
            "bollinger": 15.0,
            "dmi_adx": 15.0,
            "obv": 10.0,
            "chaikin": 10.0,
            "supply_demand": 15.0
        }

        total_max_weight = sum(item_weights.values())  # 100.0
        valid_weight = 0.0
        earned_raw_points = 0.0
        reasons = []

        # 1. 일목균형표 점수 산출 (20점 만점 - 5단계: 20, 16, 12, 8, 4)
        if not pd.isna(last_row.get('senkou_span_a')) and not pd.isna(last_row.get('senkou_span_b')):
            valid_weight += item_weights['ichimoku']
            cloud_top = max(last_row['senkou_span_a'], last_row['senkou_span_b'])
            cloud_bottom = min(last_row['senkou_span_a'], last_row['senkou_span_b'])
            tenkan = last_row.get('tenkan_sen', 0)
            kijun = last_row.get('kijun_sen', 0)
            close = last_row['close_price']

            if close > cloud_top and tenkan > kijun:
                earned_raw_points += 20.0  # 1등급
                reasons.append("일목 1등급(20점: 구름대 상단 돌파 & 전환>기준)")
            elif close > cloud_top or tenkan > kijun:
                earned_raw_points += 16.0  # 2등급
                reasons.append("일목 2등급(16점: 구름대 상단 상회 또는 전환>기준)")
            elif close >= cloud_bottom:
                earned_raw_points += 12.0  # 3등급
                reasons.append("일목 3등급(12점: 구름대 내부 공방)")
            elif close < cloud_bottom and tenkan < kijun:
                earned_raw_points += 4.0   # 5등급
                reasons.append("일목 5등급(4점: 구름대 하단 이탈 & 역배열 심화)")
            else:
                earned_raw_points += 8.0   # 4등급
                reasons.append("일목 4등급(8점: 구름대 하단 이탈)")
        else:
            earned_raw_points += 10.0
            reasons.append("일목 3등급(10점: 데이터 수량 부족 중립)")

        # 2. VWAP 점수 산출 (15점 만점 - 5단계: 15, 12, 9, 6, 3)
        if not pd.isna(last_row.get('vwap_9')) and not pd.isna(last_row.get('vwap_26')):
            valid_weight += item_weights['vwap']
            close = last_row['close_price']
            v9 = last_row['vwap_9']
            v26 = last_row['vwap_26']

            if close > v9 >= v26:
                earned_raw_points += 15.0  # 1등급
                reasons.append("VWAP 1등급(15점: 단기/중기 VWAP 우상향 정배열)")
            elif close > v9:
                earned_raw_points += 12.0  # 2등급
                reasons.append("VWAP 2등급(12점: 단기 VWAP 상회)")
            elif abs(close - v9) / v9 <= 0.01:
                earned_raw_points += 9.0   # 3등급
                reasons.append("VWAP 3등급(9점: VWAP 평형 유지)")
            elif close < v9 < v26:
                earned_raw_points += 3.0   # 5등급
                reasons.append("VWAP 5등급(3점: VWAP 하회 역배열)")
            else:
                earned_raw_points += 6.0   # 4등급
                reasons.append("VWAP 4등급(6점: 단기 VWAP 하회)")
        else:
            earned_raw_points += 7.5
            reasons.append("VWAP 3등급(7.5점: 데이터 미비 중립)")

        # 3. 볼린저밴드 점수 산출 (15점 만점 - 5단계: 15, 12, 9, 6, 3)
        if not pd.isna(last_row.get('bb_upper')) and not pd.isna(last_row.get('bb_lower')):
            valid_weight += item_weights['bollinger']
            close = last_row['close_price']
            upper = last_row['bb_upper']
            middle = last_row['bb_middle']
            lower = last_row['bb_lower']

            if close >= upper:
                earned_raw_points += 15.0  # 1등급
                reasons.append("볼린저 1등급(15점: 상단밴드 돌파/강한 상승파동)")
            elif close > middle:
                earned_raw_points += 12.0  # 2등급
                reasons.append("볼린저 2등급(12점: 중심선~상단밴드 위치)")
            elif abs(close - middle) / middle <= 0.01:
                earned_raw_points += 9.0   # 3등급
                reasons.append("볼린저 3등급(9점: 중심선 수렴)")
            elif close <= lower:
                earned_raw_points += 3.0   # 5등급
                reasons.append("볼린저 5등급(3점: 하단밴드 이탈)")
            else:
                earned_raw_points += 6.0   # 4등급
                reasons.append("볼린저 4등급(6점: 하단밴드~중심선 위치)")
        else:
            earned_raw_points += 7.5
            reasons.append("볼린저 3등급(7.5점: 데이터 미비 중립)")

        # 4. DMI / ADX 점수 산출 (15점 만점 - 5단계: 15, 12, 9, 6, 3)
        if not pd.isna(last_row.get('plus_di')) and not pd.isna(last_row.get('adx')):
            valid_weight += item_weights['dmi_adx']
            p_di = last_row['plus_di']
            m_di = last_row['minus_di']
            adx = last_row['adx']

            if p_di > m_di and adx >= 25:
                earned_raw_points += 15.0  # 1등급
                reasons.append("DMI 1등급(15점: DI+ 우위 및 ADX 추세강화)")
            elif p_di > m_di and adx >= 20:
                earned_raw_points += 12.0  # 2등급
                reasons.append("DMI 2등급(12점: DI+ 우위 상승진입)")
            elif abs(p_di - m_di) <= 3:
                earned_raw_points += 9.0   # 3등급
                reasons.append("DMI 3등급(9점: 팽팽한 혼조세)")
            elif p_di < m_di and adx >= 25:
                earned_raw_points += 3.0   # 5등급
                reasons.append("DMI 5등급(3점: DI- 우위 및 강한 하락추세)")
            else:
                earned_raw_points += 6.0   # 4등급
                reasons.append("DMI 4등급(6점: DI- 우위 하락세)")
        else:
            earned_raw_points += 7.5
            reasons.append("DMI 3등급(7.5점: 데이터 미비 중립)")

        # 5. OBV 점수 산출 (10점 만점 - 5단계: 10, 8, 6, 4, 2)
        if not pd.isna(last_row.get('obv_ma20')):
            valid_weight += item_weights['obv']
            obv = last_row['obv']
            obv_ma = last_row['obv_ma20']

            if obv > obv_ma and obv > prev_row.get('obv', obv):
                earned_raw_points += 10.0  # 1등급
                reasons.append("OBV 1등급(10점: OBV 20일 이평 상회 & 우상향)")
            elif obv > obv_ma:
                earned_raw_points += 8.0   # 2등급
                reasons.append("OBV 2등급(8점: OBV 20일 이평 상회)")
            elif abs(obv - obv_ma) / (abs(obv_ma) + 1e-9) <= 0.02:
                earned_raw_points += 6.0   # 3등급
                reasons.append("OBV 3등급(6점: OBV 이평 수렴)")
            elif obv < obv_ma and obv < prev_row.get('obv', obv):
                earned_raw_points += 2.0   # 5등급
                reasons.append("OBV 5등급(2점: OBV 이평 하회 & 연속 유출)")
            else:
                earned_raw_points += 4.0   # 4등급
                reasons.append("OBV 4등급(4점: OBV 이평 하회)")
        else:
            earned_raw_points += 5.0
            reasons.append("OBV 3등급(5점: 데이터 미비 중립)")

        # 6. 채킨 오실레이터 점수 산출 (10점 만점 - 5단계: 10, 8, 6, 4, 2)
        if not pd.isna(last_row.get('chaikin_osc')):
            valid_weight += item_weights['chaikin']
            ch = last_row['chaikin_osc']

            if ch > 1000:
                earned_raw_points += 10.0  # 1등급
                reasons.append("채킨 1등급(10점: 매수 유입 급증)")
            elif ch > 0:
                earned_raw_points += 8.0   # 2등급
                reasons.append("채킨 2등급(8점: 매수세 우위)")
            elif abs(ch) <= 100:
                earned_raw_points += 6.0   # 3등급
                reasons.append("채킨 3등급(6점: 평형 유입)")
            elif ch < -1000:
                earned_raw_points += 2.0   # 5등급
                reasons.append("채킨 5등급(2점: 매도 유출 심화)")
            else:
                earned_raw_points += 4.0   # 4등급
                reasons.append("채킨 4등급(4점: 매도세 우위)")
        else:
            earned_raw_points += 5.0
            reasons.append("채킨 3등급(5점: 데이터 미비 중립)")

        # 7. 외국인/기관 3일 수급 세분화 (15점 만점 - 5단계: 15, 12, 9, 6, 3)
        supply_demand_pass = False
        if 'foreign_net_buy' in recent_3.columns and 'inst_net_buy' in recent_3.columns:
            valid_weight += item_weights['supply_demand']
            f_buy = recent_3['foreign_net_buy'].tolist()
            i_buy = recent_3['inst_net_buy'].tolist()

            f_consec_buy = all(x > 0 for x in f_buy)
            i_consec_buy = all(x > 0 for x in i_buy)
            f_consec_sell = all(x < 0 for x in f_buy)
            i_consec_sell = all(x < 0 for x in i_buy)

            if f_consec_buy and i_consec_buy:
                earned_raw_points += 15.0  # 1등급: 3일 연속 동반 매수
                supply_demand_pass = True
                reasons.append("수급 1등급(15점: 외국인/기관 3일 연속 동반 쌍쓸이 매수)")
            elif sum(f_buy) > 0 and sum(i_buy) > 0:
                earned_raw_points += 12.0  # 2등급: 양 주체 모두 3일 누적 순매수 양수
                supply_demand_pass = True
                reasons.append("수급 2등급(12점: 외국인/기관 3일 누적 동반 순매수 양수)")
            elif f_consec_buy or i_consec_buy:
                earned_raw_points += 9.0   # 3등급: 한 주체만 3일 연속 순매수
                reasons.append("수급 3등급(9점: 외국인 또는 기관 3일 연속 매수 우세)")
            elif f_consec_sell and i_consec_sell:
                earned_raw_points += 3.0   # 5등급: 외국인/기관 3일 연속 동반 매도 폭탄
                reasons.append("수급 5등급(3점: 외국인/기관 3일 연속 동반 매도 폭탄)")
            else:
                earned_raw_points += 6.0   # 4등급: 수급 혼조/약세
                reasons.append("수급 4등급(6점: 수급 약세 지속)")
        else:
            earned_raw_points += 7.5
            reasons.append("수급 3등급(7.5점: 수급 데이터 미비 중립)")

        # 데이터 완성도 (%)
        tech_completeness = round((valid_weight / total_max_weight) * 100.0, 2)

        # 5단계 등간격 직접 합산 T 점수 (0 ~ 100점)
        t_score = round(max(0.0, min(100.0, earned_raw_points)), 1)
        t_raw = t_score

        # 8. ATR 수치 및 KRX 호가단위 보정 트레일링 가격 산출
        atr_14 = float(last_row.get('atr_14', 0.0) or 0.0)
        close_p = float(last_row.get('close_price', 0.0) or 0.0)
        if pd.isna(atr_14) or atr_14 <= 0:
            atr_14 = close_p * 0.03

        atr_pct = round((atr_14 / (close_p + 1e-9)) * 100.0, 2)
        
        # 계산상의 트레일링 가격
        raw_trailing_buy_p = close_p - (1.5 * atr_14)
        raw_trailing_stop_p = close_p - (2.0 * atr_14)
        raw_trailing_target_p = close_p + (2.5 * atr_14)

        # 키움 호가단위 보정 가격
        kiwoom_buy_tick_p = adjust_krx_tick_size(raw_trailing_buy_p, "down")
        kiwoom_stop_tick_p = adjust_krx_tick_size(raw_trailing_stop_p, "down")
        kiwoom_target_tick_p = adjust_krx_tick_size(raw_trailing_target_p, "up")

        return {
            "t_raw": t_raw,
            "t_score": t_score,
            "tech_completeness": tech_completeness,
            "atr_14": round(atr_14, 1),
            "atr_pct": atr_pct,
            "trailing_buy_price": int(round(raw_trailing_buy_p)),
            "trailing_stop_price": int(round(raw_trailing_stop_p)),
            "trailing_target_price": int(round(raw_trailing_target_p)),
            "kiwoom_buy_tick_price": kiwoom_buy_tick_p,
            "kiwoom_stop_tick_price": kiwoom_stop_tick_p,
            "kiwoom_target_tick_price": kiwoom_target_tick_p,
            "supply_demand_pass": supply_demand_pass,
            "reason": " / ".join(reasons) if reasons else "보통 범위 흐름"
        }
