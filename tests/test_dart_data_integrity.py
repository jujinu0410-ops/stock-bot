import unittest
from datetime import datetime

from src.api.dart_account_utils import is_revenue_account
from src.api.dart_api import DartAPIClient, KST
from src.api.disclosure_collector import DisclosureCollector
from src.api.quarterly_dart_collector import QuarterlyDartCollector


class TestDartDataIntegrity(unittest.TestCase):
    def test_revenue_matcher_rejects_adjustment_account(self):
        self.assertFalse(is_revenue_account("dart_AdjustmentsForOtherRevenue", "기타수익조정"))
        self.assertTrue(is_revenue_account("ifrs-full_Revenue", "매출액"))
        self.assertTrue(is_revenue_account("company_Revenue", "매출액"))

    def test_strict_parser_uses_cumulative_interim_and_rejects_adjustment(self):
        client = DartAPIClient(api_key="TEST")
        items = [
            {
                "sj_div": "IS",
                "account_id": "dart_AdjustmentsForOtherRevenue",
                "account_nm": "기타수익조정",
                "thstrm_amount": "999999999",
                "thstrm_add_amount": "1999999999",
                "frmtrm_amount": "999999999",
                "frmtrm_add_amount": "1999999999",
            },
            {
                "sj_div": "IS",
                "account_id": "ifrs-full_Revenue",
                "account_nm": "매출액",
                "thstrm_amount": "1000",
                "thstrm_add_amount": "1900",
                "frmtrm_amount": "900",
                "frmtrm_add_amount": "1700",
            },
            {
                "sj_div": "IS",
                "account_id": "ifrs-full_OperatingIncomeLoss",
                "account_nm": "영업이익",
                "thstrm_amount": "100",
                "thstrm_add_amount": "180",
                "frmtrm_amount": "80",
                "frmtrm_add_amount": "140",
            },
            {
                "sj_div": "IS",
                "account_id": "ifrs-full_ProfitLoss",
                "account_nm": "당기순이익",
                "thstrm_amount": "80",
                "thstrm_add_amount": "140",
                "frmtrm_amount": "60",
                "frmtrm_add_amount": "110",
            },
            {
                "sj_div": "CF",
                "account_id": "ifrs-full_CashFlowsFromUsedInOperatingActivities",
                "account_nm": "영업활동현금흐름",
                "thstrm_amount": "120",
                "frmtrm_amount": "100",
            },
            {
                "sj_div": "BS",
                "account_id": "ifrs-full_Assets",
                "account_nm": "자산총계",
                "thstrm_amount": "2000",
                "frmtrm_amount": "1800",
            },
            {
                "sj_div": "BS",
                "account_id": "ifrs-full_Liabilities",
                "account_nm": "부채총계",
                "thstrm_amount": "600",
                "frmtrm_amount": "600",
            },
            {
                "sj_div": "BS",
                "account_id": "ifrs-full_Equity",
                "account_nm": "자본총계",
                "thstrm_amount": "1400",
                "frmtrm_amount": "1200",
            },
        ]
        parsed = client._parse_all_dart_statement_strict(items, "CFS", 2026, "11012", True)
        self.assertEqual(parsed["revenue"], 1900.0)
        self.assertEqual(parsed["prev_revenue"], 1700.0)
        self.assertEqual(parsed["operating_profit"], 180.0)
        self.assertEqual(parsed["prev_operating_profit"], 140.0)
        self.assertEqual(parsed["financial_period_basis"], "CUMULATIVE_INTERIM")

    def test_quarterly_parser_uses_same_strict_revenue_rule(self):
        collector = QuarterlyDartCollector.__new__(QuarterlyDartCollector)
        items = [
            {
                "sj_div": "IS",
                "account_id": "dart_AdjustmentsForOtherRevenue",
                "account_nm": "기타수익조정",
                "thstrm_amount": "999999999",
                "thstrm_add_amount": "999999999",
            },
            {
                "sj_div": "IS",
                "account_id": "ifrs-full_Revenue",
                "account_nm": "매출액",
                "thstrm_amount": "1000",
                "thstrm_add_amount": "1900",
            },
        ]
        parsed = collector._extract_raw_accounts_from_items(items)
        self.assertEqual(parsed["rev_cum"], 1900.0)
        self.assertEqual(parsed["rev_discrete"], 1000.0)

    def test_latest_query_targets_prioritize_current_q2_over_q1_in_september(self):
        targets = DartAPIClient._build_latest_query_targets(datetime(2026, 9, 13, 12, 0, tzinfo=KST))
        self.assertEqual(targets[:3], [
            (2026, "11012", True),
            (2026, "11013", True),
            (2025, "11011", True),
        ])
        self.assertNotIn((2026, "11014", True), targets)

    def test_latest_query_targets_include_q3_from_november(self):
        targets = DartAPIClient._build_latest_query_targets(datetime(2026, 11, 20, 12, 0, tzinfo=KST))
        self.assertEqual(targets[:3], [
            (2026, "11014", True),
            (2026, "11012", True),
            (2026, "11013", True),
        ])

    def test_unknown_corp_code_is_fail_closed(self):
        client = DartAPIClient(api_key="TEST")
        client.dynamic_map = {"000001": "00000001"}  # suppress network refresh path
        self.assertEqual(client.get_corp_code("999999"), "")
        fallback = client._get_fallback_data("999999", "CORP_CODE_UNRESOLVED")
        self.assertFalse(fallback["f_score_confirmed"])
        self.assertEqual(fallback["data_completeness"], 0.0)

    def test_share_pledge_release_is_not_supply_order_cancel(self):
        collector = DisclosureCollector.__new__(DisclosureCollector)
        parsed = collector._classify_event("최대주주변경을수반하는주식담보제공계약해제ㆍ취소등")
        self.assertEqual(parsed["event_type"], "MAJOR_SHAREHOLDER_CHANGE")
        self.assertNotEqual(parsed["event_type"], "ORDER_CANCEL")

    def test_real_supply_contract_cancel_remains_order_cancel(self):
        collector = DisclosureCollector.__new__(DisclosureCollector)
        parsed = collector._classify_event("단일판매ㆍ공급계약해제ㆍ해지")
        self.assertEqual(parsed["event_type"], "ORDER_CANCEL")
        self.assertTrue(parsed["is_negative"])

    def test_quarter_window_is_dynamic(self):
        quarters = QuarterlyDartCollector._build_potential_quarters(2026)
        self.assertEqual(quarters[0], (2024, "11013", "Q1", "2024-03-31"))
        self.assertEqual(quarters[-1], (2026, "11011", "Q4", "2026-12-31"))
        self.assertEqual(len(quarters), 12)


if __name__ == "__main__":
    unittest.main()
