# -*- coding: utf-8 -*-
import unittest
from datetime import date, timedelta

from src.analysis.youtube_candidate_naver_flow import (
    NaverFlowError,
    _normalize_deal_rows,
    fetch_naver_flow,
)


class _FakeResponse:
    def __init__(self, *, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if isinstance(self._json_data, Exception):
            raise self._json_data
        return self._json_data


class _FakeSession:
    def __init__(self, trend_rows, fchart_xml):
        self.trend_rows = trend_rows
        self.fchart_xml = fchart_xml
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        if "front-api/stock/domestic/trend" in url:
            return _FakeResponse(json_data={
                "isSuccess": True,
                "result": {"dealTrendInfos": self.trend_rows},
            })
        if "/api/stock/" in url and url.endswith("/integration"):
            return _FakeResponse(json_data={"dealTrendInfos": []})
        if "fchart.stock.naver.com" in url:
            return _FakeResponse(text=self.fchart_xml)
        return _FakeResponse(status_code=404, json_data={})


class YouTubeCandidateNaverFlowTests(unittest.TestCase):
    def test_normalize_mobile_deal_rows(self):
        rows = _normalize_deal_rows([
            {
                "bizdate": "20260929",
                "foreignerPureBuyQuant": "1,200",
                "organPureBuyQuant": "-300",
                "closePrice": "100,000",
            },
            {
                "bizdate": "2026-09-28",
                "foreignerPureBuyQuant": "-500",
                "organPureBuyQuant": "700",
                "closePrice": "99,000",
            },
        ])
        self.assertEqual([x["dt"] for x in rows], ["20260928", "20260929"])
        self.assertEqual(rows[0]["foreign_qty"], -500.0)
        self.assertEqual(rows[0]["institution_qty"], 700.0)
        self.assertEqual(rows[1]["foreign_qty"], 1200.0)
        self.assertEqual(rows[1]["trend_close"], 100000.0)

    def test_fetch_mobile_flow_joins_fchart_and_excludes_asof(self):
        as_of = date(2026, 9, 30)
        trend_rows = []
        fchart_items = []
        # Build 22 calendar rows before as-of plus the as-of row itself.  Calendar
        # continuity is sufficient here because this is a deterministic unit test;
        # production data naturally supplies trading dates only.
        for offset in range(22, -1, -1):
            d = as_of - timedelta(days=offset)
            dt = d.strftime("%Y%m%d")
            close = 100000 + (22 - offset) * 100
            trend_rows.append({
                "bizdate": dt,
                "foreignerPureBuyQuant": str(1000 + offset),
                "organPureBuyQuant": str(-200 + offset),
                "closePrice": str(close),
            })
            fchart_items.append(
                f'<item data="{dt}|99000|101000|98000|{close}|500000" />'
            )
        xml = "<protocol><chartdata>" + "".join(fchart_items) + "</chartdata></protocol>"
        session = _FakeSession(trend_rows, xml)

        rows = fetch_naver_flow("005930", as_of, session=session)

        self.assertEqual(len(rows), 22)
        self.assertTrue(all(x["dt"] < "20260930" for x in rows))
        last = rows[-1]
        self.assertEqual(last["amount_basis"], "NAVER_MOBILE_NET_VOLUME_X_FCHART_CLOSE_ESTIMATE")
        self.assertEqual(last["foreign_amount"], last["foreign_qty"] * last["close"])
        self.assertEqual(last["institution_amount"], last["institution_qty"] * last["close"])
        self.assertEqual(last["turnover_amount"], 500000.0 * last["close"])

    def test_invalid_ticker_fails_closed(self):
        with self.assertRaises(NaverFlowError):
            fetch_naver_flow("ABC", date(2026, 9, 30), session=_FakeSession([], "<protocol/>"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
