from typing import Dict, Any, Optional, List
from src.utils.logger import logger

class TechnicalGate:
    """
    Phase 3: 45분봉 Technical Gate Engine.
    - 45분봉 지표(ADX/DMI 14, OBV 9, Chaikin 13/26, 일목 구름대)를 활용하여
      기존 F/T 매수판정을 검증·세분화(Gate)하고 보유종목의 비중축소/관망 전술을 판정합니다.
    - 원칙:
      1) F/T 점수, 가중치, ATR Risk Engine, DB 로직 일체 불변.
      2) F/T OFF 종목은 절대 ON으로 승격하지 않음 (BUY_BLOCKED).
      3) Data Quality (VALID/PARTIAL/INVALID)와 TechnicalState는 절대 같은 필드로 통합하지 않음.
      4) 확정 손절가(S_final)를 대체하거나 낮추지 않음.
    """

    @staticmethod
    def evaluate_technical_state(
        data_quality: str,
        adx_14: float = 0.0,
        plus_di: float = 0.0,
        minus_di: float = 0.0,
        obv_trend_str: str = "",
        is_obv_dead: bool = False,
        is_obv_falling: bool = False,
        chaikin_val: float = 0.0,
        intraday_cho_recent2: Optional[List[int]] = None,
        is_cho_outflow: bool = False,
        is_cloud_breakdown: bool = False
    ) -> Dict[str, Any]:
        """
        45분봉 원천 지표로부터 TechnicalState(STRONG/NEUTRAL/WEAK/DAMAGED/UNKNOWN)를 판정합니다.
        """
        if intraday_cho_recent2 is None:
            intraday_cho_recent2 = [0, 0]

        # 1. 데이터 품질 결측/부족 검사
        if "INVALID" in data_quality:
            return {
                "technical_state": "UNKNOWN",
                "state_summary": "45분봉 데이터 미수집/오류",
                "reasons": ["데이터 결측으로 기술상태 판정 불가"]
            }

        if "PARTIAL" in data_quality and (adx_14 == 0.0 and plus_di == 0.0 and intraday_cho_recent2 == [0, 0]):
            return {
                "technical_state": "UNKNOWN",
                "state_summary": "45분봉 지표 계산용 데이터 부족",
                "reasons": ["분봉 수급 지표 산출 미완료"]
            }

        reasons = []

        # 2. 개별 지표 상방/하방 상태 평가
        # (1) DMI / ADX
        is_dmi_bull = (plus_di > minus_di)
        is_dmi_bear = (minus_di > plus_di)
        is_adx_strong_trend = (adx_14 >= 20.0)

        if is_dmi_bull:
            reasons.append(f"+DI 우위 ({plus_di:.1f} > {minus_di:.1f}, ADX {adx_14:.1f})")
        elif is_dmi_bear:
            reasons.append(f"-DI 우위 하방압력 ({minus_di:.1f} > {plus_di:.1f}, ADX {adx_14:.1f})")

        # (2) OBV(9)
        is_obv_bull = ("매집" in obv_trend_str or "상승" in obv_trend_str) and not is_obv_dead
        is_obv_bear = is_obv_dead or is_obv_falling or ("데드" in obv_trend_str) or ("이탈" in obv_trend_str)

        if is_obv_bull:
            reasons.append("OBV(9) 매집 우상향 유지")
        elif is_obv_bear:
            reasons.append("OBV(9) 이평 이탈/데드크로스")

        # (3) Chaikin(13,26)
        is_cho_subzero_2bars = (intraday_cho_recent2[0] <= 0 and intraday_cho_recent2[1] <= 0)
        is_cho_bull = (chaikin_val > 0) or (intraday_cho_recent2[1] > intraday_cho_recent2[0] > 0)
        is_cho_bear = is_cho_outflow or is_cho_subzero_2bars or (chaikin_val < 0)

        if is_cho_bull:
            reasons.append(f"Chaikin 자금 유입 ({int(chaikin_val):+,d})")
        elif is_cho_bear:
            reasons.append(f"Chaikin 자금 유출/약세 ({int(chaikin_val):+,d})")

        # 3. 복합 악화 규칙 평가
        # WEAK = DMI Bear AND OBV Bear AND Chaikin Bear (3개 지표 동시 악화)
        is_composite_weak = is_dmi_bear and is_obv_bear and is_cho_bear

        # DAMAGED = 복합 악화 + 45m 일목 구름 하단 이탈
        if is_composite_weak and is_cloud_breakdown:
            tech_state = "DAMAGED"
            state_summary = "복합 수급 붕괴 및 구름 하단 이탈 (심각)"
            reasons.append("45분봉 일목 구름대 하단 완전 이탈")
        elif is_composite_weak:
            tech_state = "WEAK"
            state_summary = "DMI-/OBV데드/CHO유출 3대 복합 수급 악화"
        elif is_dmi_bull and is_obv_bull and is_cho_bull:
            tech_state = "STRONG"
            state_summary = "+DI우세 / OBV매집 / CHO유입 상방 정렬"
        else:
            tech_state = "NEUTRAL"
            state_summary = "상방/하방 혼조 및 중립 구간"

        return {
            "technical_state": tech_state,
            "state_summary": state_summary,
            "reasons": reasons,
            "is_dmi_bull": is_dmi_bull,
            "is_obv_bull": is_obv_bull,
            "is_cho_bull": is_cho_bull,
            "is_composite_weak": is_composite_weak,
            "is_cloud_breakdown": is_cloud_breakdown
        }

    @staticmethod
    def evaluate_new_buy_gate(
        is_ft_approved: bool,
        data_quality: str,
        tech_state: str,
        reasons_list: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        [1차 50% 신규 매수 Gate]
        - F/T OFF ➔ BUY_BLOCKED (절대 승격 금지)
        - ON + INVALID ➔ BUY_WAIT_DATA
        - ON + DAMAGED ➔ BUY_BLOCKED
        - ON + WEAK ➔ BUY_WAIT
        - ON + NEUTRAL ➔ BUY_ALLOWED_CONDITIONAL
        - ON + STRONG ➔ BUY_ALLOWED
        - ON + UNKNOWN/PARTIAL ➔ BUY_WAIT_DATA
        """
        if not is_ft_approved:
            return {
                "technical_action": "BUY_BLOCKED",
                "action_description": "기존 펀더멘탈/일봉기술 종합판정 OFF (신규 매수 차단)",
                "gate_passed": False
            }

        if "INVALID" in data_quality or tech_state == "UNKNOWN":
            return {
                "technical_action": "BUY_WAIT_DATA",
                "action_description": "45분봉 데이터 미수집 또는 부족 (데이터 확인 대기)",
                "gate_passed": False
            }

        if tech_state == "DAMAGED":
            return {
                "technical_action": "BUY_BLOCKED",
                "action_description": "45분봉 복합 수급 붕괴 및 구름 이탈 (매수 전면 차단)",
                "gate_passed": False
            }
        elif tech_state == "WEAK":
            return {
                "technical_action": "BUY_WAIT",
                "action_description": "45분봉 복합 수급 약세 (DMI-/OBV데드/CHO유출 - 진입 보류/관망)",
                "gate_passed": False
            }
        elif tech_state == "NEUTRAL":
            return {
                "technical_action": "BUY_ALLOWED_CONDITIONAL",
                "action_description": "45분봉 수급 중립/혼조 (1차 50% 조건부 분할매수 승인)",
                "gate_passed": True
            }
        elif tech_state == "STRONG":
            return {
                "technical_action": "BUY_ALLOWED",
                "action_description": "45분봉 전 지표 상방 정렬 (1차 50% 신규 진입 승인)",
                "gate_passed": True
            }
        else:
            return {
                "technical_action": "BUY_WAIT_DATA",
                "action_description": "45분봉 상태 미확정 (대기)",
                "gate_passed": False
            }

    @staticmethod
    def evaluate_second_buy_gate(
        is_rebound_triggered: bool,
        data_quality: str,
        tech_state: str
    ) -> Dict[str, Any]:
        """
        [2차 30% 추가 매수 Gate]
        - ATR V4 눌림목(-1.5A0) 및 반등(+0.4~0.5A0) 조건 확인 후 Technical Gate 적용
        - STRONG ➔ ADD_ALLOWED
        - NEUTRAL ➔ ADD_WAIT
        - WEAK / DAMAGED / INVALID ➔ ADD_BLOCKED
        """
        if not is_rebound_triggered:
            return {
                "technical_action": "ADD_WAIT",
                "action_description": "ATR 2차 눌림목(-1.5A0) 및 반등 조건 미도달 (추매 대기)",
                "gate_passed": False
            }

        if "INVALID" in data_quality or tech_state in ("WEAK", "DAMAGED", "UNKNOWN"):
            return {
                "technical_action": "ADD_BLOCKED",
                "action_description": "2차 반등 시도 중 45분봉 수급 훼손/데이터이상 (추매 차단)",
                "gate_passed": False
            }
        elif tech_state == "NEUTRAL":
            return {
                "technical_action": "ADD_WAIT",
                "action_description": "45분봉 수급 중립 (확실한 상방 반등 확인 대기)",
                "gate_passed": False
            }
        elif tech_state == "STRONG":
            return {
                "technical_action": "ADD_ALLOWED",
                "action_description": "45분봉 수급 상방 반등 확인 (2차 30% 추가매수 승인)",
                "gate_passed": True
            }
        else:
            return {
                "technical_action": "ADD_BLOCKED",
                "action_description": "추매 차단",
                "gate_passed": False
            }

    @staticmethod
    def evaluate_third_buy_gate(
        is_breakout_triggered: bool,
        data_quality: str,
        tech_state: str,
        is_dmi_bull: bool = True,
        is_obv_bull: bool = True,
        is_cho_bull: bool = True
    ) -> Dict[str, Any]:
        """
        [3차 20% 상승확인 추매 Gate]
        - P0 또는 주요 고점 회복/돌파 + DMI 상승추세 + OBV 악화 없음 + Chaikin 유출 없음 확인
        """
        if not is_breakout_triggered:
            return {
                "technical_action": "ADD_WAIT",
                "action_description": "P0 또는 주요 고점 회복·돌파 조건 미도달 (대기)",
                "gate_passed": False
            }

        if "INVALID" in data_quality or tech_state in ("WEAK", "DAMAGED", "UNKNOWN"):
            return {
                "technical_action": "ADD_BLOCKED",
                "action_description": "고점 돌파 중 45분봉 수급 훼손 (3차 추매 차단)",
                "gate_passed": False
            }

        if is_dmi_bull and is_obv_bull and is_cho_bull and tech_state == "STRONG":
            return {
                "technical_action": "ADD_ALLOWED",
                "action_description": "고점 돌파 및 45분봉 강력 상방 추세 유지 (3차 20% 상승확인 추매 승인)",
                "gate_passed": True
            }
        else:
            return {
                "technical_action": "ADD_WAIT",
                "action_description": "45분봉 추세 지속성 추가 확인 필요 (3차 추매 보류)",
                "gate_passed": False
            }

    @staticmethod
    def evaluate_holding_gate(
        trade_mode: str,
        data_quality: str,
        tech_state: str,
        is_cloud_breakdown: bool = False,
        consecutive_weak_bars: int = 1
    ) -> Dict[str, Any]:
        """
        [보유 종목 전술 Gate]
        - NORMAL + WEAK ➔ 추매 금지 + HOLD (관망)
        - 복합 기술악화 지속 ➔ PREEMPTIVE_REDUCE_WARNING (선제적 위험 경고)
        - 복합악화 + 일목 구름 하향이탈 ➔ REDUCE_CONSIDER (비중 축소 고려)
        - *주의*: ATR V4 확정 손절가(S_final)는 임의로 낮추거나 대체하지 않음.
        """
        if "INVALID" in data_quality:
            return {
                "technical_action": "HOLD",
                "action_description": "데이터 결측으로 인한 자동매도 금지 (기존 ATR 손절선 정상 유지)",
                "preemptive_warning": False
            }

        if tech_state == "DAMAGED" or (tech_state == "WEAK" and is_cloud_breakdown):
            return {
                "technical_action": "REDUCE_CONSIDER",
                "action_description": "45분봉 복합 수급 붕괴 및 구름 하향 이탈 (비중 축소 적극 고려)",
                "preemptive_warning": True
            }
        elif tech_state == "WEAK" and consecutive_weak_bars >= 2:
            return {
                "technical_action": "PREEMPTIVE_REDUCE_WARNING",
                "action_description": "45분봉 복합 수급 악화 지속 (선제적 위험 관리 경고)",
                "preemptive_warning": True
            }
        elif tech_state == "WEAK":
            return {
                "technical_action": "HOLD",
                "action_description": "45분봉 단기 수급 약세 (추매 금지 및 기존 ATR 손절선 기준 HOLD)",
                "preemptive_warning": False
            }
        else:
            return {
                "technical_action": "HOLD",
                "action_description": "정상 추세 유지 (ATR 트레일링/래칫 추적 HOLD)",
                "preemptive_warning": False
            }
