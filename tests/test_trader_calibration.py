import unittest

import trader_calibration as tc


def row(agent, label, directional, ticker, run="2026-09-01T10:00", horizon=5, direction="long", playbook="p"):
    return {"agent_name": agent, "playbook_name": playbook, "confidence": label, "ticker": ticker,
            "source_run_timestamp": run, "horizon_days": horizon, "trade_direction": direction,
            "directional_return_pct": directional, "confidence_probability": None}


def spread(agent, label, wins, losses, win_value, loss_value, start=0, **kwargs):
    rows = [row(agent, label, win_value, f"{label[0]}W{start + i}", **kwargs) for i in range(wins)]
    return rows + [row(agent, label, loss_value, f"{label[0]}L{start + i}", **kwargs) for i in range(losses)]


class TraderCalibrationTests(unittest.TestCase):
    def test_labels_that_rank_decisions_correctly_are_ordered(self):
        rows = (spread("yankos", "High", 24, 6, 2.0, -1.0) + spread("yankos", "Medium", 18, 12, 1.5, -1.0)
                + spread("yankos", "Low", 12, 18, 1.0, -1.0))
        result = tc.calibrate(rows)["results"][0]
        self.assertEqual(result["ordering"], "ordered")
        self.assertEqual(result["by_confidence"]["High"]["win_rate_pct"], 80.0)

    def test_reversed_labels_are_inverted_and_mixed_ones_mixed(self):
        inverted = (spread("yankos", "High", 12, 18, 1.0, -1.0) + spread("yankos", "Medium", 18, 12, 1.5, -1.0)
                    + spread("yankos", "Low", 24, 6, 2.0, -1.0))
        self.assertEqual(tc.calibrate(inverted)["results"][0]["ordering"], "inverted")
        mixed = (spread("yankos", "High", 18, 12, 1.5, -1.0) + spread("yankos", "Medium", 24, 6, 2.0, -1.0)
                 + spread("yankos", "Low", 12, 18, 1.0, -1.0))
        self.assertEqual(tc.calibrate(mixed)["results"][0]["ordering"], "mixed")

    def test_labels_without_enough_decisions_are_left_out_of_the_comparison(self):
        rows = spread("yankos", "High", 24, 6, 2.0, -1.0) + spread("yankos", "Medium", 12, 18, 1.0, -1.0)
        rows += spread("yankos", "Low", 1, 1, 9.0, -9.0)  # too few to judge, must not break the ordering
        self.assertEqual(tc.calibrate(rows)["results"][0]["ordering"], "ordered")

    def test_a_single_judged_label_is_insufficient(self):
        rows = spread("yankos", "High", 24, 6, 2.0, -1.0)
        self.assertEqual(tc.calibrate(rows)["results"][0]["ordering"], "insufficient_sample")

    def test_small_samples_are_not_judged(self):
        rows = spread("yankos", "High", 5, 1, 2.0, -1.0) + spread("yankos", "Low", 2, 4, 1.0, -1.0)
        self.assertEqual(tc.calibrate(rows)["results"][0]["ordering"], "insufficient_sample")

    def test_a_decision_shared_by_playbooks_counts_once_per_agent(self):
        shared = [row("yankos", "High", 1.0, "AAA", playbook=name) for name in ("a", "b", "c", "d")]
        other_agent = [row("periklis", "High", 1.0, "AAA")]
        payload = tc.calibrate(shared + other_agent)
        self.assertEqual(payload["unique_decisions"], 2)
        self.assertEqual({r["agent"]: r["decisions"] for r in payload["results"]}, {"yankos": 1, "periklis": 1})

    def test_horizons_and_agents_are_kept_apart_and_unknown_returns_are_skipped(self):
        rows = [row("yankos", "High", 1.0, "A", horizon=1), row("yankos", "High", 1.0, "B", horizon=5),
                row("yankos", "High", None, "C", horizon=5)]
        payload = tc.calibrate(rows)
        self.assertEqual(sorted(r["horizon_days"] for r in payload["results"]), [1, 5])
        self.assertEqual(payload["unique_decisions"], 2)

    def test_missing_probability_is_reported_not_invented(self):
        payload = tc.calibrate([row("yankos", "High", 1.0, "A")])
        self.assertEqual(payload["decisions_with_probability"], 0)
        self.assertIn("no confidence_probability", payload["probability_note"])
        self.assertIn("Trader calibration by risk tier", tc.render_markdown(payload))


