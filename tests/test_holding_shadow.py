"""Shadow multi-day holds: entry at the session open, exits per rule, pending until the window is stored."""
from __future__ import annotations

import sqlite3
import unittest

import pandas as pd

import holding_shadow as hs
import paper_learning as pl


def frame(opens, highs, lows, closes, start="2026-10-15"):
    index = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes}, index=index)


FLAT = frame([100.0] * 70, [101.0] * 70, [99.0] * 70, [100.0] * 70)


class HoldTests(unittest.TestCase):
    def test_fixed_enters_at_the_open_and_exits_at_the_last_close_of_the_class(self):
        f = frame([100, 101, 102, 103, 104, 105], [101, 102, 103, 104, 105, 106], [99, 100, 101, 102, 103, 104],
                  [101, 102, 103, 104, 110, 106])
        out = hs.hold(f, f.index[0].date(), 5, "long", "fixed", 0.03, 0.06)
        self.assertEqual((out["sessions_held"], out["exit_price"], out["result_pct"]), (5, 110.0, 10.0))
        self.assertEqual(hs.hold(f, f.index[0].date(), 1, "short", "fixed", 0.03, 0.06)["result_pct"], -1.0)

    def test_a_window_not_stored_yet_is_pending(self):
        f = frame([100] * 3, [101] * 3, [99] * 3, [100] * 3)
        self.assertIsNone(hs.hold(f, f.index[0].date(), 5, "long", "fixed", 0.03, 0.06))
        self.assertIsNone(hs.hold(f, pd.Timestamp("2026-10-14").date(), 1, "long", "fixed", 0.03, 0.06))

    def test_the_bracket_uses_the_records_own_limits_and_a_gap_exits_at_the_open(self):
        f = frame([100, 99, 95, 100], [101, 100, 96, 101], [99, 97.5, 94, 99], [100, 99, 95, 100])
        out = hs.hold(f, f.index[0].date(), 4, "long", "atr_bracket", 0.02, 0.06)
        self.assertEqual((out["exit_price"], out["sessions_held"]), (98.0, 2))   # the 2% stop (98) touched on day 2
        gap = hs.hold(f, f.index[0].date(), 4, "long", "atr_bracket", 0.04, 0.06)
        self.assertEqual((gap["exit_price"], gap["sessions_held"]), (95.0, 3))    # opened at 95 below the 96 stop
        short = hs.hold(f, f.index[0].date(), 4, "short", "atr_bracket", 0.02, 0.04)
        self.assertEqual(short["exit_price"], 96.0)                               # target 96 touched on day 3


class FillTests(unittest.TestCase):
    def test_fill_once_with_spy_excess_and_attach(self):
        conn = sqlite3.connect(":memory:")
        stock = FLAT.copy()
        stock.loc[stock.index[4], "Close"] = 105.0
        frames = {"ABC": stock, "SPY": FLAT}.get
        record = {"record_id": "r1", "ticker": "ABC", "session_date": "2026-10-15", "direction": "long",
                  "stop_loss_pct": None, "take_profit_pct": None}
        added = hs.fill(conn, [record], frames)
        self.assertEqual(added, 14)  # 4 classes x 2 rules + thesis and thesis_shock for week/month/quarter
        self.assertEqual(hs.fill(conn, [record], frames), 0)
        hs.attach(conn, [record])
        self.assertEqual(record["holds"]["week"]["fixed"]["excess_pct"], 5.0)
        self.assertEqual(record["holds"]["week"]["atr_bracket"]["sessions_held"], 5)  # 3% / 6% never touched

    def test_summary_keeps_unknown_as_unknown(self):
        records = [{"record_id": "a", "session_date": "2026-10-15", "agent": "yankos", "playbook": "p",
                    "direction": "long", "holds": {"day": {"fixed": {"result_pct": 1.0, "excess_pct": 0.5,
                                                                      "sessions_held": 1}}}}]
        summary = hs.summarize(records, pl.metrics)
        self.assertEqual(summary["overall"]["day · fixed"]["known"], 1)
        self.assertEqual(summary["overall"]["quarter · fixed"]["known"], 0)
        self.assertIsNone(summary["overall"]["quarter · fixed"]["mean_pct"])
        self.assertEqual(summary["overall"]["day · fixed"]["status"], "insufficient_sample")


if __name__ == "__main__":
    unittest.main()


class LessonTests(unittest.TestCase):
    def cell(self, excess, known=40, days=12, beat=60.0):
        return {"known": known, "days": days, "mean_excess_pct": excess, "beat_spy_pct": beat,
                "status": "judged" if known >= pl.MIN_TRADES and days >= pl.MIN_DAYS else "insufficient_sample"}

    def test_a_longer_hold_or_thesis_exit_becomes_a_candidate_only_with_judged_evidence(self):
        holding = {"strategies": {"yankos · support_bounce_swing · long": {
            "day · fixed": self.cell(0.1), "week · fixed": self.cell(0.4), "month · fixed": self.cell(1.2),
            "month · thesis": self.cell(2.0), "quarter · fixed": self.cell(5.0, known=10)}}}
        items = {i["criterion_id"]: i for i in pl.holding_proposals(holding)}
        self.assertIn("holding_class:yankos:support_bounce_swing:long:month:fixed", items)
        self.assertIn("holding_class:yankos:support_bounce_swing:long:month:thesis", items)
        self.assertIn("thesis_exit:yankos:support_bounce_swing:long:month", items)
        self.assertNotIn("holding_class:yankos:support_bounce_swing:long:week:fixed", items)  # below the margin
        self.assertFalse(any(":quarter:" in k for k in items))                                # sample too small
        self.assertEqual(items["holding_class:yankos:support_bounce_swing:long:month:fixed"]["target_agent"], "yankos")
        self.assertEqual(pl.holding_proposals({}), [])
