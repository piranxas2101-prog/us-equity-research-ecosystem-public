"""Owner 2026-11-05: "a swing long needs time; it cannot open and close the same day". The playbooks in
``paper_session.swing_long_playbooks`` never get a same-day plan for a long: a real edge keeps its horizon, and
without one the horizon comes from the playbook's own catalog range and the curve's point estimates."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import holding_horizon as hh
import playbook_framework
import strategy_tiers
import trading_clock
import trader_trade_plan

ROOT = Path(__file__).resolve().parents[1]
SWING = {"early_accumulation_swing", "controlled_breakout_swing", "failed_breakdown_reclaim", "support_bounce_swing"}
ESTIMATES = {
    ("early_accumulation_swing", "long"): [{"sessions": 1, "edge_pct": 0.15}, {"sessions": 3, "edge_pct": 0.07},
                                           {"sessions": 5, "edge_pct": 0.20}, {"sessions": 10, "edge_pct": 0.11},
                                           {"sessions": 15, "edge_pct": 0.33}],
    ("controlled_breakout_swing", "long"): [{"sessions": 1, "edge_pct": -0.16}, {"sessions": 3, "edge_pct": -0.07}],
}
CURVE = {("support_bounce_swing", "long"): [{"sessions": 60, "edge_pct": 2.1, "edge_ci": None, "years": "4/5",
                                             "n": 100}]}


class DecideTests(unittest.TestCase):
    def decide(self, playbook: str, direction: str = "long", catalog: str = "3-10 sessions") -> dict:
        return hh.decide(playbook, direction, CURVE, swing=SWING, catalog_horizon=catalog, estimates=ESTIMATES)

    def test_best_positive_estimate_inside_the_catalog_range(self):
        got = self.decide("early_accumulation_swing")  # 1 and 15 sessions lie outside 3-10
        self.assertEqual((got["rule"], got["sessions"], got["basis"]), (hh.RULE, 5, hh.SWING_BASES[0]))
        self.assertEqual(got["minutes"], 5 * trading_clock.SESSION_MINUTES)
        self.assertFalse(got["stable_edge"])
        self.assertTrue(hh.valid(got))  # the monitors can act on it

    def test_no_positive_estimate_holds_for_the_range_lower_bound(self):
        got = self.decide("controlled_breakout_swing", catalog="1-5 sessions")
        self.assertEqual((got["rule"], got["sessions"], got["basis"], got["edge_pct"]),
                         (hh.RULE, 1, hh.SWING_BASES[1], None))

    def test_a_real_edge_keeps_its_horizon(self):
        got = self.decide("support_bounce_swing", catalog="1-5 sessions")
        self.assertEqual((got["sessions"], got["basis"], got["stable_edge"]), (60, "max_edge_per_sqrt_session", True))

    def test_shorts_other_playbooks_and_unknown_ranges_stay_as_before(self):
        self.assertEqual(self.decide("early_accumulation_swing", "short")["rule"], hh.SAME_DAY)
        self.assertEqual(self.decide("momentum_12_1")["rule"], hh.SAME_DAY)
        self.assertEqual(self.decide("early_accumulation_swing", catalog="intraday-3 sessions")["rule"], hh.SAME_DAY)
        self.assertEqual(hh.decide("early_accumulation_swing", "long", {})["rule"], hh.SAME_DAY)  # no swing set given

    def test_catalog_range(self):
        self.assertEqual(hh.catalog_range("3-10 sessions"), (3, 10))
        self.assertEqual(hh.catalog_range("1-5 sessions"), (1, 5))
        for text in ("intraday", "intraday-2 sessions", None, "agent horizon from the exit lab", "0-5 sessions"):
            self.assertIsNone(hh.catalog_range(text))

    def test_estimates_and_swing_list_read_from_files(self):
        with tempfile.TemporaryDirectory() as folder:
            curve = Path(folder) / "curve.json"
            curve.write_text(json.dumps({"horizons": {"s5": 5}, "results": [
                {"strategy": "x", "side": "long", "class": "s5", "rule": "fixed", "edge_vs_random_pct": -0.2,
                 "edge_verdict": "no_edge", "years_positive": 1, "years": 4, "n": 30},
                {"strategy": "x", "side": "long", "class": "s5", "rule": "thesis", "edge_vs_random_pct": 1.0}]}),
                encoding="utf-8")
            self.assertEqual(hh.load_estimates(curve)[("x", "long")][0]["edge_pct"], -0.2)
            self.assertEqual(len(hh.load_estimates(curve)[("x", "long")]), 1)
            self.assertEqual(hh.load_estimates(Path(folder) / "none.json"), {})
            config = Path(folder) / "config.json"
            config.write_text(json.dumps({"paper_session": {"swing_long_playbooks": ["a", "b"]}}), encoding="utf-8")
            self.assertEqual(hh.swing_long_playbooks(config), {"a", "b"})
            self.assertEqual(hh.swing_long_playbooks(Path(folder) / "none.json"), set())

    def test_the_owner_list_names_real_swing_playbooks(self):
        catalog = playbook_framework.catalog_by_id(playbook_framework.load_catalog(ROOT / "playbook_catalog_v2.json"))
        configured = hh.swing_long_playbooks()
        self.assertIn("early_accumulation_swing", configured)
        for playbook in configured:
            self.assertIsNotNone(hh.catalog_range(catalog[playbook].get("holding_horizon")), playbook)


class TierAndPlanTests(unittest.TestCase):
    def test_a_swing_long_is_never_a_same_day_tier(self):
        self.assertEqual(strategy_tiers.tier("early_accumulation_swing", "long", {}, set(), SWING), "horizon")
        self.assertEqual(strategy_tiers.tier("early_accumulation_swing", "short", {}, set(), SWING),
                         "same_day_measured")
        self.assertEqual(strategy_tiers.tier("momentum_12_1", "long", {}, set(), SWING), "same_day_measured")

    def test_the_trade_plan_freezes_the_swing_horizon(self):
        catalog = playbook_framework.catalog_by_id(playbook_framework.load_catalog(ROOT / "playbook_catalog_v2.json"))
        policy = json.loads((ROOT / "periklis_playbooks.json").read_text(encoding="utf-8"))["paper_sizing_policy"]
        row = {"agent_name": "periklis", "ticker": "TEST", "trade_direction": "long",
               "playbook_name": "early_accumulation_swing", "signal_score": 8.0, "signal_verdict": "candidate",
               "confidence": "High", "risk_level": "Low", "atr_14_pct": 4.0, "rationale": ["Accumulation."]}
        plan = trader_trade_plan.build_trade_plan("periklis", row, catalog["early_accumulation_swing"], policy,
                                                  "run_test", {}, swing=SWING, estimates=ESTIMATES)
        self.assertEqual(plan["holding_period_rule"], hh.RULE)
        self.assertEqual(plan["holding_horizon"]["sessions"], 5)
        self.assertFalse(plan["execution_authority"])


def held(day: int, same_day: float, week: float, playbook: str = "early_accumulation_swing") -> dict:
    return {"session_date": f"2026-11-{day:02d}", "agent": "periklis", "playbook": playbook, "direction": "long",
            "kind": "shadow_candidate", "holds": {"day": {"fixed": {"excess_pct": same_day}},
                                                  "week": {"fixed": {"excess_pct": week}}}}


class SwingHoldReviewTests(unittest.TestCase):
    """Owner 2026-11-05: the ecosystem itself says whether a swing long's hold should change or not."""

    def review(self, records: list[dict]) -> dict:
        import paper_learning as pl

        catalog = {"early_accumulation_swing": {"holding_horizon": "3-10 sessions"}}
        return pl.swing_hold_review(records, SWING, {}, ESTIMATES, catalog)[0]

    def test_collecting_until_enough_records_and_sessions(self):
        got = self.review([held(1 + i % 9, 0.0, -2.0) for i in range(60)])  # 9 sessions only
        self.assertEqual((got["verdict"], got["planned_sessions"], got["planned_class"]), ("collecting", 5, "week"))

    def test_clearly_worse_hold_says_close_the_same_day_and_becomes_a_lesson(self):
        import paper_learning as pl

        got = self.review([held(1 + i % 12, 0.5, -1.0 if i % 5 else 2.0) for i in range(60)])
        self.assertEqual(got["verdict"], "close_same_day")
        self.assertLess(got["vs_same_day"]["mean_gap_pct"], -0.5)
        lessons = [p for p in pl.proposals({"strategies": {}, "control_team": [], "swing_hold_review": [got]})]
        self.assertEqual([p["criterion_id"] for p in lessons],
                         ["swing_hold_review:periklis:early_accumulation_swing:long:close_same_day"])

    def test_clearly_better_hold_is_kept_and_no_difference_is_kept_without_a_lesson(self):
        import paper_learning as pl

        better = self.review([held(1 + i % 12, 0.0, 1.5 if i % 5 else -1.0) for i in range(60)])
        unclear = self.review([held(1 + i % 12, 0.0, 1.0 if i % 2 else -1.0) for i in range(60)])
        self.assertEqual((better["verdict"], unclear["verdict"]), ("keep_confirmed", "keep"))
        self.assertEqual(pl.proposals({"strategies": {}, "control_team": [],
                                       "swing_hold_review": [better, unclear]}), [])

    def test_unknown_holds_are_left_out_not_counted_as_zero(self):
        records = [held(1 + i % 12, 0.0, 1.5) for i in range(60)]
        records += [{**held(3, 0.0, 0.0), "holds": {"day": {"fixed": {"excess_pct": None}}}} for _ in range(50)]
        self.assertEqual(self.review(records)["vs_same_day"]["records"], 60)


if __name__ == "__main__":
    unittest.main()
