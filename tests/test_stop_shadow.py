"""The held-position stop in shadow: live widening stop against the class-wide stop, judged only on whole windows."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

import stop_shadow as ss


def bars(closes: list[float], lows: list[float] | None = None, start: str = "2026-01-05") -> pd.DataFrame:
    index = pd.bdate_range(start, periods=len(closes))
    lows = lows or closes
    return pd.DataFrame({"Open": closes, "High": closes, "Low": lows, "Close": closes}, index=index)


def position(day: str = "2026-01-05", sessions: int = 4, atr: float | None = 2.0, **extra) -> dict:
    return {"agent": "yankos", "symbol": "T", "playbook": "support_bounce_swing", "direction": "long",
            "holding_class": "quarter", "opened_session": day, "horizon_sessions": sessions, "atr_14_pct": atr,
            "entry_price": 100.0, "filled_at": f"{day}T15:00:00Z", **extra}


class ReplayTests(unittest.TestCase):
    def test_the_live_stop_cuts_a_dip_the_class_stop_rides_out(self):
        # ATR 2%: live stop 2.2% on session 1, 3.11% on 2 (level 96.89, touched by the 96.5 low after a 97 open);
        # class stop (4 sessions) 4.4% throughout
        frame = bars([100, 97, 99, 106], lows=[100, 96.5, 99, 106])
        live = ss.replay(frame, date(2026, 1, 5), 100.0, 2.0, 4, "long", "live")
        wide = ss.replay(frame, date(2026, 1, 5), 100.0, 2.0, 4, "long", "class")
        self.assertEqual((live["exit"], live["sessions_held"]), ("stop", 2))
        self.assertEqual(live["result_pct"], -3.111)
        self.assertEqual((wide["exit"], wide["result_pct"]), ("time", 6.0))

    def test_a_window_not_stored_yet_is_running_not_final(self):
        out = ss.replay(bars([100, 101]), date(2026, 1, 5), 100.0, 2.0, 4, "long", "class")
        self.assertEqual((out["exit"], out["final"]), ("running", False))

    def test_a_short_is_stopped_on_the_way_up(self):
        out = ss.replay(bars([100, 110, 100, 100]), date(2026, 1, 5), 100.0, 2.0, 4, "short", "class")
        self.assertEqual(out["exit"], "stop_gap")
        self.assertEqual(out["result_pct"], -10.0)


class EvaluationTests(unittest.TestCase):
    def test_unknown_atr_stays_unknown_and_running_positions_are_not_judged(self):
        frame = bars([100, 97, 99, 106], lows=[100, 96.5, 99, 106])
        rows = ss.build([position(), position(atr=None, symbol="U"), position(sessions=10)], lambda s: frame)
        self.assertEqual([r["known"] for r in rows], [True, False, True])
        self.assertEqual([r["mature"] for r in rows], [True, False, False])
        result = ss.evaluate(rows, 0.5)
        self.assertEqual((result["unknown"], result["mature"], result["running"]), (1, 1, 1))
        self.assertEqual(result["verdict"], "insufficient_sample")
        self.assertEqual(ss.proposal(result), [])
        self.assertFalse(result["active_decisions_changed"])

    def test_enough_whole_windows_give_a_verdict_and_a_candidate_only(self):
        cases, frames = [], {}
        for k in range(36):
            day = pd.bdate_range("2026-01-05", periods=40)[k]
            symbol = f"S{k}"
            frames[symbol] = bars([100, 97, 99, 106], lows=[100, 96.5, 99, 106], start=str(day.date()))
            cases.append(position(day=str(day.date()), symbol=symbol))
        result = ss.evaluate(ss.build(cases, frames.get), 0.5)
        self.assertEqual(result["verdict"], "class stop did better")
        self.assertEqual(result["judged"]["class_minus_live_pct"], 9.111)
        found = ss.proposal(result)
        self.assertEqual(found[0]["target_agent"], "position_monitor")
        self.assertIn("stopped position loses", found[0]["proposed_change"])
        self.assertIn("φαρδύ", ss.render(result))

    def test_positions_are_read_once_from_the_open_and_closed_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "open.json").write_text(json.dumps({"T": position(), "D": position(sessions=None)}), encoding="utf-8")
            (root / "closed.jsonl").write_text(json.dumps({**position(), "close_reason": "scaled_stop"}) + "\n" +
                                               json.dumps(position(day="2026-01-06")) + "\n", encoding="utf-8")
            found = ss.positions(root / "open.json", root / "closed.jsonl")
        self.assertEqual(len(found), 2)  # the same position once; no planned sessions -> not a held position


    def test_the_learning_report_carries_the_stop_shadow_line(self):
        import paper_learning as pl
        frame = bars([100, 97, 99, 106], lows=[100, 96.5, 99, 106])
        result = ss.evaluate(ss.build([position()], lambda s: frame), 0.5)
        evaluation = pl.evaluate([])
        evaluation["stop_shadow"] = {k: result[k] for k in ("positions", "unknown", "mature", "mature_days", "running",
                                                            "judged", "running_view", "verdict")}
        text = pl.render_report("week", "w", date(2026, 1, 5), date(2026, 1, 9), evaluation, pl.trade_review([]), [])
        self.assertIn("σημερινό ή φαρδύ", text)
        self.assertIn("stop_shadow_latest.md", text)


if __name__ == "__main__":
    unittest.main()
