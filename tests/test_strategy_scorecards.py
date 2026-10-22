from __future__ import annotations

import unittest

import strategy_scorecards


class StrategyScorecardTests(unittest.TestCase):
    def test_short_outcome_is_direction_aware(self) -> None:
        self.assertEqual(strategy_scorecards.directional_return(-12.0, "short"), 12.0)
        self.assertEqual(strategy_scorecards.directional_return(8.0, "short"), -8.0)
        self.assertIsNone(strategy_scorecards.directional_return(8.0, "flat"))

    def test_scorecard_uses_trader_outcomes_not_classification(self) -> None:
        rows = [
            {"ticker": "A", "directional_return_pct": 10.0, "source_run_timestamp": "2026-01-01"},
            {"ticker": "B", "directional_return_pct": -5.0, "source_run_timestamp": "2026-01-02"},
        ]
        metrics = strategy_scorecards.metric_row(rows)
        assert metrics is not None
        self.assertEqual(metrics["sample_count"], 2)
        self.assertEqual(metrics["win_rate_pct"], 50.0)
        self.assertEqual(metrics["expectancy_pct"], 2.5)
        self.assertEqual(metrics["payoff_ratio"], 2.0)
        self.assertEqual(metrics["evidence_status"], "exploratory")


    def _row(self, agent: str, playbook: str, ticker: str, ret: float, direction: str = "short") -> dict:
        return {
            "agent_name": agent, "playbook_name": playbook, "signal_verdict": "candidate",
            "trade_direction": direction, "horizon_days": 20, "ticker": ticker,
            "source_run_timestamp": "2026-09-01T10:00:00", "directional_return_pct": ret,
            "sector_id": "tech", "sector": "Technology",
        }

    def test_shared_decision_counts_once_in_aggregates(self) -> None:
        # Four emitters make the same short on AAA; only Yankos shorts BBB.
        rows = [
            self._row("yankos", "support_bounce_swing", "AAA", 5.0),
            self._row("yankos", "controlled_breakout_swing", "AAA", 5.0),
            self._row("periklis", "early_accumulation_swing", "AAA", 5.0),
            self._row("periklis", "pre_run_breakout_confirmation", "AAA", 5.0),
            self._row("yankos", "support_bounce_swing", "BBB", -2.0),
        ]
        decisions = strategy_scorecards.unique_decisions(rows)
        self.assertEqual(len(decisions), 2)
        sector = strategy_scorecards.build_grouped_scorecards(decisions, ["sector_id", "trade_direction", "horizon_days"])
        self.assertEqual(sector[0]["sample_count"], 2)
        self.assertEqual(sector[0]["win_rate_pct"], 50.0)

    def test_emitter_scorecards_report_overlap(self) -> None:
        rows = [
            self._row("yankos", "support_bounce_swing", "AAA", 5.0),
            self._row("periklis", "early_accumulation_swing", "AAA", 5.0),
            self._row("yankos", "support_bounce_swing", "BBB", -2.0),
        ]
        cards = strategy_scorecards.build_grouped_scorecards(
            rows, ["agent_name", "playbook_name", "trade_direction", "horizon_days"],
            emitters=strategy_scorecards.decision_emitters(rows),
        )
        by_emitter = {(card["agent_name"], card["playbook_name"]): card for card in cards}
        yankos = by_emitter[("yankos", "support_bounce_swing")]
        self.assertEqual(yankos["sample_count"], 2)
        self.assertEqual(yankos["shared_decision_count"], 1)
        self.assertEqual(yankos["shared_decision_pct"], 50.0)
        self.assertEqual(by_emitter[("periklis", "early_accumulation_swing")]["shared_decision_pct"], 100.0)

    def test_baseline_excess_is_direction_aware(self) -> None:
        # Shortlist averaged +2% that day. A long that made +5% beat it by 3 points;
        # a short on a stock that fell 5% (+5% for the short) beat it by 7 points.
        rows = [
            {**self._row("yankos", "support_bounce_swing", "AAA", 5.0, direction="long"), "raw_symbol_return_pct": 5.0},
            {**self._row("periklis", "early_accumulation_swing", "BBB", 5.0, direction="short"), "raw_symbol_return_pct": -5.0},
        ]
        strategy_scorecards.attach_baselines(rows, {("2026-09-01T10:00:00", 20): 2.0})
        self.assertAlmostEqual(rows[0]["excess_return_pct"], 3.0)
        self.assertAlmostEqual(rows[1]["excess_return_pct"], 7.0)
        self.assertAlmostEqual(rows[0]["net_return_pct"], 5.0 - strategy_scorecards.ROUND_TRIP_COST_PCT)

    def test_missing_baseline_stays_unknown(self) -> None:
        rows = [{**self._row("yankos", "support_bounce_swing", "AAA", 5.0, direction="long"), "raw_symbol_return_pct": 5.0}]
        strategy_scorecards.attach_baselines(rows, {})
        self.assertIsNone(rows[0]["excess_return_pct"])
        metrics = strategy_scorecards.metric_row(rows)
        assert metrics is not None
        self.assertIsNone(metrics["avg_excess_return_pct"])
        self.assertEqual(metrics["edge_status"], "insufficient_sample")

    def test_edge_status_requires_every_condition(self) -> None:
        base = {"sample_count": 40, "wilson_win_rate_lower_pct": 55.0, "net_expectancy_pct": 0.5, "avg_excess_return_pct": 0.3}
        self.assertEqual(strategy_scorecards.edge_status(base), "candidate_edge")
        self.assertEqual(strategy_scorecards.edge_status({**base, "sample_count": 29}), "insufficient_sample")
        self.assertEqual(strategy_scorecards.edge_status({**base, "wilson_win_rate_lower_pct": 50.0}), "no_edge")
        self.assertEqual(strategy_scorecards.edge_status({**base, "net_expectancy_pct": -0.01}), "no_edge")
        self.assertEqual(strategy_scorecards.edge_status({**base, "avg_excess_return_pct": 0.0}), "no_edge")
        self.assertEqual(strategy_scorecards.edge_status({**base, "avg_excess_return_pct": None}), "no_edge")

    def test_opposite_directions_are_different_decisions(self) -> None:
        rows = [
            self._row("yankos", "support_bounce_swing", "AAA", 5.0, direction="long"),
            self._row("periklis", "early_accumulation_swing", "AAA", -5.0, direction="short"),
        ]
        self.assertEqual(len(strategy_scorecards.unique_decisions(rows)), 2)


if __name__ == "__main__":
    unittest.main()
