"""Fallback research (owner 2026-10-30): when tonight's full run did not complete and positions are held, a
research-only run on the last good shortlist and the held positions stands in for it - once a day, never on a day
the nightly run completed, and opening the computer starts it only between 07:00 Athens and 14:00 New York."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest

import fallback_research as fr

ATHENS = fr.ATHENS
FAILED = {"run_id": "full_research_20261030_003715", "status": "failed", "pid": 1,
          "finished_at": "2026-10-30T02:00:00+02:00"}
DONE_TODAY = {"run_id": "full_research_20261030_003715", "status": "completed_review_required",
              "finished_at": "2026-10-30T05:50:00+02:00"}
DONE_YESTERDAY = {**DONE_TODAY, "run_id": "full_research_20261029_003715", "finished_at": "2026-10-29T05:50:00+02:00"}


def at(hour: int, minute: int = 0, day: int = 30) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=ATHENS)   # 2026-10-30 is a Friday


def decide(now, trigger="nightly", pointer=FAILED, record=None, held=("AAA",), config=None, alive=lambda _p: False):
    return fr.decide(now, trigger=trigger, config=config, pointer=pointer, record=record, held=list(held),
                     alive=alive)


class DecideTests(unittest.TestCase):
    def test_failed_blocked_or_interrupted_nightly_with_held_positions_starts_it(self):
        self.assertTrue(decide(at(2))[0])                                   # failed tonight
        start, why = decide(at(0, 36), pointer=DONE_YESTERDAY)              # blocked: the pointer is yesterday's run
        self.assertTrue(start)
        self.assertIn("not completed today", why)
        self.assertTrue(decide(at(9), pointer={**FAILED, "status": "running_pipeline"})[0])   # process gone
        self.assertTrue(decide(at(9), pointer=None, trigger="boot")[0])

    def test_never_when_the_nightly_completed_or_still_runs(self):
        self.assertFalse(decide(at(9), trigger="boot", pointer=DONE_TODAY)[0])
        running = {**FAILED, "status": "running_pipeline"}
        start, why = decide(at(3), pointer=running, alive=lambda _p: True)
        self.assertFalse(start)
        self.assertIn("still running", why)

    def test_once_a_day_switch_weekend_and_no_positions(self):
        self.assertFalse(decide(at(9), record={"date": "2026-10-30", "run_id": "run_x"})[0])
        self.assertTrue(decide(at(9), record={"date": "2026-10-29", "run_id": "run_x"})[0])
        self.assertFalse(decide(at(9), config={"paper_session": {"fallback_research": "off"}})[0])
        self.assertFalse(decide(at(9, day=31))[0])                          # Saturday
        self.assertFalse(decide(at(9), held=())[0])

    def test_opening_the_computer_only_between_07_athens_and_14_new_york(self):
        self.assertFalse(decide(at(3), trigger="boot")[0])                  # the nightly run still decides
        self.assertTrue(decide(at(7), trigger="boot")[0])
        self.assertTrue(decide(at(15, 2), trigger="boot")[0])               # woken by the afternoon task
        self.assertFalse(decide(at(20, 0), trigger="boot")[0])              # 14:00 New York
        self.assertTrue(decide(at(3), trigger="nightly")[0])


class WatchlistTests(unittest.TestCase):
    def test_last_good_shortlist_and_held_positions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runs, output = root / "runs", root / "output"
            output.mkdir()
            for name, rows in (("full_research_20261028_003629", [{"ticker": "OLD"}]),
                               ("full_research_20261029_003715", [{"ticker": "aaa"}, {"ticker": "BBB"},
                                                                  {"ticker": "AAA"}]),
                               ("full_research_20261030_003715", [])):          # stopped half way: no shortlist
                (runs / name / "artifacts").mkdir(parents=True)
                (runs / name / "artifacts" / "babis_shortlist_latest.json").write_text(
                    json.dumps({"shortlist": rows}), encoding="utf-8")
            self.assertEqual(fr.last_good_shortlist(runs), ("full_research_20261029_003715", ["AAA", "BBB"]))
            record = fr.prepare(at(2), trigger="nightly", reason="r", pointer=FAILED, held=["BBB", "ZZZ"],
                                runs_dir=runs, output=output)
            listed = json.loads((output / "fallback_watchlist_20261030.json").read_text(encoding="utf-8"))
        self.assertEqual(listed["tickers"], ["AAA", "BBB", "ZZZ"])
        self.assertEqual((record["date"], record["run_id"], record["shortlist_run_id"], record["symbols"]),
                         ("2026-10-30", "run_20261030_020000", "full_research_20261029_003715", 3))
        self.assertEqual(record["held_not_on_shortlist"], ["ZZZ"])
        self.assertEqual(record["status"], "started")

    def test_nightly_state_reads_the_finish_in_athens(self):
        self.assertEqual(fr.nightly_state(DONE_TODAY, "2026-10-30", lambda _p: False), "completed")
        late_utc = {**DONE_TODAY, "finished_at": "2026-10-29T22:30:00+00:00"}   # 00:30 Athens on the 30th
        self.assertEqual(fr.nightly_state(late_utc, "2026-10-30", lambda _p: False), "completed")
        self.assertEqual(fr.nightly_state(None, "2026-10-30", lambda _p: False), "not_completed")

    def test_process_alive_sees_this_process_and_not_a_missing_one(self):
        import os

        self.assertTrue(fr.process_alive(os.getpid()))
        self.assertFalse(fr.process_alive(None))
        self.assertFalse(fr.process_alive(0))


if __name__ == "__main__":
    unittest.main()