if __name__ == "__main__":
    unittest.main()


class BenchmarkAdjustTests(unittest.TestCase):
    def test_a_falling_market_no_longer_makes_shorts_look_skilful(self):
        from datetime import date

        closes = {date(2026, 10, 1): 100.0, date(2026, 10, 2): 98.0}  # SPY -2 %
        base = {"source_run_timestamp": "2026-10-01T23:30:00+00:00", "target_session_date": "2026-10-02"}
        long_row = {**base, "trade_direction": "long", "raw_symbol_return_pct": -1.0, "directional_return_pct": -1.0}
        short_row = {**base, "trade_direction": "short", "raw_symbol_return_pct": -1.0, "directional_return_pct": 1.0}
        adjusted = tc.benchmark_adjust([long_row, short_row], closes)
        self.assertAlmostEqual(adjusted[0]["directional_return_pct"], 1.0)   # fell less than the market
        self.assertAlmostEqual(adjusted[1]["directional_return_pct"], -1.0)  # short lost against the market
        self.assertEqual(adjusted[1]["raw_directional_return_pct"], 1.0)

    def test_rows_without_a_benchmark_are_left_out_not_kept_raw(self):
        row = {"source_run_timestamp": "2026-10-01T23:30:00+00:00", "target_session_date": "2026-10-02",
               "trade_direction": "long", "raw_symbol_return_pct": 1.0, "directional_return_pct": 1.0}
        self.assertEqual(tc.benchmark_adjust([row], None), [])
        self.assertEqual(tc.benchmark_adjust([{**row, "trade_direction": "flat"}], {}), [])


class RiskTierAndTrackRecordTests(unittest.TestCase):
    def test_new_signals_are_grouped_by_their_risk_tier(self):
        rows = [dict(r, confidence="insufficient_history", risk_tier=r["confidence"])
                for r in spread("yankos", "High", 24, 6, 2.0, -1.0) + spread("yankos", "Medium", 12, 18, 1.0, -1.0)]
        self.assertEqual(tc.calibrate(rows)["results"][0]["ordering"], "ordered")

    def test_track_record_judges_a_playbook_only_with_enough_days(self):
        import trader_confidence
        few_days = spread("yankos", "High", 24, 16, 1.0, -1.0, horizon=3)  # 40 decisions, one day
        record = trader_confidence.build_track_record(few_days)
        cell = record["cells"]["yankos"]["p|long"]
        self.assertEqual((cell["decisions"], cell["days"], cell["status"]), (40, 1, "insufficient_history"))
        many = [dict(r, source_run_timestamp=f"2026-09-{1 + i % 25:02d}T10:00") for i, r in enumerate(few_days)]
        judged = trader_confidence.build_track_record(many)["cells"]["yankos"]["p|long"]
        self.assertEqual(judged["status"], "judged")
        shadow = trader_confidence.shadow_confidence("yankos", "p", "long", {"cells": {"yankos": {"p|long": judged}}})
        self.assertAlmostEqual(shadow["value"], judged["wilson_lower_pct"] / 100.0, places=4)
        pending = trader_confidence.shadow_confidence("yankos", "p", "long", record)
        self.assertIsNone(pending["value"])

    def test_trade_plan_sizes_exactly_as_before(self):
        import trader_confidence
        self.assertEqual(trader_confidence.sizing_label({"confidence": "insufficient_history", "risk_tier": "High"}), "High")
        self.assertEqual(trader_confidence.sizing_label({"confidence": "Medium"}), "Medium")  # older rows
        self.assertEqual(trader_confidence.sizing_label({}), "Low")
