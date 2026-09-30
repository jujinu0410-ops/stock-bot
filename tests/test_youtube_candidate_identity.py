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
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        return self.response


class CandidateIdentityTests(unittest.TestCase):
    def test_name_normalization_ignores_cosmetic_company_markers(self):
        self.assertEqual(normalize_company_name("(주) 한화솔루션"), normalize_company_name("한화 솔루션"))

    def test_valid_identity_passes(self):
        session = _Session(_Response({"stockName": "이수페타시스"}))
        result = verify_candidate_identity("007660", "이수페타시스", session=session)
        self.assertTrue(result.valid)
        self.assertEqual(result.status, "VERIFIED")
        self.assertEqual(result.verified_name, "이수페타시스")

    def test_wrong_name_for_valid_code_is_quarantined(self):
        # Regression guard for source-mail style code/name mismatch.
        session = _Session(_Response({"stockName": "다른회사"}))
        result = verify_candidate_identity("098300", "한화솔루션", session=session)
        self.assertFalse(result.valid)
        self.assertEqual(result.status, "SOURCE_IDENTITY_MISMATCH")
        self.assertIn("does not match", result.reason)

    def test_network_or_schema_failure_fails_closed(self):
        session = _Session(_Response({}))
        result = verify_candidate_identity("007660", "이수페타시스", session=session)
        self.assertFalse(result.valid)
        self.assertEqual(result.status, "DATA_HOLD")


if __name__ == "__main__":
    unittest.main(verbosity=2)
