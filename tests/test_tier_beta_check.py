"""Giannis tiers vs beta (owner approved 2026-11-16): Dimson beta, unknown stays unknown, verdicts."""
from __future__ import annotations

from datetime import date, timedelta
import random
import unittest

import tier_beta_check as tb


def market(n=300, seed=3):
    rng = random.Random(seed)
    days = [date(2025, 1, 1) + timedelta(days=k) for k in range(n)]
    spy = [100.0]
    for _ in range(n - 1):
        spy.append(spy[-1] * (1 + rng.gauss(0.0005, 0.01)))
    return days, spy, rng


class BetaTests(unittest.TestCase):
    def test_a_stock_moving_twice_the_market_has_beta_near_two(self):
        days, spy, rng = market()
        stock, price = {}, 50.0
        for k, day in enumerate(days):
            if k:
                price *= 1 + 2 * (spy[k] / spy[k - 1] - 1) + rng.gauss(0, 0.002)
            stock[day] = price
        self.assertAlmostEqual(tb.beta(stock, days, spy, len(days) - 1), 2.0, delta=0.15)

    def test_a_stock_reacting_a_day_late_is_not_read_as_low_beta(self):
        days, spy, _ = market()
        stock, price = {}, 50.0
        for k, day in enumerate(days):
            if k > 1:
                price *= 1 + 1.5 * (spy[k - 1] / spy[k - 2] - 1)
            stock[day] = price
        self.assertAlmostEqual(tb.beta(stock, days, spy, len(days) - 1), 1.5, delta=0.15)

    def test_too_few_known_days_give_no_beta(self):
        days, spy, _ = market()
        stock = {day: 10.0 + k for k, day in enumerate(days[:50])}
        self.assertIsNone(tb.beta(stock, days, spy, len(days) - 1))


class VerdictTests(unittest.TestCase):
    def cell(self, tier, spy, beta, dates=20):
        return {"horizon": 10, "tier": tier, "vs_spy": {"mean_pct": spy}, "vs_beta": {"mean_pct": beta},
                "vs_beta_by_date": {"dates": dates}}

    def test_verdicts(self):
        cases = {(0.0, 2.0, 0.0, -0.5): "inversion explained by beta",
                 (0.0, 2.0, 0.0, 1.8): "inverted even after beta",
                 (0.0, 2.0, 0.0, 0.5): "mostly beta, part remains",
                 (1.0, 0.5, 1.0, 0.5): "not inverted even against SPY"}
        for (core_spy, spec_spy, core_beta, spec_beta), expected in cases.items():
            tiers = [self.cell("Core Watchlist", core_spy, core_beta), self.cell("Speculative Monitor", spec_spy, spec_beta)]
            self.assertEqual(tb.verdicts(tiers)["10"], expected)
        thin = [self.cell("Core Watchlist", 0, 0, dates=5), self.cell("Speculative Monitor", 2, 2)]
        self.assertEqual(tb.verdicts(thin)["10"], "insufficient")
        unknown = [self.cell("Core Watchlist", None, None), self.cell("Speculative Monitor", 2, 2)]
        self.assertEqual(tb.verdicts(unknown)["10"], "insufficient")


if __name__ == "__main__":
    unittest.main()
