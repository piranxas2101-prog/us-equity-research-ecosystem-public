"""After a direction flip the opposite position is only measured: Koulis's own-position block counts as passing, unknown
stays unknown, windows not stored stay pending, and a verdict needs judged evidence with the stop."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import pandas as pd

import flip_shadow as fs
import paper_learning as pl

SESSION = "2026-11-06"


def closed_line(symbol="VISN", reason="thesis_direction_flip", direction="short", atr=3.0):
    return json.dumps({"symbol": symbol, "agent": "periklis", "playbook": "momentum_12_1", "direction": direction,
                       "close_reason": reason, "closed_at": "2026-11-06T14:38:11+00:00", "exit_plpc": 0.16,
                       "atr_14_pct": atr})


def frame(closes, opens=None, lows=None, start="2026-11-06"):
    opens = opens or closes
    lows = lows or [min(o, c) for o, c in zip(opens, closes)]
    index = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({"Open": opens, "High": [max(o, c) for o, c in zip(opens, closes)], "Low": lows,
                         "Close": closes}, index=index)


def flip_case(**extra):
    return {"flip_id": "f1", "session_date": SESSION, "ticker": "VISN", "direction": "long", "candidate": True,
            "passes_koulis": True, "miltos_entered": True, "miltos_entry_price": 100.0, "atr_14_pct": 3.0, **extra}


class FlipsTests(unittest.TestCase):
    def test_only_flip_closes_count_and_the_opposite_is_measured(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "closed.jsonl"
            path.write_text("\n".join([closed_line(), closed_line("EFX", "owner_close"), "not json",
                                       closed_line("ABC", direction="long")]), encoding="utf-8")
            out = fs.flips(path)
        self.assertEqual([(f["ticker"], f["direction"]) for f in out], [("VISN", "long"), ("ABC", "short")])
        self.assertEqual(out[0]["session_date"], SESSION)   # the New York session
        self.assertEqual(out[0]["closed_result_pct"], 16.0)

    def test_no_closed_log_is_no_flip(self):
        self.assertEqual(fs.flips(Path("no_such_file.jsonl")), [])


class KoulisViewTests(unittest.TestCase):
    flip = {"session_date": SESSION, "ticker": "VISN", "direction": "long"}

    def koulis(self, approved=(), blocked=()):
        return {"session_date": SESSION, "approved_intents": list(approved), "blocked_intents": list(blocked)}

    def test_another_sessions_file_is_unknown_not_a_pass(self):
        self.assertIsNone(fs.koulis_view({"session_date": "2026-11-05"}, self.flip))
        self.assertIsNone(fs.koulis_view(None, self.flip))

    def test_the_position_being_closed_does_not_count_against_it(self):
        row = {"ticker": "VISN", "trade_direction": "long", "block_reason": "existing_account_exposure"}
        self.assertEqual(fs.koulis_view(self.koulis(blocked=[row]), self.flip)["status"], "only_own_position")

    def test_another_block_stays_a_block_and_the_short_row_is_not_read(self):
        rows = [{"ticker": "VISN", "trade_direction": "long", "block_reason": "results_ahead"},
                {"ticker": "VISN", "trade_direction": "short", "block_reason": "existing_account_exposure"}]
        view = fs.koulis_view(self.koulis(blocked=rows), self.flip)
        self.assertEqual((view["status"], view["reasons"]), ("blocked", ["results_ahead"]))

    def test_approved_and_not_seen(self):
        approved = {"ticker": "VISN", "trade_direction": "long"}
        self.assertEqual(fs.koulis_view(self.koulis(approved=[approved]), self.flip)["status"], "approved")
        self.assertEqual(fs.koulis_view(self.koulis(), self.flip)["status"], "not_seen")


class ForwardTests(unittest.TestCase):
    session = date(2026, 11, 6)

    def test_a_window_not_stored_stays_pending(self):
        self.assertIsNone(fs.forward(frame([100, 101]), self.session, 100, 3, "long", "fixed", 3.0, checks_entry_day=True))

    def test_fixed_counts_the_entry_session(self):
        out = fs.forward(frame([100, 101, 110]), self.session, 100, 3, "long", "fixed", 3.0, checks_entry_day=True)
        self.assertEqual((out["result_pct"], out["sessions_held"]), (10.0, 3))

    def test_the_stop_exits_and_unknown_atr_gives_no_result(self):
        data = frame([100, 90, 110], lows=[100, 90, 110])
        out = fs.forward(data, self.session, 100, 3, "long", "stop", 3.0, checks_entry_day=True)
        self.assertEqual(out["sessions_held"], 2)
        self.assertLess(out["result_pct"], 0)
        self.assertIsNone(fs.forward(data, self.session, 100, 3, "long", "stop", None, checks_entry_day=True))

    def test_a_miltos_entry_ignores_the_lows_before_it(self):
        data = frame([100, 101, 102], opens=[100, 101, 102], lows=[80, 100, 101])
        kept = fs.forward(data, self.session, 100, 3, "long", "stop", 3.0, checks_entry_day=False)
        self.assertEqual(kept["sessions_held"], 3)
        out = fs.forward(data, self.session, 100, 3, "long", "stop", 3.0, checks_entry_day=True)
        self.assertEqual(out["sessions_held"], 1)


class CasesAndVerdictTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)

    def test_koulis_is_kept_once_read_and_a_missing_candidate_is_no_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "closed.jsonl"
            log.write_text(closed_line(), encoding="utf-8")
            koulis = {"session_date": SESSION, "approved_intents": [], "blocked_intents": [
                {"ticker": "VISN", "trade_direction": "long", "block_reason": "existing_account_exposure"}]}
            first = fs.build_cases(self.conn, log, [], koulis)[0]
            later = fs.build_cases(self.conn, log, [], {"session_date": "2026-11-09"})[0]
        self.assertEqual((first["koulis"], later["koulis"], later["passes_koulis"]),
                         ("only_own_position", "only_own_position", True))
        self.assertFalse(later["candidate"])
        self.assertIsNone(later["miltos_entered"])     # no replay: unknown, not "did not enter"

    def test_fill_writes_each_window_once_and_no_entry_is_not_a_zero(self):
        frames = {"VISN": frame([100, 101, 102, 103, 104, 105]), "SPY": frame([100] * 6)}.get
        cases = [flip_case(), {**flip_case(), "flip_id": "f2", "miltos_entered": False, "miltos_entry_price": None}]
        added = fs.fill(self.conn, cases, frames)
        self.assertEqual(fs.fill(self.conn, cases, frames), 0)
        self.assertGreater(added, 0)
        miltos = self.conn.execute("SELECT DISTINCT flip_id FROM paper_flip_shadows WHERE entry_rule='miltos'").fetchall()
        self.assertEqual(miltos, [("f1",)])
        horizons = {r[0] for r in self.conn.execute("SELECT horizon FROM paper_flip_shadows")}
        self.assertEqual(horizons, {1, 3, 5})           # 10 and 20 sessions are not stored yet

    def insert(self, count, days, excess):
        fs.ensure_tables(self.conn)
        for i in range(count):
            self.conn.execute("INSERT INTO paper_flip_shadows VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                              (f"f{i}", "miltos", "stop", 5, f"2026-11-{i % days + 1:02d}", 100.0, "2026-12-01", 5,
                               excess, 0.0, excess, "now"))
        return [flip_case(flip_id=f"f{i}") for i in range(count)]

    def test_few_flips_give_no_verdict_and_no_lesson(self):
        result = fs.evaluate(self.conn, self.insert(12, 6, 5.0), pl.metrics, pl.MARGIN_PCT)
        self.assertEqual(result["verdict"], "insufficient_sample")
        self.assertEqual(fs.proposal(result), [])
        self.assertFalse(result["active_decisions_changed"])

    def test_judged_evidence_only_proposes_a_lesson(self):
        result = fs.evaluate(self.conn, self.insert(40, 12, 2.0), pl.metrics, pl.MARGIN_PCT)
        self.assertEqual((result["verdict"], result["passed_horizons"]), ("the opposite entry would have helped", [5]))
        self.assertEqual(fs.proposal(result)[0]["criterion_id"], "flip_opposite_entry")

    def test_judged_evidence_without_a_gain_stays_no(self):
        result = fs.evaluate(self.conn, self.insert(40, 12, -1.0), pl.metrics, pl.MARGIN_PCT)
        self.assertEqual(result["verdict"], "no clear gain")

    def test_only_a_known_pass_reaches_the_gated_group(self):
        cases = self.insert(2, 2, 1.0)
        cases[1]["passes_koulis"] = None
        result = fs.evaluate(self.conn, cases, pl.metrics, pl.MARGIN_PCT)
        self.assertEqual(result["summary"]["gated · stop · 5"]["known"], 1)
        self.assertEqual(result["summary"]["miltos · stop · 5"]["known"], 2)

    def test_render_shows_unknown_in_words(self):
        result = fs.evaluate(self.conn, [{**flip_case(), "closed_direction": "short", "closed_result_pct": None,
                                          "candidate_playbook": None, "koulis": None, "koulis_reasons": []}],
                             pl.metrics, pl.MARGIN_PCT)
        self.assertIn("άγνωστο", fs.render(result))


if __name__ == "__main__":
    unittest.main()
