# -*- coding: utf-8 -*-
import unittest

from src.analysis.youtube_candidate_identity import (
    normalize_company_name,
    verify_candidate_identity,
)


class _Response:
    def __init__(self, payload=None, status_code=200, json_error=None):
        self._payload = payload
        self.status_code = status_code
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        if not self.responses:
            raise AssertionError("unexpected GET")
        return self.responses.pop(0)


class CandidateIdentityTests(unittest.TestCase):
    def test_name_normalization_ignores_cosmetic_company_markers(self):
        self.assertEqual(normalize_company_name("(주) 한화솔루션"), normalize_company_name("한화 솔루션"))

    def test_valid_identity_passes_without_autocomplete(self):
        session = _Session([_Response({"stockName": "이수페타시스"})])
        result = verify_candidate_identity("007660", "이수페타시스", session=session)
        self.assertTrue(result.valid)
        self.assertEqual(result.status, "VERIFIED")
        self.assertEqual(result.verified_name, "이수페타시스")
        self.assertEqual(len(session.calls), 1)

    def test_legitimate_alias_passes_when_autocomplete_resolves_same_ticker(self):
        session = _Session([
            _Response({"stockName": "피에스케이홀딩스"}),
            _Response({"result": {"items": [{"code": "031980", "name": "피에스케이홀딩스"}]}}),
        ])
        result = verify_candidate_identity("031980", "PSK홀딩스", session=session)
        self.assertTrue(result.valid)
        self.assertEqual(result.status, "VERIFIED_ALIAS")

    def test_wrong_code_is_quarantined_when_source_name_resolves_elsewhere(self):
        # Regression guard for 2026-09-29 source mail: 한화솔루션 (098300).
        session = _Session([
            _Response({"code": "StockConflict"}, status_code=409),
            _Response({"result": {"items": [{"code": "009830", "name": "한화솔루션"}]}}),
        ])
        result = verify_candidate_identity("098300", "한화솔루션", session=session)
        self.assertFalse(result.valid)
        self.assertEqual(result.status, "SOURCE_IDENTITY_MISMATCH")
        self.assertIn("009830", result.reason)
        self.assertIn("not 098300", result.reason)

    def test_unresolved_lookup_fails_closed(self):
        session = _Session([
            _Response({}, status_code=409),
            _Response({"result": {"items": []}}),
        ])
        result = verify_candidate_identity("098300", "알수없는회사", session=session)
        self.assertFalse(result.valid)
        self.assertEqual(result.status, "DATA_HOLD")

    def test_numeric_source_name_fails_closed_without_network(self):
        session = _Session([])
        result = verify_candidate_identity("012450", "012450", session=session)
        self.assertFalse(result.valid)
        self.assertEqual(result.status, "DATA_HOLD")
        self.assertEqual(session.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
