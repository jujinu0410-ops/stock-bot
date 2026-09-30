# -*- coding: utf-8 -*-
from src.analysis.youtube_candidate_review import (
    KiwoomAfterCloseMarketRegimeReader,
    decide_notification,
    describe_flow,
    market_code_from_market_type,
    render_candidate_preview,
)


class _Resp:
    def __init__(self, payload, headers=None, status=200):
        self._payload = payload
        self.headers = headers or {}
        self.status_code = status

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append((url, headers, json, timeout))
        return self.responses.pop(0)


def test_describe_negative_mixed_flow_is_not_called_broad_selling():
    text = describe_flow({
        "status": "NEGATIVE",
        "foreign_5d": -40746,
        "institution_5d": 37495,
        "combined_5d": -3251,
        "combined_20d": -41594,
    })
    assert "대부분 흡수" in text
    assert "광범위" not in text
    assert "동반 순매도" not in text


def test_describe_negative_both_sellers():
    text = describe_flow({
        "status": "NEGATIVE",
        "foreign_5d": -10,
        "institution_5d": -20,
        "combined_5d": -30,
        "combined_20d": -100,
    })
    assert "동반 순매도" in text


def test_notification_first_buy_state_sends():
    d = decide_notification("BUY_ALERT", previous_signal="WATCH_ONLY")
    assert d.would_send is True


def test_notification_repeat_same_cycle_suppressed():
    d = decide_notification("BUY_ALERT_STRONG", previous_signal="BUY_ALERT", new_ready_cycle=False)
    assert d.would_send is False
    assert "duplicate" in d.reason


def test_notification_new_cycle_requires_cooldown():
    assert decide_notification(
        "BUY_ALERT", previous_signal="BUY_ALERT", new_ready_cycle=True,
        trading_days_since_last=4, cooldown_trading_days=5,
    ).would_send is False
    assert decide_notification(
        "BUY_ALERT", previous_signal="BUY_ALERT", new_ready_cycle=True,
        trading_days_since_last=5, cooldown_trading_days=5,
    ).would_send is True


def test_watch_only_never_mail_worthy():
    d = decide_notification("WATCH_ONLY")
    assert d.would_send is False


def test_market_type_mapping():
    assert market_code_from_market_type("KOSPI") == ("KOSPI", "001")
    assert market_code_from_market_type("KOSDAQ") == ("KOSDAQ", "101")
    assert market_code_from_market_type("ETF") is None


def test_market_reader_aggregates_all_pages():
    session = _Session([
        _Resp(
            {"opaf_invsr_trde": [
                {"frgnr_invsr": "+100", "orgn": "-20"},
                {"frgnr_invsr": "-10", "orgn": "+5"},
            ], "return_code": 0},
            {"cont-yn": "Y", "next-key": "abc"},
        ),
        _Resp(
            {"opaf_invsr_trde": [
                {"frgnr_invsr": "+30", "orgn": "+40"},
            ], "return_code": 0},
            {"cont-yn": "N", "next-key": ""},
        ),
    ])
    r = KiwoomAfterCloseMarketRegimeReader("token", session).fetch(
        "KOSPI", "001", page_delay=0,
    )
    assert r.complete is True
    assert r.row_count == 3
    assert r.foreign_amount == 120
    assert r.institution_amount == 25
    assert r.status == "POSITIVE"
    assert session.calls[1][1]["next-key"] == "abc"


def test_preview_watch_only_contains_refined_flow_and_no_send_claim():
    candidate = {
        "ticker": "007660", "name": "이수페타시스", "last_seen_date": "2026-09-23",
        "mention_count_30d": 1,
        "technical": {"status": "BUY_CANDIDATE", "rebound_atr": 0.54045,
                      "current_price": 115400, "atr14": 7401.22,
                      "buy_trigger_05": 115100.61, "confirm_trigger_06": 115840.73},
        "flow": {"status": "NEGATIVE", "foreign_5d": -40746,
                 "institution_5d": 37495, "combined_5d": -3251,
                 "combined_20d": -41594},
        "final": {"signal": "WATCH_ONLY", "market_regime": "UNAVAILABLE"},
    }
    text = render_candidate_preview(candidate)
    assert "WATCH_ONLY" in text
    assert "대부분 흡수" in text
    assert "매수 검토용 preview" in text
