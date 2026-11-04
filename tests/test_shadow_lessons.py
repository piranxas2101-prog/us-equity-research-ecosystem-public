"""Lessons move along allowed transitions with a recorded reason (new ones straight to shadow, owner 2026-11-05); shadow lessons are followed on our
own records by their tag, against the rest, with unknown tags left out."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import json
import tempfile
import unittest
from unittest import mock

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
        # owner 2026-11-05: a new lesson is already in shadow, with the reason recorded
        status, evidence = conn.execute("SELECT status, evidence_json FROM agent_lesson_candidates").fetchone()
        self.assertEqual(status, "shadow")
        self.assertIn("owner 2026-11-05", evidence)
        with self.assertRaises(ValueError):
            learning.set_lesson_status(conn, lesson_id, "shadow", reason="again")
        with self.assertRaises(ValueError):
            learning.set_lesson_status(conn, lesson_id, "candidate", reason="back")
        # evidence keeps being added without a promotion
        learning.upsert_lesson_candidate(
            conn, target_agent="giannis", task_id="earnings_journal", rubric_version="r",
            criterion_id="results:reaction:losses=widening:worse", support_count=12, proposal={"p": 1}, evidence={})
        status, evidence = conn.execute("SELECT status, evidence_json FROM agent_lesson_candidates").fetchone()
        self.assertEqual(status, "shadow")
        self.assertIn("owner 2026-11-05", evidence)  # the decision survives new evidence


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
            records = ([record("widening", -1.0, 1 + i % 12) for i in range(40)]
                       + [record("narrowing", 1.0, 1 + i % 12, week="yes") for i in range(40)]
                       + [record("unknown", 5.0, 3)])
            got = pl.shadow_lessons(db, records)[0]
            cell = got["all_sessions"]
            self.assertEqual((got["tag"], cell["with_tag"]["known"], cell["rest"]["known"]), ("results_losses", 40, 40))
            self.assertEqual((cell["beat_spy_gap_points"], cell["agrees_with_lesson"]), (-100.0, True))
            self.assertEqual(got["results_week"]["with_tag"]["known"], 0)  # none of the widening ones reported that week
            self.assertEqual(pl.shadow_lessons(Path(folder) / "none.db", records), [])


class ShadowFollowTests(unittest.TestCase):
    """Owner 2026-11-05: every paper-trading lesson in shadow is judged only on the sessions after it entered."""

    def _db(self, folder: str, entered: str) -> Path:
        db = Path(folder) / "intel.db"
        with closing(market_intel_db.connect(db)) as conn:
            learning.init_schema(conn)
            for criterion in ("control_team:miltos", "control_team:koulis"):
                lesson_id, _ = learning.upsert_lesson_candidate(
                    conn, target_agent=criterion.split(":")[1], task_id="paper_trading", rubric_version="r",
                    criterion_id=criterion, support_count=1, proposal={"proposed_change": criterion}, evidence={})
                evidence = json.loads(conn.execute("SELECT evidence_json FROM agent_lesson_candidates WHERE "
                                                   "lesson_id=?", (lesson_id,)).fetchone()[0])
                evidence["decisions"][-1]["at"] = entered + "T21:10:00+00:00"
                conn.execute("UPDATE agent_lesson_candidates SET evidence_json=? WHERE lesson_id=?",
                             (json.dumps(evidence), lesson_id))
            conn.commit()
        return db

    def test_collecting_until_enough_new_sessions_then_judged_out_of_sample(self):
        records = [{"session_date": f"2026-11-{d:02d}"} for d in range(1, 21)]
        seen = []

        def fake_evaluate(rows, **_):
            seen.append(sorted({r["session_date"] for r in rows}))
            return {}

        with tempfile.TemporaryDirectory() as folder,                 mock.patch.object(pl, "evaluate", fake_evaluate),                 mock.patch.object(pl, "proposals", lambda _: [{"criterion_id": "control_team:miltos"}]):
            got = {f["criterion_id"]: f for f in pl.shadow_follow(self._db(folder, "2026-11-05"), records)}
            self.assertEqual(got["control_team:miltos"]["verdict"], "confirmed")
            self.assertEqual(got["control_team:koulis"]["verdict"], "not_confirmed")
            self.assertEqual(got["control_team:miltos"]["sessions_after"], 15)
            self.assertEqual(seen, [[f"2026-11-{d:02d}" for d in range(6, 21)]])  # the creating session is left out
            got = pl.shadow_follow(self._db(folder, "2026-11-15"), records)
            self.assertEqual({f["verdict"] for f in got}, {"collecting"})
            self.assertEqual(pl.shadow_follow(Path(folder) / "none.db", records), [])


if __name__ == "__main__":
    unittest.main()
