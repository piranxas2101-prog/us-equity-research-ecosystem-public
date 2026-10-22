"""Gap reversal lab: events use only what is known at the open; the fade is entered at the open; the day's volume
is a description, never an edge."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

import gap_reversal_lab as gl


def frame(n=80, gap_at=60, gap=-0.10):
    index = pd.bdate_range("2025-01-01", periods=n)
    close = np.full(n, 100.0)
    opens = close.copy()
    opens[gap_at] = 100 * (1 + gap)
    close[gap_at:] = 95.0
    opens[gap_at + 1:] = 95.0
    return pd.DataFrame({"Open": opens, "High": np.maximum(opens, close) + 1, "Low": np.minimum(opens, close) - 1,
                         "Close": close, "Volume": np.full(n, 1e6)}, index=index)


class EventTests(unittest.TestCase):
    def test_a_gap_down_is_faded_long_from_the_open_against_spy(self):
        stock = frame()
        spy = pd.DataFrame({"Open": 100.0, "Close": 100.0}, index=stock.index)
        rows = gl.events_for(stock, spy, np.random.default_rng(1))
        day = [r for r in rows if r[4] == "day"]
        self.assertEqual(len(day), 1)
        date, side, size, news, cls, excess, _ = day[0]
        self.assertEqual((side, size, news), ("long", ">=3 ATR", False))
        self.assertAlmostEqual(excess, (95 / 90 - 1) * 100, places=3)  # bought the 90 open, closed at 95

    def test_illiquid_stocks_give_no_events(self):
        stock = frame()
        stock["Volume"] = 1.0
        spy = pd.DataFrame({"Open": 100.0, "Close": 100.0}, index=stock.index)
        self.assertEqual(gl.events_for(stock, spy, np.random.default_rng(1)), [])


class SummaryTests(unittest.TestCase):
    def test_the_days_volume_never_makes_an_edge(self):
        rows = []
        for m in range(36):
            for k in range(15):
                date = pd.Timestamp(2023, 1, 1) + pd.DateOffset(months=m, days=k)
                rows.append(("S", date, "short", ">=3 ATR", True, "day", 2.0, 0.0))
        events = pd.DataFrame(rows, columns=["symbol", "date", "side", "size", "news", "class", "excess",
                                             "random_excess"])
        results = {e["volume"]: e for e in gl.summarize(events)}
        self.assertEqual(results["all"]["verdict"], "fade edge")
        self.assertTrue(results["high_volume_day"]["verdict"].startswith("descriptive: "))


if __name__ == "__main__":
    unittest.main()
