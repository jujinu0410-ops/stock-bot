from src.analysis.held_intraday_alert_state import (
    alert_key,
    detect_state_changes,
    make_snapshot,
    strongest_level,
)


def snap(**overrides):
    base = dict(
        ticker="000990",
        name="DB하이텍",
        price=100,
        ref_close=100,
        atr14=10,
        stop_price=90,
        trade_time="2026-10-01 10:00",
        data_quality="VALID",
        vwap9_dir="FLAT",
        vwap26_dir="FALLING",
        vwap_rel="DEAD",
        vwap_cross="NONE",
        obv_dir="FLAT",
        obv9_dir="FALLING",
        obv_rel="DEAD",
        obv_cross="NONE",
        regime="NEUTRAL",
        regime_asof="2026-10-01",
    )
    base.update(overrides)
    return make_snapshot(**base)


def test_first_observation_seeds_without_alert():
    assert detect_state_changes(None, snap()) == []


def test_gold_cross_against_falling_vwap26_is_only_info():
    prev = snap(vwap9_dir="FALLING", vwap26_dir="FALLING", vwap_rel="DEAD")
    cur = snap(vwap9_dir="RISING", vwap26_dir="FALLING", vwap_rel="GOLD", vwap_cross="GOLD_CROSS", regime="BOUNCE_IN_DOWNREGIME")
    events = detect_state_changes(prev, cur)
    cross = [e for e in events if e.key == "VWAP_GOLD_CROSS"][0]
    assert cross.level == "INFO"
    assert "VWAP26" in cross.message


def test_long_and_baseline_turn_up_then_bull_confirmed_is_strong():
    prev = snap(vwap9_dir="RISING", vwap26_dir="FALLING", obv_dir="RISING", obv9_dir="FALLING", regime="EARLY_IMPROVEMENT")
    cur = snap(vwap9_dir="RISING", vwap26_dir="RISING", vwap_rel="GOLD", obv_dir="RISING", obv9_dir="RISING", obv_rel="GOLD", regime="BULL_CONFIRMED")
    events = detect_state_changes(prev, cur)
    keys = {e.key for e in events}
    assert "VWAP26_TURN_UP" in keys
    assert "OBV9_TURN_UP" in keys
    assert "REGIME_BULL_CONFIRMED" in keys
    assert strongest_level(events) == "STRONG"


def test_stop_breach_emits_once_then_does_not_repeat():
    prev = snap(price=95)
    cur = snap(price=89)
    events = detect_state_changes(prev, cur)
    assert "STOP_BREACH" in {e.key for e in events}

    repeat = detect_state_changes(cur, snap(price=88))
    assert "STOP_BREACH" not in {e.key for e in repeat}


def test_atr_band_transition_is_stateful():
    prev = snap(price=100)
    cur = snap(price=106)
    events = detect_state_changes(prev, cur)
    assert "ATR_UP_0_5" in {e.key for e in events}
    assert alert_key(events)


def test_invalid_data_suppresses_technical_inference():
    prev = snap()
    cur = snap(data_quality="DATA_REVIEW", vwap9_dir="RISING", regime="BULL_CONFIRMED")
    events = detect_state_changes(prev, cur)
    assert {e.key for e in events} == {"DATA_REVIEW"}
