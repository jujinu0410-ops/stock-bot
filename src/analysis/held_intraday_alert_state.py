"""State-transition alert logic for the held-position 5-minute monitor.

This module is deliberately side-effect free.  It does not send mail, place
orders, write Google Sheets, or touch the production DB.  The caller supplies
previous/current snapshots and decides how to persist state and dispatch mail.

Design rule: crosses are events; slope/direction defines the regime.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional


LEVEL_RANK = {"INFO": 1, "WATCH": 2, "STRONG": 3, "RISK": 4}


@dataclass(frozen=True)
class AlertEvent:
    key: str
    level: str
    title: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def atr_band(price: Any, ref_close: Any, atr14: Any) -> str:
    """Classify current move from reference close in ATR units."""
    p = _float(price)
    ref = _float(ref_close)
    atr = _float(atr14)
    if p is None or ref is None or atr is None or ref <= 0 or atr <= 0:
        return "ATR_DATA_MISSING"
    move = (p - ref) / atr
    if move >= 1.0:
        return "UP_1_0"
    if move >= 0.5:
        return "UP_0_5"
    if move <= -1.0:
        return "DOWN_1_0"
    if move <= -0.5:
        return "DOWN_0_5"
    return "NORMAL"


def make_snapshot(
    *,
    ticker: Any,
    name: Any,
    price: Any,
    ref_close: Any,
    atr14: Any,
    stop_price: Any,
    trade_time: Any,
    data_quality: Any,
    vwap9_dir: Any,
    vwap26_dir: Any,
    vwap_rel: Any,
    vwap_cross: Any,
    obv_dir: Any,
    obv9_dir: Any,
    obv_rel: Any,
    obv_cross: Any,
    regime: Any,
    regime_asof: Any = "",
) -> Dict[str, Any]:
    """Build the normalized state persisted in ALERT_STATE."""
    p = _float(price)
    stop = _float(stop_price)
    return {
        "ticker": _text(ticker).zfill(6),
        "name": _text(name),
        "price": p,
        "trade_time": _text(trade_time),
        "data_quality": _text(data_quality, "DATA_REVIEW") or "DATA_REVIEW",
        "atr_band": atr_band(p, ref_close, atr14),
        "stop_broken": bool(p is not None and stop is not None and stop > 0 and p <= stop),
        "vwap9_dir": _text(vwap9_dir, "UNKNOWN") or "UNKNOWN",
        "vwap26_dir": _text(vwap26_dir, "UNKNOWN") or "UNKNOWN",
        "vwap_rel": _text(vwap_rel, "UNKNOWN") or "UNKNOWN",
        "vwap_cross": _text(vwap_cross, "NONE") or "NONE",
        "obv_dir": _text(obv_dir, "UNKNOWN") or "UNKNOWN",
        "obv9_dir": _text(obv9_dir, "UNKNOWN") or "UNKNOWN",
        "obv_rel": _text(obv_rel, "UNKNOWN") or "UNKNOWN",
        "obv_cross": _text(obv_cross, "NONE") or "NONE",
        "regime": _text(regime, "UNKNOWN") or "UNKNOWN",
        "regime_asof": _text(regime_asof),
    }


def _changed(prev: Dict[str, Any], cur: Dict[str, Any], key: str) -> bool:
    return prev.get(key) != cur.get(key)


def _turn_event(
    prev: Dict[str, Any],
    cur: Dict[str, Any],
    *,
    key: str,
    up_key: str,
    down_key: str,
    up_title: str,
    down_title: str,
    up_level: str,
    down_level: str,
) -> Optional[AlertEvent]:
    before = prev.get(key)
    after = cur.get(key)
    if before == after:
        return None
    if after == "RISING" and before in {"FALLING", "FLAT"}:
        return AlertEvent(up_key, up_level, up_title, f"{before} → RISING")
    if after == "FALLING" and before in {"RISING", "FLAT"}:
        return AlertEvent(down_key, down_level, down_title, f"{before} → FALLING")
    return None


def detect_state_changes(
    previous: Optional[Dict[str, Any]],
    current: Dict[str, Any],
) -> List[AlertEvent]:
    """Return only meaningful transitions.

    First observation seeds state and intentionally emits no alert.  This avoids
    a mail storm when the 5-minute monitor is first enabled or restarted.
    """
    if not previous:
        return []

    events: List[AlertEvent] = []

    # Data quality / suspension transitions first.  No technical inference when
    # the current data is not valid.
    prev_q = previous.get("data_quality")
    cur_q = current.get("data_quality")
    if prev_q != cur_q:
        if cur_q == "SUSPENDED":
            events.append(AlertEvent("DATA_SUSPENDED", "RISK", "거래정지 상태", "장중 계산을 중지합니다."))
        elif cur_q != "VALID":
            events.append(AlertEvent("DATA_REVIEW", "WATCH", "장중 데이터 점검", f"{prev_q} → {cur_q}"))
        elif prev_q != "VALID":
            events.append(AlertEvent("DATA_RECOVERED", "INFO", "장중 데이터 정상화", f"{prev_q} → VALID"))
    if cur_q != "VALID":
        return events

    # Stop line: first breach and recovery only.
    if previous.get("stop_broken") is not True and current.get("stop_broken") is True:
        events.append(AlertEvent("STOP_BREACH", "RISK", "손절선 최초 침범", "보유전략을 즉시 확인하십시오."))
    elif previous.get("stop_broken") is True and current.get("stop_broken") is False:
        events.append(AlertEvent("STOP_RECOVER", "INFO", "손절선 재회복", "가격이 손절선 위로 회복했습니다."))

    # ATR bands: alert only on band changes, not while price remains in the band.
    if _changed(previous, current, "atr_band"):
        band = current.get("atr_band")
        if band == "UP_1_0":
            events.append(AlertEvent("ATR_UP_1_0", "STRONG", "+1.0 ATR 강한 상승", "가격 변화 강도가 한 단계 상승했습니다."))
        elif band == "UP_0_5":
            events.append(AlertEvent("ATR_UP_0_5", "WATCH", "+0.5 ATR 상승", "의미 있는 상승 구간에 진입했습니다."))
        elif band == "DOWN_1_0":
            events.append(AlertEvent("ATR_DOWN_1_0", "RISK", "-1.0 ATR 강한 하락", "가격 훼손 여부를 확인하십시오."))
        elif band == "DOWN_0_5":
            events.append(AlertEvent("ATR_DOWN_0_5", "WATCH", "-0.5 ATR 하락", "의미 있는 하락 구간에 진입했습니다."))

    # Long/baseline directions get more weight than short-line turns.
    for event in (
        _turn_event(previous, current, key="vwap26_dir", up_key="VWAP26_TURN_UP", down_key="VWAP26_TURN_DOWN",
                    up_title="VWAP26 상승 전환", down_title="VWAP26 하락 전환", up_level="STRONG", down_level="RISK"),
        _turn_event(previous, current, key="obv9_dir", up_key="OBV9_TURN_UP", down_key="OBV9_TURN_DOWN",
                    up_title="OBV 기준선 상승 전환", down_title="OBV 기준선 하락 전환", up_level="STRONG", down_level="RISK"),
        _turn_event(previous, current, key="vwap9_dir", up_key="VWAP9_TURN_UP", down_key="VWAP9_TURN_DOWN",
                    up_title="VWAP9 머리 들기", down_title="VWAP9 머리 숙임", up_level="WATCH", down_level="WATCH"),
        _turn_event(previous, current, key="obv_dir", up_key="OBV_TURN_UP", down_key="OBV_TURN_DOWN",
                    up_title="OBV 상승 전환", down_title="OBV 하락 전환", up_level="WATCH", down_level="WATCH"),
    ):
        if event:
            events.append(event)

    # Crosses are secondary.  Alignment with the long/baseline direction decides
    # whether the cross is noteworthy or only informational.
    if current.get("vwap_cross") == "GOLD_CROSS" and previous.get("vwap_cross") != "GOLD_CROSS":
        aligned = current.get("vwap26_dir") == "RISING"
        events.append(AlertEvent(
            "VWAP_GOLD_CROSS",
            "WATCH" if aligned else "INFO",
            "VWAP 골드크로스",
            "VWAP26 상승과 정렬됨" if aligned else "VWAP26이 상승하지 않아 반등 신호로만 봅니다.",
        ))
    elif current.get("vwap_cross") == "DEAD_CROSS" and previous.get("vwap_cross") != "DEAD_CROSS":
        aligned = current.get("vwap26_dir") == "FALLING"
        events.append(AlertEvent(
            "VWAP_DEAD_CROSS",
            "RISK" if aligned else "INFO",
            "VWAP 데드크로스",
            "VWAP26 하락과 정렬됨" if aligned else "장기선 하락 확인 전에는 단기 약화로 봅니다.",
        ))

    if current.get("obv_cross") == "GOLD_CROSS" and previous.get("obv_cross") != "GOLD_CROSS":
        aligned = current.get("obv9_dir") == "RISING"
        events.append(AlertEvent(
            "OBV_GOLD_CROSS",
            "WATCH" if aligned else "INFO",
            "OBV 골드크로스",
            "OBV9 기준선 상승과 정렬됨" if aligned else "OBV9 기준선 상승 전환은 아직 확인되지 않았습니다.",
        ))
    elif current.get("obv_cross") == "DEAD_CROSS" and previous.get("obv_cross") != "DEAD_CROSS":
        aligned = current.get("obv9_dir") == "FALLING"
        events.append(AlertEvent(
            "OBV_DEAD_CROSS",
            "RISK" if aligned else "INFO",
            "OBV 데드크로스",
            "OBV9 기준선 하락과 정렬됨" if aligned else "기준선 하락 확인 전에는 단기 수급 약화로 봅니다.",
        ))

    # Regime transition is the compact summary of all indicator directions.
    if _changed(previous, current, "regime"):
        regime = current.get("regime")
        if regime == "BULL_CONFIRMED":
            events.append(AlertEvent("REGIME_BULL_CONFIRMED", "STRONG", "상승국면 확인", "VWAP 장단기선과 OBV/기준선이 모두 상승 정렬되었습니다."))
        elif regime == "BEAR_CONFIRMED":
            events.append(AlertEvent("REGIME_BEAR_CONFIRMED", "RISK", "약세국면 확인", "VWAP 장단기선과 OBV/기준선이 모두 하락 정렬되었습니다."))
        elif regime == "PULLBACK_IN_UPREGIME":
            events.append(AlertEvent("REGIME_PULLBACK", "INFO", "상승국면 내 눌림", "VWAP26 상승을 유지한 채 VWAP9가 조정 중입니다."))
        elif regime == "BOUNCE_IN_DOWNREGIME":
            events.append(AlertEvent("REGIME_BOUNCE_DOWN", "INFO", "하락국면 내 반등", "VWAP9는 상승하지만 VWAP26 하락이 계속됩니다."))
        elif regime == "EARLY_IMPROVEMENT":
            events.append(AlertEvent("REGIME_EARLY_IMPROVEMENT", "WATCH", "초기 개선", "단기선/OBV 개선이 시작됐으나 장기·기준선 확인이 더 필요합니다."))
        elif regime == "EARLY_WEAKENING":
            events.append(AlertEvent("REGIME_EARLY_WEAKENING", "WATCH", "초기 약화", "단기선/OBV 약화가 시작돼 장기선 훼손 여부를 확인해야 합니다."))

    return events


def strongest_level(events: Iterable[AlertEvent]) -> str:
    events = list(events)
    if not events:
        return "NONE"
    return max(events, key=lambda e: LEVEL_RANK.get(e.level, 0)).level


def alert_key(events: Iterable[AlertEvent]) -> str:
    """Stable dedupe key for one ticker/one 5-minute evaluation."""
    keys = sorted({e.key for e in events})
    return "|".join(keys)
