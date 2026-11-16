"""Jarvis health: what the nightly run and the afternoon session left, read from their files; unknown stays unknown."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

from jarvis_health import diagnose, nightly_health, session_health

POINTER = "output/full_research_control_plane_background_latest.json"
PERF_HEADER = "run_id,recorded_at,started_at,finished_at,status,technical_errors,agent_audit,last_step,wall_hours\n"


def by_name(steps):
    return {s.name: s for s in steps}


def states(steps):
    return {s.name: s.state for s in steps}


class HealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for d in ("output/paper_session", "logs"):
            (self.root / d).mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, rel: str, text: str, bom: bool = False) -> None:
        (self.root / rel).write_text(text, encoding="utf-8-sig" if bom else "utf-8")

    def pointer(self, **fields) -> None:
        base = {"run_id": "full_research_20261116_012229", "status": "completed_review_required",
                "technical_errors": 0, "agent_audit_status": "pass"}
        self.write(POINTER, json.dumps({**base, **fields}), bom=True)

    def test_completed_run_is_ok(self):
        self.pointer()
        self.assertEqual(states(nightly_health(self.root)), {"run": "ok", "technical_errors": "ok", "agent_audit": "ok"})

    def test_failed_run_has_last_step_and_diagnosis(self):
        self.pointer(status="failed")
        self.write("output/run_performance_history.csv",
                   PERF_HEADER + "full_research_20261116_012229,x,x,x,failed,0,pass,giannis_triage,2.0\n")
        self.write("logs/nightly_research_20261116_013000.log", "start\nHTTP Error 429: Too Many Requests\n")
        s = by_name(nightly_health(self.root))["run"]
        self.assertEqual((s.state, s.diagnosis), ("failed", "rate_limit"))
        self.assertIn("giannis_triage", s.detail)

    def test_failed_run_reports_fallback(self):
        self.pointer(status="interrupted")
        self.write("output/fallback_research_latest.json", json.dumps({"status": "completed"}))
        self.assertEqual(by_name(nightly_health(self.root))["fallback"].state, "ok")

    def test_technical_errors_are_a_problem(self):
        self.pointer(technical_errors=3)
        self.assertEqual(by_name(nightly_health(self.root))["technical_errors"].state, "failed")

    def test_running_run(self):
        self.pointer(status="running")
        self.assertEqual(by_name(nightly_health(self.root))["run"].state, "running")

    def test_missing_pointer_is_unknown_not_ok(self):
        self.assertEqual(by_name(nightly_health(self.root))["run"].state, "unknown")

    def test_missing_audit_field_is_unknown(self):
        self.write(POINTER, json.dumps({"run_id": "r", "status": "completed"}))
        self.assertEqual(by_name(nightly_health(self.root))["agent_audit"].state, "unknown")

    def test_session_report_of_today_is_ok(self):
        self.write("output/paper_session/session_report_latest.json",
                   json.dumps({"session_date": "2026-11-16", "day_result": {"positions": 18, "average_result_pct": -0.42}}))
        s = by_name(session_health(self.root, "2026-11-16"))["session_report"]
        self.assertEqual(s.state, "ok")
        self.assertIn("18", s.detail)

    def test_session_report_of_another_day_is_missing(self):
        self.write("output/paper_session/session_report_latest.json", json.dumps({"session_date": "2026-11-13"}))
        self.assertEqual(by_name(session_health(self.root, "2026-11-16"))["session_report"].state, "missing")

    def test_no_orders_day_is_ok_with_reason(self):
        self.write("logs/afternoon_session_20261116_150000.log",
                   "2026-11-16T22:01:00 No paper orders today: after the 15:00 New York entry cutoff\n")
        s = by_name(session_health(self.root, "2026-11-16"))["session"]
        self.assertEqual(s.state, "ok")
        self.assertIn("after the 15:00", s.detail)

    def test_session_error_is_diagnosed(self):
        self.write("logs/afternoon_session_20261116_150000.log",
                   "x\nAfternoon session stopped by an error: boom\n")
        self.assertEqual(by_name(session_health(self.root, "2026-11-16"))["session"].state, "failed")

    def test_no_session_log_is_unknown(self):
        self.assertEqual(by_name(session_health(self.root, "2026-11-16"))["session"].state, "unknown")

    def test_diagnose_traceback_takes_last_line(self):
        self.assertEqual(diagnose("x\nTraceback (most recent call last):\n  f\nValueError: bad\n"),
                         ("traceback", "ValueError: bad"))

    def test_diagnose_start_failure_wins(self):
        self.assertEqual(diagnose("0xC0000142\nrate limit\n")[0], "start_failure")

    def test_diagnose_nothing(self):
        self.assertEqual(diagnose("all fine\n"), (None, None))


class FreshnessTests(HealthTests):
    """Review finding 2: last night's completed run is not tonight's."""

    def test_stale_run_is_not_ok(self):
        self.pointer(started_at="2026-11-14T01:22:29+02:00")  # New York 2026-11-13
        self.write("logs/nightly_research_20261117_013000.log", "Full run did not start: fallback research\n")
        s = by_name(nightly_health(self.root, expected_day=date(2026, 11, 16)))["run"]
        self.assertEqual((s.state, s.diagnosis), ("failed", "not_started"))
        self.assertIn("2026-11-16", s.detail)

    def test_fresh_run_is_ok(self):
        self.pointer(started_at="2026-11-17T01:22:29+02:00")  # New York 2026-11-16
        self.assertEqual(by_name(nightly_health(self.root, expected_day=date(2026, 11, 16)))["run"].state, "ok")

    def test_unknown_start_is_unknown_when_freshness_is_asked(self):
        self.pointer()
        self.assertEqual(by_name(nightly_health(self.root, expected_day=date(2026, 11, 16)))["run"].state, "unknown")


if __name__ == "__main__":
    unittest.main()
