import unittest

from scripts.youtube_candidate_registered_ip_validated_dry_run import _Retry429Session


class FakeResponse:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.responses.pop(0)


class RateLimitRetryTests(unittest.TestCase):
    def test_retry_after_header_is_honored_and_request_recovers(self):
        sleeps = []
        session = FakeSession([
            FakeResponse(429, {"Retry-After": "3"}),
            FakeResponse(200),
        ])
        wrapped = _Retry429Session(session, max_attempts=3, sleep_fn=sleeps.append)

        response = wrapped.post("https://api.kiwoom.com/api/dostk/stkinfo")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(sleeps, [3.0])

    def test_missing_retry_after_uses_bounded_exponential_backoff(self):
        sleeps = []
        session = FakeSession([
            FakeResponse(429),
            FakeResponse(429),
            FakeResponse(200),
        ])
        wrapped = _Retry429Session(session, max_attempts=4, sleep_fn=sleeps.append)

        response = wrapped.post("https://api.kiwoom.com/api/dostk/stkinfo")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_retry_exhaustion_returns_last_429_for_fail_closed_handling(self):
        sleeps = []
        session = FakeSession([
            FakeResponse(429),
            FakeResponse(429),
            FakeResponse(429),
        ])
        wrapped = _Retry429Session(session, max_attempts=3, sleep_fn=sleeps.append)

        response = wrapped.post("https://api.kiwoom.com/api/dostk/stkinfo")

        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(sleeps, [1.0, 2.0])


if __name__ == "__main__":
    unittest.main()
