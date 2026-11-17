"""Jarvis pass: one report a day whatever happens, no duplicates, a failing part never stops the others."""
from __future__ import annotations

import ast
from contextlib import closing
from datetime import date
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import jarvis

ROOT = Path(__file__).resolve().parents[1]
D = date(2026, 11, 16)  # a Monday
FORBIDDEN = {"paper_trade_ledger", "thymios_order_intent", "koulis_risk_gate", "paper_learning"}


def open_props(root: Path) -> list[dict]:
    path = root / "data" / "jarvis" / "proposals.sqlite3"
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as conn:
        return [{"source_key": k, "state": s} for k, s in
                conn.execute("SELECT source_key, state FROM proposals WHERE state='open' ORDER BY source_key")]


class PassTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "jarvis_checks.json").write_text(json.dumps({"schema": "x", "checks": []}), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, rel: str, payload: dict) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8-sig")

    def evaluation(self) -> None:
        self.write("output/paper_learning/evaluation_latest.json", {"shadow_follow": [
            {"lesson_id": "lesson_A", "criterion_id": "c:a", "proposed_change": "Αλλαγή Α", "verdict": "confirmed",
             "sessions_after": 11, "minimum_sessions": 10},
            {"lesson_id": "lesson_B", "criterion_id": "c:b", "proposed_change": "Αλλαγή Β", "verdict": "collecting",
             "sessions_after": 3, "minimum_sessions": 10}]})

    def report(self, day: date) -> Path:
        return self.root / "output" / "jarvis" / f"report_{day.isoformat()}.md"

    def test_first_pass_with_nothing_writes_report(self):
        jarvis.run_pass("after-session", root=self.root, today=D, python="py")
        md = (self.root / "output/jarvis/report_latest.md").read_text(encoding="utf-8")
        self.assertIn("άγνωστο", md)
        self.assertIn("Προτάσεις που περιμένουν απόφαση", md)
        self.assertTrue(self.report(D).exists())
        self.assertTrue((self.root / "output/jarvis/report_latest.json").exists())

    def test_second_pass_same_day_is_idempotent(self):
        self.evaluation()
        jarvis.run_pass("after-session", root=self.root, today=D, python="py")
        jarvis.run_pass("after-session", root=self.root, today=D, python="py")
        self.assertEqual(len(open_props(self.root)), 1)

    def test_paper_learning_verdicts_become_proposals(self):
        self.evaluation()
        jarvis.run_pass("after-session", root=self.root, today=D, python="py")
        self.assertEqual([p["source_key"] for p in open_props(self.root)], ["lesson_A"])
        self.assertIn("Αλλαγή Α", self.report(D).read_text(encoding="utf-8"))

    def test_part_failure_does_not_stop_the_pass(self):
        with mock.patch("jarvis.jarvis_learning.find_rules", side_effect=RuntimeError("boom")):
            rec = jarvis.run_pass("after-session", root=self.root, today=D, python="py")
        self.assertIn("RuntimeError", rec["sections"]["learning"]["error"])
        self.assertTrue(self.report(D).exists())

    def test_broken_registry_is_a_reported_error(self):
        (self.root / "jarvis_checks.json").write_text("{not json", encoding="utf-8")
        rec = jarvis.run_pass("after-session", root=self.root, today=D, python="py")
        self.assertIn("error", rec["sections"]["checks"])

    def test_weekend_nightly_writes_report(self):
        sat = date(2026, 11, 21)
        jarvis.run_pass("after-nightly", root=self.root, today=sat, python="py")
        md = self.report(sat).read_text(encoding="utf-8")
        self.assertIn("Σαββατοκύριακο", md)

    def test_weekday_nightly_keeps_the_session_report(self):
        jarvis.run_pass("after-session", root=self.root, today=D, python="py")
        before = self.report(D).read_text(encoding="utf-8")
        rec = jarvis.run_pass("after-nightly", root=self.root, today=D, python="py")
        self.assertFalse(rec["report_written"])
        self.assertEqual(self.report(D).read_text(encoding="utf-8"), before)

    def test_missed_session_pass_is_written_by_the_nightly(self):
        rec = jarvis.run_pass("after-nightly", root=self.root, today=D, python="py")
        self.assertTrue(rec["report_written"])
        self.assertTrue(self.report(D).exists())

    def test_nightly_day_comes_from_the_clock(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        athens = ZoneInfo("Europe/Athens")
        self.assertEqual(jarvis.nightly_day_from_clock(datetime(2026, 11, 17, 7, 0, tzinfo=athens)), date(2026, 11, 16))
        self.assertEqual(jarvis.nightly_day_from_clock(datetime(2026, 11, 17, 2, 0, tzinfo=athens)), date(2026, 11, 16))

    def test_failed_run_proposes_resume(self):
        self.write("output/full_research_control_plane_background_latest.json",
                   {"run_id": "full_research_x", "status": "failed", "started_at": "2026-11-16T01:30:00+02:00"})
        jarvis.run_pass("after-session", root=self.root, today=D, python="py")
        self.assertIn("resume:full_research_x", [p["source_key"] for p in open_props(self.root)])
        self.assertIn("--resume full_research_x", self.report(D).read_text(encoding="utf-8"))

    def test_check_that_is_no_longer_ready_withdraws_its_proposal(self):
        (self.root / "jarvis_checks.json").write_text(json.dumps({"checks": [
            {"id": "flip_shadow_s8", "title": "flip", "due": {"kind": "every_pass"}, "producer": None,
             "output": "output/paper_learning/flip_shadow_latest.json", "verdict_path": "verdict"}]}), encoding="utf-8")
        self.write("output/paper_learning/flip_shadow_latest.json", {"verdict": "the opposite entry would have helped"})
        jarvis.run_pass("after-session", root=self.root, today=D, python="py")
        self.assertEqual([p["source_key"] for p in open_props(self.root)], ["flip_shadow_s8"])
        self.write("output/paper_learning/flip_shadow_latest.json", {"verdict": "insufficient_sample"})
        jarvis.run_pass("after-session", root=self.root, today=date(2026, 11, 17), python="py")
        self.assertEqual(open_props(self.root), [])

    def test_paper_lesson_back_to_collecting_is_withdrawn(self):
        self.evaluation()
        jarvis.run_pass("after-session", root=self.root, today=D, python="py")
        self.write("output/paper_learning/evaluation_latest.json", {"shadow_follow": [
            {"lesson_id": "lesson_A", "criterion_id": "c:a", "proposed_change": "Αλλαγή Α", "verdict": "collecting"}]})
        jarvis.run_pass("after-session", root=self.root, today=date(2026, 11, 17), python="py")
        self.assertEqual(open_props(self.root), [])

    def test_main_holds_the_computer_awake(self):
        calls = []
        with mock.patch("jarvis.keep_awake", side_effect=lambda on: calls.append(on)), \
             mock.patch("jarvis.run_pass", return_value={"open_proposals": 0, "problems": 0}):
            jarvis.main(["after-session", "--root", str(self.root), "--today", "2026-11-16"])
        self.assertEqual(calls, [True, False])

    def test_saturday_nightly_runs_weekly_checks(self):
        self.assertTrue(jarvis.is_saturday_nightly("after-nightly", date(2026, 11, 21)))
        self.assertFalse(jarvis.is_saturday_nightly("after-session", date(2026, 11, 21)))

    def test_sessions_since_counts_ledger_days(self):
        db = self.root / "data" / "paper_learning.db"
        db.parent.mkdir(parents=True)
        with closing(sqlite3.connect(db)) as conn:
            conn.execute("CREATE TABLE paper_trade_ledger (record_id TEXT, session_date TEXT, kind TEXT,"
                         " complete INTEGER, record_json TEXT, recorded_at TEXT)")
            for i, d in enumerate(["2026-11-10", "2026-11-11", "2026-11-11", "2026-11-12"]):
                conn.execute("INSERT INTO paper_trade_ledger VALUES (?,?,?,?,?,?)", (str(i), d, "trade", 1, "{}", "x"))
            conn.commit()
        self.assertEqual(jarvis.sessions_since(self.root)("2026-11-10"), 2)
        self.assertEqual(jarvis.sessions_since(self.root / "nowhere")("2026-11-10"), 0)

    def test_cli_line_is_ascii(self):
        line = jarvis.summary_line({"open_proposals": 2, "problems": 1})
        self.assertEqual(line, "JARVIS 2 proposals, 1 problems")
        line.encode("ascii")

    def test_no_broker_imports(self):
        for name in sorted(ROOT.glob("jarvis*.py")):
            tree = ast.parse(name.read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    imported.add((node.module or "").split(".")[0])
            bad = {m for m in imported if m.startswith(("alpaca", "start_trader")) or m in FORBIDDEN}
            self.assertFalse(bad, f"{name.name} imports {bad}")


class EcosystemSectionTests(PassTests):
    """Owner 2026-11-16: the report also tells what the ecosystem is learning and how the market reads."""

    def test_report_shows_market_and_lessons_closest_to_a_verdict(self):
        self.write("output/market_state_latest.json", {"session": "2026-11-13", "trend": "up", "mood": "calm",
                                                       "paragraph": "Η αγορά ανεβαίνει ήρεμα."})
        self.write("output/paper_learning/evaluation_latest.json", {"shadow_follow": [
            {"lesson_id": "a", "proposed_change": "Χαλάρωσε τον Miltos", "verdict": "collecting",
             "sessions_after": 7, "minimum_sessions": 10},
            {"lesson_id": "b", "proposed_change": "Λιγότερο rsi2", "verdict": "collecting",
             "sessions_after": 2, "minimum_sessions": 10}]})
        rec = jarvis.run_pass("after-session", root=self.root, today=D, python="py")
        md = self.report(D).read_text(encoding="utf-8")
        self.assertIn("Η εικόνα του οικοσυστήματος", md)
        self.assertIn("Η αγορά ανεβαίνει ήρεμα.", md)
        self.assertIn("2 μαζεύουν δείγμα", md)
        self.assertLess(md.index("Χαλάρωσε τον Miltos"), md.index("Λιγότερο rsi2"))
        self.assertEqual(rec["sections"]["ecosystem"]["lessons"]["by_verdict"], {"collecting": 2})

    def test_missing_ecosystem_files_are_unknown(self):
        jarvis.run_pass("after-session", root=self.root, today=D, python="py")
        md = self.report(D).read_text(encoding="utf-8")
        part = md[md.index("Η εικόνα του οικοσυστήματος"):]
        self.assertIn("άγνωστο", part)


if __name__ == "__main__":
    unittest.main()


class TuningPassTests(PassTests):
    def test_nightly_pass_runs_the_auto_tuning_and_session_pass_does_not(self):
        calls = []
        with mock.patch("jarvis.jarvis_tiers.tune", side_effect=lambda root, base: calls.append(base) or []):
            jarvis.run_pass("after-session", root=self.root, today=D, python="py")
            self.assertEqual(calls, [])
            jarvis.run_pass("after-nightly", root=self.root, today=D, python="py")
        self.assertEqual(len(calls), 1)


class OwnerPassTests(PassTests):
    def test_report_carries_what_waits_for_the_owner(self):
        jarvis.run_pass("after-session", root=self.root, today=D, python="py")
        md = self.report(D).read_text(encoding="utf-8")
        self.assertIn("Τι περιμένει από σένα", md)


class SearchRefreshTests(PassTests):
    def test_session_pass_refreshes_the_index_through_the_given_function(self):
        calls = []
        rec = jarvis.run_pass("after-session", root=self.root, today=D, python="py",
                              refresh=lambda root: calls.append(root) or {"available": False})
        self.assertEqual((calls, rec["sections"]["search_index"]), ([self.root], {"available": False}))
        nightly = jarvis.run_pass("after-nightly", root=self.root, today=D, python="py", refresh=lambda r: calls.append(r))
        self.assertNotIn("search_index", nightly["sections"])
