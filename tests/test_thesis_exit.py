"""Thesis exit: each holding class judges the position on its own clock; unknown boards hold; stops widen with time."""
from __future__ import annotations

from datetime import date
import math
import unittest

import pandas as pd

import thesis_exit as te

OPEN_LONG = {"agent": "yankos", "ticker": "ABC", "playbook": "support_bounce_swing", "direction": "long",
             "giannis_score": 7.5, "atr_14_pct": 2.0}


def row(verdict="candidate", direction="long", playbook="support_bounce_swing", giannis=7.5, risk="Low"):
    return {"playbook_name": playbook, "signal_verdict": verdict, "trade_direction": direction, "signal_score": 8.0,
            "giannis_score": giannis, "risk_level": risk, "atr_14_pct": 2.0}


class BreakTests(unittest.TestCase):
    def test_week_is_technical_month_needs_two_nights_quarter_is_fundamental(self):
        gone = [row(verdict="reject", direction="flat")]
        self.assertEqual(te.thesis_break("week", OPEN_LONG, gone, {}), "setup_gone")
        state: dict[str, int] = {}
        self.assertIsNone(te.thesis_break("month", OPEN_LONG, gone, state))       # one weak night is noise
        self.assertEqual(te.thesis_break("month", OPEN_LONG, gone, state), "setup_gone_nights")
        self.assertIsNone(te.thesis_break("quarter", OPEN_LONG, gone, {}))         # technical only via the stop
        self.assertEqual(te.thesis_break("quarter", OPEN_LONG, [row(giannis=6.4)], {}), "fundamentals_weaker")
        self.assertEqual(te.thesis_break("month", OPEN_LONG, [row(risk="High")], {}), "risk_high")
        self.assertIsNone(te.thesis_break("week", OPEN_LONG, [row(giannis=5.0)], {}))  # fundamentals not a week's clock

    def test_a_flip_closes_week_and_month_and_a_daily_event_reject_is_not_a_break(self):
        flip = [row(), row(direction="short", playbook="gap_fade")]
        self.assertEqual(te.thesis_break("week", OPEN_LONG, flip, {}), "direction_flip")
        self.assertIsNone(te.thesis_break("quarter", OPEN_LONG, flip, {}))
        event = dict(OPEN_LONG, playbook="rsi2_mean_reversion")
        self.assertIsNone(te.thesis_break("week", event, [row(verdict="reject", direction="flat",
                                                               playbook="rsi2_mean_reversion")], {}))

    def test_missing_from_the_board_or_unknown_scores_hold(self):
        self.assertIsNone(te.thesis_break("week", OPEN_LONG, None, {}))
        self.assertIsNone(te.thesis_break("quarter", dict(OPEN_LONG, giannis_score=None), [row(giannis=1.0)], {}))


class ReplayTests(unittest.TestCase):
    def frame(self, n=25):
        index = pd.bdate_range("2026-10-05", periods=n)
        return pd.DataFrame({"Open": [100.0] * n, "High": [101.0] * n, "Low": [99.0] * n,
                             "Close": [100.0 + k for k in range(n)]}, index=index)

    def test_the_stop_widens_with_the_square_root_of_time_within_bounds(self):
        self.assertAlmostEqual(te.scaled_stop(2.0, 5), 0.022 * math.sqrt(5))
        self.assertEqual(te.scaled_stop(0.5, 1), te.STOP_FLOOR)
        self.assertEqual(te.scaled_stop(6.0, 60), te.STOP_CAP)
        self.assertIsNone(te.scaled_stop(None, 5))

    def test_a_break_at_night_exits_at_the_next_open_and_fixed_ignores_it(self):
        f = self.frame()
        nights = {2: {"yankos": {"ABC": [row(verdict="reject", direction="flat")]}}}
        out = te.replay(f, 0, "week", "thesis", OPEN_LONG, nights)
        self.assertEqual((out["exit_reason"], out["exit_index"], out["result_pct"]), ("setup_gone", 2, 0.0))
        fixed = te.replay(f, 0, "week", "fixed", OPEN_LONG, nights)
        self.assertEqual((fixed["exit_reason"], fixed["sessions_held"], fixed["result_pct"]), ("time_stop", 5, 4.0))
        self.assertIsNone(te.replay(f, 10, "month", "fixed", OPEN_LONG, nights))  # window not stored yet

    def test_weekend_runs_collapse_onto_monday(self):
        dates = pd.bdate_range("2026-10-08", periods=5).date  # Thu .. Wed
        boards = [(date(2026, 10, 9), "fri"), (date(2026, 10, 10), "sat"), (date(2026, 10, 11), "sun")]
        nights = te.nightly_sessions(boards, dates)
        self.assertEqual(nights, {1: "fri", 2: "sun"})  # Monday is index 2


if __name__ == "__main__":
    unittest.main()


class ShockTests(unittest.TestCase):
    def test_a_market_shock_against_the_position_exits_at_the_next_open(self):
        index = pd.bdate_range("2026-10-05", periods=25)
        frame = pd.DataFrame({"Open": [100.0] * 25, "High": [101.0] * 25, "Low": [99.0] * 25,
                              "Close": [100.0 + k for k in range(25)]}, index=index)
        shocks = {index[2].date(): "down"}
        out = te.replay(frame, 0, "week", "thesis_shock", OPEN_LONG, {}, shocks)
        self.assertEqual((out["exit_reason"], out["exit_index"]), ("market_shock", 3))
        short = dict(OPEN_LONG, direction="short")
        self.assertEqual(te.replay(frame, 0, "week", "thesis_shock", short, {}, shocks)["exit_reason"], "time_stop")
        self.assertEqual(te.replay(frame, 0, "week", "thesis", OPEN_LONG, {}, shocks)["exit_reason"], "time_stop")

    def test_shocks_from_spy(self):
        index = pd.bdate_range("2025-01-01", periods=260)
        close = [100.0 + 0.1 * k for k in range(260)]
        close[250] = close[249] * 0.95  # a 5% fall on calm days
        spy = pd.DataFrame({"High": [c * 1.005 for c in close], "Low": [c * 0.995 for c in close], "Close": close},
                           index=index)
        shocks = te.market_shocks(spy)
        self.assertEqual(shocks.get(index[250].date()), "down")
        self.assertNotIn(index[100].date(), shocks)
        self.assertEqual(te.market_shocks(None), {})
