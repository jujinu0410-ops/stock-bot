# -*- coding: utf-8 -*-
import unittest
from datetime import date, timedelta

from src.analysis.youtube_candidate_naver_pc_flow import (
    NaverPCFlowError,
    fetch_naver_pc_flow,
    parse_naver_pc_flow_html,
)


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code


class _FakeSession:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        page = int((params or {}).get("page", 1))
        self.calls.append((url, dict(params or {})))
        return _FakeResponse(self.pages.get(page, ""))


def _html_for_rows(rows):
    body = []
    for dt, close, volume, inst, foreign in rows:
        body.append(
            "<tr>"
            f"<td>{dt}</td><td>{close:,}</td><td>상승 100</td><td>+0.1%</td>"
            f"<td>{volume:,}</td><td>{inst:+,}</td><td>{foreign:+,}</td>"
            "<td>1,000,000</td><td>10.0%</td></tr>"
        )
    return '<html><table class="type2"><tbody>' + "".join(body) + "</tbody></table></html>"


class YouTubeCandidateNaverPCFlowTests(unittest.TestCase):
    def test_parse_pc_table(self):
        html = _html_for_rows([
            ("2026.09.29", 100000, 500000, 1200, -300),
            ("2026.09.28", 99000, 450000, -500, 700),
        ])
        rows = parse_naver_pc_flow_html(html)
        self.assertEqual([x["dt"] for x in rows], ["20260928", "20260929"])
        self.assertEqual(rows[0]["institution_qty"], -500.0)
        self.assertEqual(rows[0]["foreign_qty"], 700.0)
        self.assertEqual(rows[1]["close"], 100000.0)

    def test_fetch_two_pages_provides_20_completed_sessions(self):
        as_of = date(2026, 9, 30)
        rows = []
        d = as_of
        while len(rows) < 25:
            d -= timedelta(days=1)
            if d.weekday() >= 5:
                continue
            idx = len(rows)
            rows.append((d.strftime("%Y.%m.%d"), 100000 + idx * 100, 500000 + idx, 1000 + idx, -200 + idx))

        # Naver's first page normally contains about 20 rows; second page supplies
        # enough older sessions if the as-of session itself is filtered out.
        page1 = _html_for_rows(rows[:18])
        page2 = _html_for_rows(rows[18:])
        session = _FakeSession({1: page1, 2: page2})
        result = fetch_naver_pc_flow("005930", as_of, pages=2, session=session)

        self.assertGreaterEqual(len(result), 20)
        self.assertTrue(all(x["dt"] < "20260930" for x in result))
        last = result[-1]
        self.assertEqual(last["amount_basis"], "NAVER_PC_NET_VOLUME_X_CLOSE_ESTIMATE")
        self.assertEqual(last["foreign_amount"], last["foreign_qty"] * last["close"])
        self.assertGreater(last["turnover_amount"], 0)

    def test_invalid_ticker_fails_closed(self):
        with self.assertRaises(NaverPCFlowError):
            fetch_naver_pc_flow("ABC", date(2026, 9, 30), session=_FakeSession({}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
