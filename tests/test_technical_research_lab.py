import unittest
from datetime import datetime, timedelta, timezone

import technical_research_lab as lab


def bars(count: int = 70):
    start = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    rows = []
    price = 10.0
    for index in range(count):
        open_ = price
        close = open_ + 0.05
        stamp = start + timedelta(minutes=15 * index)
        rows.append({"timestamp": stamp.isoformat(), "available_at": (stamp + timedelta(minutes=15)).isoformat(),
                     "session": stamp.date().isoformat(), "open": open_, "high": close + 0.03,
                     "low": open_ - 0.03, "close": close, "volume": 10000})
        price = close
    return rows


class TechnicalResearchLabTests(unittest.TestCase):
    def test_indicator_audit_is_incrementally_causal(self):
        result = lab.audit_indicator_causality(bars())
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["mismatches"], [])

    def test_future_availability_is_rejected(self):
        rows = bars(3)
        rows[1]["available_at"] = "2026-01-02T14:00:00+00:00"
        result = lab.audit_indicator_causality(rows)
        self.assertEqual(result["status"], "blocked")

    def test_execution_uses_next_bar_and_models_costs(self):
        result = lab.simulate_trade(bars(), signal_index=25, side="long", quantity=100, stop_price=10.0,
                                    target_price=11.6, max_holding_bars=10,
                                    costs=lab.ExecutionCosts(commission_bps=1, slippage_bps=5, max_volume_participation=.02))
        self.assertEqual(result["status"], "filled")
        self.assertEqual(result["entry_index"], 26)
        self.assertGreater(result["costs"], 0)
        self.assertIn("mfe_pct", result)
        self.assertIn("mae_pct", result)

    def test_volume_participation_caps_fill(self):
        result = lab.simulate_trade(bars(), 20, "long", 1000, costs=lab.ExecutionCosts(max_volume_participation=.01))
        self.assertEqual(result["filled_quantity"], 100)

    def test_walk_forward_never_overlaps_future_training(self):
        splits = lab.walk_forward_splits(100, train=40, validation=10, test=10)
        self.assertGreater(len(splits), 1)
        for split in splits:
            self.assertEqual(split["train"][1], split["validation"][0])
            self.assertEqual(split["validation"][1], split["test"][0])

    def test_promotion_fails_closed(self):
        result = lab.evaluate_promotion({"bias_audit_status": "pass", "out_of_sample_trades": 60,
                                        "net_expectancy_pct": 1.0, "profit_factor": 1.3,
                                        "max_drawdown_pct": -12, "paper_trades": 0})
        self.assertEqual(result["status"], "shadow")
        self.assertIn("paper_trades_at_least_20", result["missing_gates"])

    def test_scorecard_reports_drawdown_costs_and_regimes(self):
        trades = [
            {"status": "filled", "return_pct": 3.0, "mfe_pct": 4.0, "mae_pct": -1.0, "costs": 1.0, "regimes": ["trend_up"]},
            {"status": "filled", "return_pct": -2.0, "mfe_pct": 1.0, "mae_pct": -3.0, "costs": 1.5, "regimes": ["trend_up"]},
        ]
        result = lab.summarize_trades(trades)
        self.assertEqual(result["trade_count"], 2)
        self.assertEqual(result["profit_factor"], 1.5)
        self.assertLess(result["max_drawdown_pct"], 0)
        self.assertEqual(result["regime_scorecards"]["trend_up"]["count"], 2)
        self.assertEqual(len(result["expectancy_bootstrap_95pct"]), 2)


if __name__ == "__main__":
    unittest.main()
