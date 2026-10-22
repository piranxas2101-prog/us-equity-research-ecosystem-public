"""Exit lab: each exit rule ends where it should, a gap past a stop exits at the open, the class is the time stop."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

import exit_lab as el


def arrays(closes, highs=None, lows=None, opens=None):
    c = np.asarray(closes, float)
    h = c + 0.5 if highs is None else np.asarray(highs, float)
    low = c - 0.5 if lows is None else np.asarray(lows, float)
    o = c if opens is None else np.asarray(opens, float)
    sma5 = pd.Series(c).rolling(5).mean().to_numpy()
    return o, h, low, c, sma5


class SimulateTests(unittest.TestCase):
    def test_fixed_holds_to_the_end_of_the_class_and_needs_enough_future(self):
        o, h, low, c, sma = arrays([100, 101, 102, 103, 104, 105])
        self.assertEqual(el.simulate(o, h, low, c, sma, 2.0, 0, 5, "fixed", True), (105.0, 5))
        self.assertIsNone(el.simulate(o, h, low, c, sma, 2.0, 2, 5, "fixed", True))

    def test_atr_bracket_stops_and_takes_profit(self):
        # ATR 2% -> stop 2.2%, take-profit 3.85%
        o, h, low, c, sma = arrays([100, 100, 100], lows=[99.5, 97.0, 99.0])
        self.assertEqual(el.simulate(o, h, low, c, sma, 2.0, 0, 2, "atr_bracket", True), (97.8, 1))
        o, h, low, c, sma = arrays([100, 100, 100], highs=[100.5, 104.0, 100.0])
        price, held = el.simulate(o, h, low, c, sma, 2.0, 0, 2, "atr_bracket", True)
        self.assertAlmostEqual(price, 103.85)
        self.assertEqual(held, 1)

    def test_a_gap_past_the_stop_exits_at_the_open(self):
        o, h, low, c, sma = arrays([100, 95, 95], opens=[100, 94.0, 95], lows=[99.5, 93.5, 94.5])
        self.assertEqual(el.simulate(o, h, low, c, sma, 2.0, 0, 2, "atr_bracket", True), (94.0, 1))

    def test_mean_reversion_exits_when_the_close_crosses_the_5_day_average(self):
        closes = [110, 108, 106, 104, 100, 101, 105, 106]  # entry at index 4 after a drop
        o, h, low, c, sma = arrays(closes)
        price, held = el.simulate(o, h, low, c, sma, 2.0, 4, 3, "mr_signal", True)
        self.assertEqual((price, held), (105.0, 2))  # 105 > 5-day average 103.2 on the second day

    def test_the_trailing_stop_follows_the_high(self):
        closes = [100, 104, 108, 112, 106]
        o, h, low, c, sma = arrays(closes, highs=[100, 104, 108, 112, 106], lows=[99, 103, 107, 111, 104])
        price, held = el.simulate(o, h, low, c, sma, 2.0, 0, 4, "trend_trailing", True)
        self.assertAlmostEqual(price, 112 * 0.94)  # 3 x 2% below the 112 high, touched on the last day
        self.assertEqual(held, 4)

    def test_a_short_mirrors_the_long(self):
        o, h, low, c, sma = arrays([100, 100, 100], highs=[100.5, 103.0, 100.5])
        self.assertEqual(el.simulate(o, h, low, c, sma, 2.0, 0, 2, "atr_bracket", False), (102.2, 1))


class SummaryTests(unittest.TestCase):
    def test_a_rule_is_only_better_with_enough_months_and_the_fdr_control(self):
        rows = []
        for m in range(1, 25):
            for k in range(20):
                date = pd.Timestamp(2024, 1, 1) + pd.DateOffset(months=m - 1, days=k)
                for rule, excess in (("fixed", 0.0), ("trend_trailing", 1.0), ("atr_bracket", 0.0)):
                    rows.append(("momentum_12_1", "long", "month", rule, date, f"S{k}", excess, excess, 20))
        replays = pd.DataFrame(rows, columns=["strategy", "side", "class", "rule", "date", "symbol", "result", "excess", "held"])
        results = {e["rule"]: e for e in el.summarize(replays)}
        self.assertEqual(results["trend_trailing"]["verdict"], "better than fixed")
        self.assertEqual(results["atr_bracket"]["verdict"], "no clear difference")
        best = el.best_per_class(list(results.values()))[0]
        self.assertEqual((best["best_class"], best["best_rule"]), ("month", "trend_trailing"))

    def test_the_edge_is_against_a_random_entry_in_the_same_stock_and_must_hold_across_years(self):
        rows = []
        for m in range(1, 37):  # three years of monthly signals
            for k in range(20):
                date = pd.Timestamp(2023, 1, 1) + pd.DateOffset(months=m - 1, days=k)
                # the stock beats SPY by 2% whatever the entry: no edge for the signal
                rows.append(("gap_fade", "short", "quarter", "fixed", date, f"S{k}", 2.0, 2.0, 60, "signal"))
                rows.append(("gap_fade", "short", "quarter", "fixed", date, f"S{k}", 2.0, 2.0, 60, "random"))
                # this one earns 1% more than its random entry, but only in 2023
                signal = 1.0 if date.year == 2023 else 0.0
                rows.append(("rsi2_mean_reversion", "long", "week", "fixed", date, f"S{k}", signal, signal, 5, "signal"))
                rows.append(("rsi2_mean_reversion", "long", "week", "fixed", date, f"S{k}", 0.0, 0.0, 5, "random"))
        replays = pd.DataFrame(rows, columns=["strategy", "side", "class", "rule", "date", "symbol", "result", "excess",
                                              "held", "kind"])
        results = {e["strategy"]: e for e in el.summarize(replays)}
        self.assertEqual(results["gap_fade"]["mean_excess_pct"], 2.0)
        self.assertEqual(results["gap_fade"]["edge_vs_random_pct"], 0.0)
        self.assertEqual(results["gap_fade"]["edge_verdict"], "no clear edge")
        rsi = results["rsi2_mean_reversion"]
        self.assertEqual((rsi["years"], rsi["years_positive"]), (3, 1))
        self.assertEqual(el.real_edges(list(results.values())), [])  # positive in only one of three years


if __name__ == "__main__":
    unittest.main()
