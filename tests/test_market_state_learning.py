"""Market state everywhere: rebuilt per session from SPY, the lab per state, Sokratis's lesson, outcomes per state."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

import macro_environment as me
import outcome_evaluator as oe
import sokratis_strategy_teacher as sokratis
import strategy_lab as lab
import trader_calibration as tc


def spy(closes) -> pd.DataFrame:
    return pd.DataFrame({"Close": closes}, index=pd.bdate_range("2022-01-03", periods=len(closes), name="Date"))


class MarketStateTests(unittest.TestCase):
    def test_states_follow_trend_and_volatility_and_early_sessions_have_none(self):
        rising = 100 * np.cumprod(np.full(260, 1.001))
        states = me.market_state_history(spy(rising))
        self.assertTrue(states.iloc[:199].isna().all())          # no 200-day average yet: no state
        self.assertEqual(states.iloc[-1], "up_calm")
        swings = 100 * np.cumprod(np.r_[np.full(240, 1.001), np.tile([0.94, 1.03], 20)])
        self.assertEqual(me.market_state_history(spy(swings)).iloc[-1], "down_volatile")

    def test_holding_view_leans_only_month_and_quarter(self):
        view = me.holding_class_view({"label": "supportive"})
        self.assertEqual((view["day"]["leans"], view["week"]["leans"], view["quarter"]["leans"]), (None, None, "long"))
        self.assertIsNone(me.holding_class_view({"label": "mixed"})["month"]["leans"])
        self.assertEqual(me.holding_class_view({"label": "hostile"})["month"]["leans"], "short")


class LabStateTests(unittest.TestCase):
    def test_edge_is_measured_against_random_entries_in_the_same_state(self):
        signals = pd.DataFrame({"strategy": ["momentum_12_1"] * 4, "side": ["long"] * 4,
                                "market_state": ["up_calm", "up_calm", "down_calm", None],
                                "x20": [1.0, 2.0, -1.0, 5.0]})
        baseline = {(20, "up_calm"): [np.array([1.0, -1.0])], (20, "down_calm"): [np.array([1.0, 1.0])]}
        row = lab.by_market_state(signals, baseline)[0]
        self.assertEqual(row["by_state"]["up_calm"]["edge_vs_random_pct_points"], 50.0)   # 100% vs 50%
        self.assertEqual(row["by_state"]["down_calm"]["edge_vs_random_pct_points"], -100.0)
        self.assertEqual(row["by_state"]["up_volatile"], {"n": 0})


class SokratisLessonTests(unittest.TestCase):
    def test_the_lesson_names_what_held_up_in_todays_state(self):
        lab_payload = {"by_market_state": [
            {"strategy": "momentum_12_1", "side": "long", "horizon_days": 20,
             "by_state": {"up_calm": {"n": 500, "edge_vs_random_pct_points": 2.0}}},
            {"strategy": "rsi2_mean_reversion", "side": "long", "horizon_days": 5,
             "by_state": {"up_calm": {"n": 500, "edge_vs_random_pct_points": -3.0}}},
            {"strategy": "gap_fade", "side": "short", "horizon_days": 3,
             "by_state": {"up_calm": {"n": 20, "edge_vs_random_pct_points": 9.0}}}]}
        lesson = sokratis.build_backdrop_lesson(lab_payload, {"market": {"state": "up_calm"}})
        verdicts = {s["strategy_side"]: s["verdict"] for v in lesson["traders"].values() for s in v["strategies"]}
        self.assertEqual(verdicts["momentum_12_1 long"], "works_in_state")
        self.assertEqual(verdicts["rsi2_mean_reversion long"], "weak_in_state")
        self.assertEqual(verdicts["gap_fade short"], "too_few_signals")
        self.assertEqual(sokratis.build_backdrop_lesson({}, {"market": {}})["status"], "unavailable")
        text = "\n".join(me.report_lines({"backdrop": {"label": "mixed"}}, lesson))
        self.assertIn("δούλεψαν momentum_12_1 long", text)


class OutcomeStateTests(unittest.TestCase):
    def test_outcomes_are_grouped_by_the_state_of_their_baseline_session(self):
        states = pd.Series(["up_calm", "down_volatile"], index=pd.to_datetime(["2026-10-01", "2026-10-02"]))
        rows = [{"agent_id": "petros", "horizon_days": 5, "label": label, "excess_return_pct": value,
                 "baseline_date": day} for label, value, day in
                (("candidate", 2.0, "2026-10-01"), ("watch", -1.0, "2026-10-01"), ("candidate", 1.0, "2026-10-02"),
                 ("watch", 0.5, "2026-10-09"))]
        oe.attach_market_states(rows, states)
        self.assertEqual([r["market_state"] for r in rows], ["up_calm", "up_calm", "down_volatile", None])
        grouped = {r["market_state"]: r for r in oe.evaluate_by_market_state(rows)}
        self.assertEqual(set(grouped), {"up_calm", "down_volatile"})
        self.assertEqual(grouped["up_calm"]["ordering"], "insufficient_sample")

    def test_trader_tiers_per_state_need_a_baseline_session(self):
        states = pd.Series(["up_calm"], index=pd.to_datetime(["2026-10-01"]))
        decisions = [{"agent_name": "yankos", "horizon_days": 5, "risk_tier": "High", "directional_return_pct": 1.0,
                      "baseline_date": "2026-10-01"},
                     {"agent_name": "yankos", "horizon_days": 5, "risk_tier": "High", "directional_return_pct": 1.0}]
        result = tc.by_market_state(decisions, states)
        self.assertEqual((len(result), result[0]["decisions"], result[0]["market_state"]), (1, 1, "up_calm"))
        self.assertEqual(tc.by_market_state(decisions, None), [])


if __name__ == "__main__":
    unittest.main()
