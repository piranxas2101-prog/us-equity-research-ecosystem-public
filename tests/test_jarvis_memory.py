"""Jarvis's memory of past days (owner 2026-11-17, idea from OpenJarvis's monitor agent): questions about a week, a
month or the last sessions answered from the ecosystem's own history, without the model."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

from jarvis_facts import answer_facts
import jarvis_memory as jm


def day_report(day: str, positions: int, average: float, winners: int, spy: float, strategies: list[tuple]) -> dict:
    return {"session_date": day, "spy_open_to_close_pct": spy,
            "day_result": {"positions": positions, "with_result": positions, "average_result_pct": average,
                           "winners": winners, "losers": positions - winners},
            "per_strategy": [{"agent": a, "playbook": p, "direction": d, "closed": n, "average_pct": avg, "winners": w}
                             for a, p, d, n, avg, w in strategies]}


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        folder = self.root / "output" / "paper_session"
        folder.mkdir(parents=True)
        days = [  # Mon 2026-11-02 .. Fri 11-06, Mon 11-09, Tue 11-10
            ("2026-11-02", 10, -1.0, 4, -0.5, [("yankos", "sbs", "long", 6, -2.0, 2), ("periklis", "mom", "short", 4, 0.5, 2)]),
            ("2026-11-03", 10, 1.0, 6, 0.2, [("yankos", "sbs", "long", 6, 1.0, 4), ("periklis", "mom", "short", 4, 1.0, 2)]),
            ("2026-11-04", 20, 0.5, 11, 0.6, [("periklis", "mom", "short", 20, 0.5, 11)]),
            ("2026-11-05", 5, -3.0, 1, -4.4, [("yankos", "sbs", "long", 5, -3.0, 1)]),
            ("2026-11-06", 5, 2.0, 4, 1.0, [("periklis", "mom", "short", 5, 2.0, 4)]),
            ("2026-11-09", 8, 0.0, 4, -0.1, [("yankos", "sbs", "long", 8, 0.0, 4)]),
            ("2026-11-10", 2, 1.5, 2, 0.3, [("periklis", "mom", "short", 2, 1.5, 2)]),
        ]
        for day, *rest in days:
            (folder / f"session_report_{day.replace('-', '')}.json").write_text(
                json.dumps(day_report(day, *rest)), encoding="utf-8")
        (self.root / "output" / "run_performance_history.csv").write_text(
            "run_id,started_at,status,last_step\n"
            "full_research_20261102_003000,2026-11-02T00:30:00+02:00,completed_review_required,\n"
            "full_research_20261103_003000,2026-11-03T00:30:00+02:00,failed,giannis_triage\n"
            "full_research_20261104_003000,2026-11-04T00:30:00+02:00,completed,\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_days_are_read_from_the_session_reports(self):
        days = jm.sessions(self.root)
        self.assertEqual([d["session_date"] for d in days][:2], ["2026-11-02", "2026-11-03"])
        self.assertEqual(len(days), 7)

    def test_period_parsing(self):
        latest = date(2026, 11, 10)
        self.assertEqual(jm.period("πώς πήγε η εβδομάδα;", latest)[:2], (date(2026, 11, 9), date(2026, 11, 10)))
        self.assertEqual(jm.period("η προηγούμενη εβδομάδα;", latest)[:2], (date(2026, 11, 2), date(2026, 11, 8)))
        self.assertEqual(jm.period("τον μήνα;", latest)[:2], (date(2026, 11, 1), date(2026, 11, 10)))
        self.assertEqual(jm.period("οι τελευταίες 3 συνεδρίες", latest)[2], "στις τελευταίες 3 συνεδρίες")
        self.assertIsNone(jm.period("πώς πήγε η συνεδρία;", latest))

    def test_previous_week_in_sentences(self):
        text = answer_facts("Πώς πήγε η προηγούμενη εβδομάδα;", self.root)["answer"]
        self.assertTrue(text.startswith("Την εβδομάδα 2/11 έως 8/11 είχαμε 5 συνεδρίες με 50 θέσεις"), text)
        self.assertIn("κέρδισαν οι 26 και έχασαν οι 24", text)
        self.assertIn("μέσο αποτέλεσμα +0.1% ανά θέση", text)       # (−10+10+10−15+10)/50
        self.assertIn("σε 3 από τις 5 μέρες πήγαμε καλύτερα από την αγορά", text)
        self.assertIn("Η καλύτερη μέρα ήταν η Παρασκευή 6/11 (+2%)", text)
        self.assertIn("η χειρότερη η Πέμπτη 5/11 (-3%)", text)
        self.assertIn("Η καλύτερη στρατηγική ήταν τα short του Periklis στο mom: 33 θέσεις με μέσο +0.79%", text)
        self.assertIn("η χειρότερη τα long του Yankos στο sbs: 17 θέσεις με μέσο -1.24%", text)
        self.assertNotIn("\n- ", text)

    def test_last_n_sessions(self):
        text = answer_facts("πώς πήγαν οι τελευταίες 2 συνεδρίες;", self.root)["answer"]
        self.assertTrue(text.startswith("Στις τελευταίες 2 συνεδρίες είχαμε 10 θέσεις"), text)

    def test_one_session_is_singular(self):
        text = answer_facts("πώς πήγε η τελευταία 1 συνεδρία;", self.root)["answer"]
        self.assertNotIn("1 συνεδρίες", text)
        self.assertNotIn("1 από τις 1", text)
        self.assertIn("πήγαμε καλύτερα από την αγορά (SPY) εκείνη τη μέρα", text)

    def test_current_week_says_from(self):
        self.assertTrue(answer_facts("πώς πήγε η εβδομάδα;", self.root)["answer"].startswith(
            "Την εβδομάδα από τις 9/11 είχαμε 2 συνεδρίες"))

    def test_nightly_history_of_the_month(self):
        text = answer_facts("πόσες φορές απέτυχε το nightly τον μήνα;", self.root)["answer"]
        self.assertIn("3 νυχτερινά runs", text)
        self.assertIn("1 απέτυχε", text)
        self.assertIn("3/11", text)  # the failed one, by its New York start date: 2/11 evening -> shown as Athens start 3/11
        self.assertIn("giannis_triage", text)

    def test_empty_period_is_said_plainly(self):
        text = answer_facts("πώς πήγε ο προηγούμενος μήνας;", self.root)["answer"]
        self.assertIn("δεν βρήκα συνεδρίες", text)

    def test_sources_are_the_history_files(self):
        out = answer_facts("πώς πήγε η εβδομάδα;", self.root)
        self.assertEqual((out["mode"], out["sources"]), ("facts", ["output/paper_session/session_report_*.json"]))


if __name__ == "__main__":
    unittest.main()


class UnknownCountTests(unittest.TestCase):
    def test_a_day_without_counts_is_said_unknown_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "output" / "paper_session"
            folder.mkdir(parents=True)
            (folder / "session_report_20261109.json").write_text(json.dumps(day_report("2026-11-09", 8, 0.0, 4, -0.1, [])), encoding="utf-8")
            (folder / "session_report_20261110.json").write_text(json.dumps({"session_date": "2026-11-10", "day_result": {}}), encoding="utf-8")
            text = answer_facts("πώς πήγε η εβδομάδα;", Path(tmp))["answer"]
        self.assertIn("σε 1 μέρα το πλήθος των θέσεων είναι άγνωστο", text)
