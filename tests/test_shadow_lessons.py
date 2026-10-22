"""Lessons move only by an explicit owner decision along allowed transitions; shadow lessons are followed on our
own records by their tag, against the rest, with unknown tags left out."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import tempfile
import unittest

import agent_learning_control_plane as learning
import market_intel_db
import paper_learning as pl


def record(value: str, excess: float, day: int, week: str = "no") -> dict:
    return {"session_date": f"2026-10-{day:02d}", "outcome_pct": excess, "excess_pct": excess,
            "categories": {"results_losses": value, "results_this_week": week}}


class StatusTests(unittest.TestCase):
    def test_only_allowed_transitions_with_a_recorded_reason(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        conn = market_intel_db.connect(Path(folder.name) / "intel.db")
        self.addCleanup(conn.close)
        learning.init_schema(conn)
        lesson_id, _ = learning.upsert_lesson_candidate(
            conn, target_agent="giannis", task_id="earnings_journal", rubric_version="r",
            criterion_id="results:reaction:losses=widening:worse", support_count=10, proposal={"p": 1}, evidence={})
        learning.set_lesson_status(conn, lesson_id, "shadow", reason="owner 2026-10-22: strong, 6/6 years")
        status, evidence = conn.execute("SELECT status, evidence_json FROM agent_lesson_candidates").fetchone()
        self.assertEqual(status, "shadow")
        self.assertIn("owner 2026-10-22", evidence)
        with self.assertRaises(ValueError):
            learning.set_lesson_status(conn, lesson_id, "candidate", reason="back")
        # evidence keeps being added without a promotion
        learning.upsert_lesson_candidate(
            conn, target_agent="giannis", task_id="earnings_journal", rubric_version="r",
            criterion_id="results:reaction:losses=widening:worse", support_count=12, proposal={"p": 1}, evidence={})
        status, evidence = conn.execute("SELECT status, evidence_json FROM agent_lesson_candidates").fetchone()
        self.assertEqual(status, "shadow")
        self.assertIn("owner 2026-10-22", evidence)  # the decision survives new evidence


class FollowTests(unittest.TestCase):
    def test_a_shadow_lesson_is_followed_on_the_records_that_carry_its_tag(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "intel.db"
            with closing(market_intel_db.connect(db)) as conn:
                learning.init_schema(conn)
                lesson_id, _ = learning.upsert_lesson_candidate(
                    conn, target_agent="position_monitor", task_id="earnings_journal", rubric_version="r",
                    criterion_id="results_hold:reaction:losses=widening:worse", support_count=1,
                    proposal={"p": 2}, evidence={})
                learning.set_lesson_status(conn, lesson_id, "shadow", reason="test")
            records = ([record("widening", -1.0, 1 + i % 12) for i in range(40)]
                       + [record("narrowing", 1.0, 1 + i % 12, week="yes") for i in range(40)]
                       + [record("unknown", 5.0, 3)])
            got = pl.shadow_lessons(db, records)[0]
            cell = got["all_sessions"]
            self.assertEqual((got["tag"], cell["with_tag"]["known"], cell["rest"]["known"]), ("results_losses", 40, 40))
            self.assertEqual((cell["beat_spy_gap_points"], cell["agrees_with_lesson"]), (-100.0, True))
            self.assertEqual(got["results_week"]["with_tag"]["known"], 0)  # none of the widening ones reported that week
            self.assertEqual(pl.shadow_lessons(Path(folder) / "none.db", records), [])


if __name__ == "__main__":
    unittest.main()
