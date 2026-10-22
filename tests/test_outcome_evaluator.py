"""Outcome evaluator: excess return per label, sample rules, lesson candidates only on inversion."""
from __future__ import annotations

from contextlib import closing
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest

import agent_learning_control_plane as learning
import market_intel_db
import outcome_evaluator as oe

TIERS = ("Core Watchlist", "Selective Watchlist", "Speculative Monitor", "Avoid For Now")


def sessions(count: int) -> list[date]:
    days, day = [], date(2026, 6, 1)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def rows_for(edge: dict[str, float], dates: list[date], per_date: int = 2) -> list[dict]:
    """Rows whose stock return is the benchmark (flat) plus ``edge[label]``; baseline = day before the target."""
    out = []
    for base, target in zip(dates, dates[1:]):
        for label, excess in edge.items():
            for _ in range(per_date):
                out.append({"agent_id": "giannis", "label": label, "horizon_days": 1, "return_pct": excess,
                            "observed_at": f"{base.isoformat()}T23:30:00+00:00",
                            "target_session_date": target.isoformat()})
    return out


class OutcomeEvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.dates = sessions(26)
        self.flat = {day: 100.0 for day in self.dates}

    def test_excess_return_subtracts_the_benchmark_over_the_same_sessions(self):
        closes = {self.dates[0]: 100.0, self.dates[1]: 102.0}
        rows = rows_for({"Core Watchlist": 5.0}, self.dates[:2], per_date=1)
        oe.add_excess(rows, closes)
        self.assertAlmostEqual(rows[0]["excess_return_pct"], 3.0)
        self.assertEqual(rows[0]["baseline_date"], self.dates[0].isoformat())

    def test_without_benchmark_excess_stays_unknown_and_nothing_is_judged(self):
        rows = rows_for(dict(zip(TIERS, (2.0, 1.0, 0.0, -1.0))), self.dates)
        oe.add_excess(rows, None)
        payload = oe.evaluate(rows, benchmark_available=False)
        self.assertEqual(payload["outcomes_with_excess"], 0)
        self.assertEqual(payload["results"], [])

    def test_ordered_labels_need_enough_outcomes_and_dates(self):
        rows = rows_for(dict(zip(TIERS, (2.0, 1.0, 0.0, -1.0))), self.dates)
        oe.add_excess(rows, self.flat)
        self.assertEqual(oe.evaluate(rows, True)["results"][0]["ordering"], "ordered")
        few_dates = rows_for(dict(zip(TIERS, (2.0, 1.0, 0.0, -1.0))), self.dates[:6], per_date=10)
        oe.add_excess(few_dates, self.flat)
        result = oe.evaluate(few_dates, True)["results"][0]
        self.assertEqual(result["ordering"], "insufficient_sample")  # 50 outcomes but 5 dates
        self.assertEqual(result["cells"]["Core Watchlist"]["distinct_dates"], 5)

    def test_inverted_labels_become_a_lesson_candidate_and_nothing_else(self):
        rows = rows_for(dict(zip(TIERS, (-1.0, 0.0, 1.0, 2.0))), self.dates)
        oe.add_excess(rows, self.flat)
        payload = oe.evaluate(rows, True)
        self.assertEqual(payload["results"][0]["ordering"], "inverted")
        self.assertFalse(payload["active_decisions_changed"])
        with tempfile.TemporaryDirectory() as tmp, closing(market_intel_db.connect(Path(tmp) / "m.db")) as conn:
            learning.init_schema(conn)
            lessons = oe.record_lessons(conn, payload)
            self.assertEqual(len(lessons), 1)
            proposal = json.loads(conn.execute("SELECT proposal_json FROM agent_lesson_candidates").fetchone()[0])
            self.assertEqual((proposal["activation"], proposal["changes_active_scores"]), ("candidate_only", False))
            self.assertEqual(conn.execute("SELECT status FROM agent_lesson_candidates").fetchone()[0], "candidate")

    def test_run_reads_episodes_and_outcomes_from_the_learning_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            with closing(market_intel_db.connect(db)) as conn:
                learning.init_schema(conn)
                for i, tier in enumerate(TIERS):
                    episode_id, _ = learning.record_episode(
                        conn, agent_id="giannis", task_id="fundamental_quality", run_id="r1", subject_id=f"T{i}",
                        observed_at=f"{self.dates[0].isoformat()}T23:30:00+00:00", context={},
                        decision={"giannis_tier": tier}, policy_version="test")
                    learning.record_outcome(
                        conn, episode_id=episode_id, objective_id="market_return_pct", horizon_days=1,
                        matured_at="2026-06-03T00:00:00+00:00", label=3.0 - i, label_confidence=1.0,
                        evidence={"target_session_date": self.dates[1].isoformat()})
            payload = oe.run(db, Path(tmp) / "out" / "oe", closes_fn=lambda _s, _e: self.flat)
            cells = payload["results"][0]["cells"]
            self.assertEqual(cells["Core Watchlist"]["median_excess_pct"], 3.0)
            self.assertEqual(payload["results"][0]["ordering"], "insufficient_sample")
            self.assertTrue((Path(tmp) / "out" / "oe_latest.md").exists())


if __name__ == "__main__":
    unittest.main()
