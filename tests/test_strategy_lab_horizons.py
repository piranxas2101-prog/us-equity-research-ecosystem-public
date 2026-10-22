"""Strategy lab holding classes: edge against a random entry per horizon, and a fit only with enough signals."""
from __future__ import annotations

import unittest

import numpy as np

import strategy_lab as lab


def entry(cells: dict[int, tuple[int, float]], side: str = "long") -> dict:
    return {"strategy": "momentum_12_1", "side": side,
            "by_horizon": {h: {"n": n, "beat_spy_pct": pct, "median_excess_pct": 0.0} for h, (n, pct) in cells.items()}}


class HorizonTests(unittest.TestCase):
    def test_random_baseline_counts_wins_per_side(self):
        baseline = lab.random_baseline({5: [np.array([1.0, -1.0, 2.0, 3.0])]})
        self.assertEqual(baseline[5]["long"]["beat_spy_pct"], 75.0)
        self.assertEqual(baseline[5]["short"]["beat_spy_pct"], 25.0)

    def test_the_best_class_needs_enough_signals_and_a_real_edge(self):
        result = entry({1: (500, 50.5), 5: (500, 52.0), 20: (500, 55.0), 60: (40, 70.0)})
        baseline = {h: {"long": {"beat_spy_pct": 50.0, "median_excess_pct": 0.0}} for h in (1, 5, 20, 60)}
        lab.attach_edges([result], baseline)
        fit = lab.horizon_fit(result)
        self.assertEqual(fit["best"], "month")                                  # +5.0 at 20 sessions
        self.assertIsNone(fit["classes"]["quarter"]["edge_vs_random_pct_points"])  # 40 signals: no verdict
        self.assertEqual(fit["classes"]["day"]["edge_vs_random_pct_points"], 0.5)

    def test_no_class_is_a_fit_below_the_minimum_edge(self):
        result = entry({1: (500, 50.2), 5: (500, 50.9), 20: (500, 49.0), 60: (500, 50.0)})
        lab.attach_edges([result], {h: {"long": {"beat_spy_pct": 50.0, "median_excess_pct": 0.0}} for h in (1, 5, 20, 60)})
        self.assertIsNone(lab.horizon_fit(result)["best"])
        self.assertIn("| καμία |", lab.render_horizons([result])[-1])


if __name__ == "__main__":
    unittest.main()
