"""Recheck at the open: the opening gap decides again; material 8-Ks need a market not against the trade; unknown
stays on watch; the what-if is judged only with enough evidence."""
from __future__ import annotations

from datetime import date
import unittest

import pandas as pd

import paper_learning as pl
import preopen_recheck as pr

GAP = "Pre-market gap -2.85% against the long trade is larger than 1x the 14-day ATR (2.39%)."
RESULTS = "New SEC event after the last close (8-K item 2.02); the fundamental review has not read it."
REG_FD = "New SEC event after the last close (8-K item 7.01); the fundamental review has not read it."


def view(gap_pct, atr=2.0):
    return {"open": 100 * (1 + gap_pct / 100), "previous_close": 100.0, "atr_pct": atr}


class RecheckTests(unittest.TestCase):
    def test_a_premarket_gap_that_is_gone_at_the_open_comes_back(self):
        self.assertEqual(pr.recheck("long", [GAP], view(-0.6))["decision"], "restored")      # VRTX 2026-10-21
        self.assertEqual(pr.recheck("long", [GAP], view(-3.0))["decision"], "stays_watch")
        self.assertEqual(pr.recheck("short", [GAP], view(-3.0))["decision"], "restored")     # gap with a short

    def test_a_material_8k_needs_the_open_not_against_the_trade(self):
        self.assertEqual(pr.recheck("long", [RESULTS], view(-0.3))["decision"], "restored")    # 0.15 x ATR
        self.assertEqual(pr.recheck("long", [RESULTS], view(-1.5))["decision"], "stays_watch")  # 0.75 x ATR
        self.assertEqual(pr.recheck("short", [RESULTS], view(-6.6))["decision"], "restored")  # NFLX short
        self.assertEqual(pr.recheck("long", [REG_FD], view(-1.5))["decision"], "restored")     # supporting item
        self.assertEqual(pr.recheck("long", [REG_FD], view(-3.0))["decision"], "stays_watch")
        both = pr.recheck("long", [GAP, RESULTS], view(-1.2))
        self.assertEqual((both["decision"], both["items"]), ("stays_watch", ["2.02"]))

    def test_unknown_prices_keep_the_idea_on_watch(self):
        self.assertEqual(pr.recheck("long", [GAP], {"open": None, "previous_close": 100.0, "atr_pct": 2.0})["decision"],
                         "stays_watch")
        self.assertEqual(pr.recheck("long", ["something else"], view(0.0))["decision"], "stays_watch")

    def test_opening_view_uses_the_official_open_and_the_atr_before_the_session(self):
        index = pd.bdate_range("2026-10-01", periods=16)
        frame = pd.DataFrame({"Open": [100.0] * 15 + [98.0], "High": [101.0] * 16, "Low": [99.0] * 16,
                              "Close": [100.0] * 16}, index=index)
        got = pr.opening_view(frame, index[-1].date())
        self.assertEqual((got["open"], got["previous_close"], round(got["atr_pct"], 6)), (98.0, 100.0, 2.0))
        self.assertIsNone(pr.opening_view(frame, date(2026, 12, 1))["open"])


class EvaluateTests(unittest.TestCase):
    def test_restored_and_stayed_are_judged_against_each_other(self):
        index = pd.bdate_range("2026-09-01", periods=60)
        frame = pd.DataFrame({"Open": [100.0] * 60, "High": [101.0] * 60, "Low": [99.0] * 60, "Close": [100.0] * 60},
                             index=index)
        records = []
        for k, day in enumerate(index[20:56]):
            for ticker, gap_reason, outcome in (("GOOD", GAP, 1.0), ("BAD", RESULTS, -1.0)):
                records.append({"kind": "preopen_demoted", "direction": "long", "ticker": ticker, "agent": "yankos",
                                "playbook": "p", "session_date": str(day.date()),
                                "preopen": {"reasons": [gap_reason], "gap_pct": -2.0},
                                "outcome_pct": outcome, "excess_pct": outcome})
        # GOOD opens flat (its pre-market gap is gone); BAD has results and opens 3% down against the long
        frames = {"GOOD": frame, "BAD": frame.assign(Open=[97.0] * 60)}.get
        result = pr.evaluate(records, frames, pl.metrics, pl.MARGIN_PCT)
        self.assertEqual((result["ideas"], result["restored"]), (72, 36))
        self.assertEqual(result["verdict"], "restoring at the open would have helped")
        self.assertEqual(pr.proposal(result)[0]["target_agent"], "preopen")
        self.assertFalse(result["active_decisions_changed"])
        few = pr.evaluate(records[:4], frames, pl.metrics, pl.MARGIN_PCT)
        self.assertEqual((few["verdict"], pr.proposal(few)), ("insufficient_sample", []))


if __name__ == "__main__":
    unittest.main()
