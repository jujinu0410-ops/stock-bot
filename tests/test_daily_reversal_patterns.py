import unittest

from src.analysis.daily_reversal_patterns import detect_daily_reversal_patterns


def _base(direction: str, n: int = 37):
    bars = []
    price = 130.0 if direction == "DOWN" else 70.0
    step = -1.0 if direction == "DOWN" else 1.0
    for i in range(n):
        close = price + step * i
        open_ = close + (0.5 if direction == "DOWN" else -0.5)
        bars.append({
            "date": f"202609{i+1:02d}",
            "open": open_,
            "high": max(open_, close) + 1.0,
            "low": min(open_, close) - 1.0,
            "close": close,
            "volume": 1000 + i,
        })
    return bars


def _bar(o, c, date="20261001"):
    return {
        "date": date,
        "open": float(o),
        "high": max(float(o), float(c)) + 1.0,
        "low": min(float(o), float(c)) - 1.0,
        "close": float(c),
        "volume": 2000,
    }


class DailyReversalPatternTests(unittest.TestCase):
    def _assert_pattern(self, bars, code, bias):
        out = detect_daily_reversal_patterns(bars)
        self.assertTrue(out["available"])
        self.assertIn(code, out["patterns"], out)
        self.assertEqual(out["bias"], bias)

    def test_three_red_soldiers(self):
        bars = _base("DOWN")
        bars += [_bar(92, 94), _bar(93, 96), _bar(95, 98)]
        self._assert_pattern(bars, "BULL_THREE_RED_SOLDIERS", "BULL")

    def test_bullish_harami_confirmed(self):
        bars = _base("DOWN")
        bars += [_bar(94, 88), _bar(89, 89.2), _bar(90, 96)]
        self._assert_pattern(bars, "BULL_HARAMI_CONFIRMED", "BULL")

    def test_bullish_engulfing_confirmed(self):
        bars = _base("DOWN")
        bars += [_bar(94, 93.8), _bar(93, 96), _bar(96, 99)]
        self._assert_pattern(bars, "BULL_ENGULFING_CONFIRMED", "BULL")

    def test_morning_star(self):
        bars = _base("DOWN")
        bars += [_bar(94, 88), _bar(86.5, 86.7), _bar(88.0, 92)]
        self._assert_pattern(bars, "MORNING_STAR", "BULL")

    def test_three_black_crows(self):
        bars = _base("UP")
        bars += [_bar(108, 106), _bar(107, 104), _bar(105, 102)]
        self._assert_pattern(bars, "BEAR_THREE_BLACK_CROWS", "BEAR")

    def test_bearish_harami_confirmed(self):
        bars = _base("UP")
        bars += [_bar(106, 112), _bar(111, 110.8), _bar(110, 104)]
        self._assert_pattern(bars, "BEAR_HARAMI_CONFIRMED", "BEAR")

    def test_bearish_engulfing_confirmed(self):
        bars = _base("UP")
        bars += [_bar(106, 106.2), _bar(107, 104), _bar(104, 101)]
        self._assert_pattern(bars, "BEAR_ENGULFING_CONFIRMED", "BEAR")

    def test_evening_star(self):
        bars = _base("UP")
        bars += [_bar(106, 112), _bar(114, 113.8), _bar(112.5, 108)]
        self._assert_pattern(bars, "EVENING_STAR", "BEAR")

    def test_no_pattern_without_required_pretrend(self):
        bars = _base("UP")
        bars += [_bar(92, 94), _bar(93, 96), _bar(95, 98)]
        out = detect_daily_reversal_patterns(bars)
        self.assertNotIn("BULL_THREE_RED_SOLDIERS", out["patterns"])


if __name__ == "__main__":
    unittest.main()
