# -*- coding: utf-8 -*-
import unittest

from src.analysis.youtube_candidate_naver_flow import _extract_table


class YouTubeCandidateNaverFlowTests(unittest.TestCase):
    def test_extracts_daily_institution_foreign_net_volume(self):
        html = """
        <table>
          <thead>
            <tr>
              <th rowspan="2">날짜</th><th rowspan="2">종가</th><th rowspan="2">거래량</th>
              <th colspan="2">순매매량</th>
            </tr>
            <tr><th>기관</th><th>외국인</th></tr>
          </thead>
          <tbody>
            <tr><td>2026.09.29</td><td>118,000</td><td>556,930</td><td>147,396</td><td>48,039</td></tr>
            <tr><td>2026.09.28</td><td>112,300</td><td>400,000</td><td>-93,156</td><td>-163,730</td></tr>
          </tbody>
        </table>
        """
        df = _extract_table(html)
        self.assertEqual(len(df), 2)
        self.assertEqual(float(df.iloc[0]["institution_qty"]), 147396.0)
        self.assertEqual(float(df.iloc[0]["foreign_qty"]), 48039.0)
        self.assertEqual(float(df.iloc[1]["foreign_qty"]), -163730.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
