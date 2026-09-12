# -*- coding: utf-8 -*-
import unittest
from pathlib import Path
import openpyxl

from src.analysis.marketcap_leadership_radar import (
    MARKETCAP_RADAR_UNIVERSE,
    format_rank_band_korean,
    get_marketcap_leadership_summary_for_email,
    build_marketcap_radar_section_html,
)
from src.notifications.mobile_renderer_v2 import generate_mobile_html_report_v2
from tests.fixtures.sample_portfolio_fixture import SAMPLE_HELD_PORTFOLIO


class TestMarketCapLeadershipRadar(unittest.TestCase):

    def test_universe_count_and_structure(self):
        """35개 한국 대형주 유니버스 구조 및 무결성 검증"""
        self.assertGreaterEqual(len(MARKETCAP_RADAR_UNIVERSE), 30)
        for item in MARKETCAP_RADAR_UNIVERSE:
            self.assertTrue(item["active"])
            self.assertEqual(len(item["stock_code"]), 6)
            self.assertIn(item["market"], ("KOSPI", "KOSDAQ"))
            self.assertTrue(item["ticker"].startswith(("KRX:", "KOSDAQ:")))
            self.assertGreater(len(item["stock_name"]), 0)
            self.assertGreater(len(item["sector"]), 0)

    def test_format_rank_band_korean(self):
        """RankBand 한국어 표시명 변환 검증"""
        self.assertEqual(format_rank_band_korean("TOP_9"), "TOP 9")
        self.assertEqual(format_rank_band_korean("RANK_10_20"), "10~20위")
        self.assertEqual(format_rank_band_korean("RANK_21_30"), "21~30위")
        self.assertEqual(format_rank_band_korean("RANK_31_50"), "31~50위")
        self.assertEqual(format_rank_band_korean("OTHER"), "50위 밖")
        self.assertEqual(format_rank_band_korean("UNKNOWN"), "확인대기")

    def test_get_marketcap_leadership_summary_prioritization(self):
        """RANK_10_20 우선 선별 및 RANK_21_30 보충, 최대 5개 제한 검증"""
        sample_items = [
            {"stock_name": "삼성전자", "stock_code": "005930", "current_rank": 1, "rank_band": "TOP_9", "data_quality": "VALID"},
            {"stock_name": "NAVER", "stock_code": "035420", "current_rank": 10, "rank_band": "RANK_10_20", "data_quality": "VALID", "daily_change_pct": 1.5},
            {"stock_name": "신한지주", "stock_code": "055550", "current_rank": 11, "rank_band": "RANK_10_20", "data_quality": "VALID", "daily_change_pct": -0.5},
            {"stock_name": "HD현대중공업", "stock_code": "329180", "current_rank": 12, "rank_band": "RANK_10_20", "data_quality": "VALID", "daily_change_pct": 3.0},
            {"stock_name": "불량종목", "stock_code": "999999", "current_rank": 13, "rank_band": "RANK_10_20", "data_quality": "DATA_REVIEW", "daily_change_pct": 0.0},
            {"stock_name": "크래프톤", "stock_code": "259960", "current_rank": 21, "rank_band": "RANK_21_30", "data_quality": "VALID", "daily_change_pct": 2.1},
            {"stock_name": "HMM", "stock_code": "011200", "current_rank": 22, "rank_band": "RANK_21_30", "data_quality": "VALID", "daily_change_pct": 0.0},
            {"stock_name": "한국전력", "stock_code": "015760", "current_rank": 23, "rank_band": "RANK_21_30", "data_quality": "VALID", "daily_change_pct": -1.2},
        ]

        summary = get_marketcap_leadership_summary_for_email(sample_items, max_count=5)
        self.assertEqual(len(summary), 5)

        # 1~3번은 RANK_10_20 종목 (NAVER 10위, 신한지주 11위, HD현대중공업 12위; DATA_REVIEW 불량종목 제외)
        self.assertEqual(summary[0]["stock_name"], "NAVER")
        self.assertEqual(summary[0]["current_rank"], 10)
        self.assertEqual(summary[1]["stock_name"], "신한지주")
        self.assertEqual(summary[1]["current_rank"], 11)
        self.assertEqual(summary[2]["stock_name"], "HD현대중공업")
        self.assertEqual(summary[2]["current_rank"], 12)

        # 4~5번은 RANK_21_30 종목 (크래프톤 21위, HMM 22위)
        self.assertEqual(summary[3]["stock_name"], "크래프톤")
        self.assertEqual(summary[3]["current_rank"], 21)
        self.assertEqual(summary[4]["stock_name"], "HMM")
        self.assertEqual(summary[4]["current_rank"], 22)

    def test_build_marketcap_radar_section_html(self):
        """모바일 이메일 HTML 섹션 렌더링 및 스타일 검증"""
        self.assertEqual(build_marketcap_radar_section_html([]), "")
        self.assertEqual(build_marketcap_radar_section_html(None), "")

        sample_items = [
            {"stock_name": "NAVER", "stock_code": "035420", "current_rank": 10, "rank_band": "RANK_10_20", "data_quality": "VALID", "daily_change_pct": 1.5},
            {"stock_name": "신한지주", "stock_code": "055550", "current_rank": 11, "rank_band": "RANK_10_20", "data_quality": "VALID", "daily_change_pct": -0.5},
        ]

        html_out = build_marketcap_radar_section_html(sample_items)
        self.assertIn("📊 시총 리더십 레이더", html_out)
        self.assertIn("전 거래일/최근 GoogleFinance 기준", html_out)
        self.assertIn("NAVER", html_out)
        self.assertIn("035420", html_out)
        self.assertIn("10위", html_out)
        self.assertIn("10~20위", html_out)
        self.assertIn("+1.5%", html_out)
        self.assertIn("-0.5%", html_out)

    def test_excel_spreadsheet_file_exists_and_valid(self):
        """생성된 MarketCap_Leadership_Radar.xlsx 파일 무결성 및 수식 검증"""
        base_dir = Path(__file__).resolve().parent.parent
        xlsx_path = base_dir / "MarketCap_Leadership_Radar.xlsx"
        self.assertTrue(xlsx_path.exists(), f"MarketCap_Leadership_Radar.xlsx 파일이 존재하지 않습니다: {xlsx_path}")

        wb = openpyxl.load_workbook(str(xlsx_path))
        self.assertIn("UNIVERSE", wb.sheetnames)
        self.assertIn("LIVE_RADAR", wb.sheetnames)

        ws_uni = wb["UNIVERSE"]
        self.assertGreaterEqual(ws_uni.max_row, 31)

        ws_radar = wb["LIVE_RADAR"]
        self.assertGreaterEqual(ws_radar.max_row, 31)
        # Check that GOOGLEFINANCE formulas are embedded
        self.assertIn("=UNIVERSE!E2", ws_radar.cell(row=2, column=1).value)
        self.assertIn("GOOGLEFINANCE", ws_radar.cell(row=2, column=3).value)
        self.assertIn("GOOGLEFINANCE", ws_radar.cell(row=2, column=4).value)
        self.assertIn("RANK", ws_radar.cell(row=2, column=7).value)
        self.assertIn("RANK_10_20", ws_radar.cell(row=2, column=8).value)

    def test_generate_mobile_html_report_v2_with_marketcap_radar(self):
        """모바일 이메일 리포트 전체 생성 시 시총 리더십 섹션 통합 검증"""
        held = list(SAMPLE_HELD_PORTFOLIO)
        sample_radar = [
            {"stock_name": "NAVER", "stock_code": "035420", "current_rank": 10, "rank_band": "RANK_10_20", "data_quality": "VALID", "daily_change_pct": 1.5},
            {"stock_name": "신한지주", "stock_code": "055550", "current_rank": 11, "rank_band": "RANK_10_20", "data_quality": "VALID", "daily_change_pct": -0.5},
            {"stock_name": "크래프톤", "stock_code": "259960", "current_rank": 21, "rank_band": "RANK_21_30", "data_quality": "VALID", "daily_change_pct": 2.1},
        ]

        html_out = generate_mobile_html_report_v2(
            date_str="2026-08-27 15:00",
            total_count=len(held),
            caught_signals=[],
            all_results=[],
            held_portfolio=held,
            marketcap_radar_items=sample_radar
        )

        self.assertNotIn("📊 시총 리더십 레이더", html_out)
        self.assertNotIn("전 거래일/최근 GoogleFinance 기준", html_out)
        self.assertIn("45M ADD ADVISORY", html_out)
        self.assertIn("📊 전체 보유종목 현황", html_out)


if __name__ == "__main__":
    unittest.main()
