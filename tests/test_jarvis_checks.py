"""Jarvis due checks: when a check is due, what its output says, and only allow-listed producers ever run."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from jarvis_checks import check_proposal, is_due, load_registry, run_check

ROOT = Path(__file__).resolve().parents[1]
SAT = date(2026, 11, 21)
D = date(2026, 11, 18)
WEEKLY = {"id": "w", "due": {"kind": "weekly"}}
T3 = {"id": "T3_volume_basis", "title": "T3", "rules_review": "T3", "due": {"kind": "sessions_since",
      "date": "2026-10-24", "sessions": 20}, "producer": None, "output": None}
FLIP = {"id": "flip_shadow_s8", "title": "flip", "rules_review": "section 8", "due": {"kind": "every_pass"},
        "producer": None, "output": "output/paper_learning/flip_shadow_latest.json", "verdict_path": "verdict"}
EXIT = {"id": "exit_lab_horizon", "title": "exit", "rules_review": None, "due": {"kind": "weekly"},
        "producer": "exit_lab_horizon", "timeout_seconds": 420,
        "output": "output/strategy_lab/horizon_curve_latest.json", "report_only": True}


class ChecksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.far = time.time() + 3600

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, rel: str, payload: dict) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8-sig")

    def registry(self, checks: list[dict]) -> Path:
        path = self.root / "jarvis_checks.json"
        path.write_text(json.dumps({"schema": "ecosystem.jarvis_checks.v1", "checks": checks}), encoding="utf-8")
        return path

    def test_registry_rejects_unlisted_producer(self):
        with self.assertRaises(ValueError):
            load_registry(self.registry([{**EXIT, "producer": "rm_everything"}]))

    def test_registry_rejects_duplicate_ids(self):
        with self.assertRaises(ValueError):
            load_registry(self.registry([FLIP, FLIP]))

    def test_weekly_only_on_saturday_nightly(self):
        self.assertFalse(is_due(WEEKLY, today=SAT, is_saturday_nightly=False, sessions_since=lambda d: 0))
        self.assertTrue(is_due(WEEKLY, today=SAT, is_saturday_nightly=True, sessions_since=lambda d: 0))

    def test_sessions_since_counts(self):
        self.assertFalse(is_due(T3, today=D, is_saturday_nightly=False, sessions_since=lambda d: 19))
        self.assertTrue(is_due(T3, today=D, is_saturday_nightly=False, sessions_since=lambda d: 20))

    def test_after_date(self):
        check = {"id": "f", "due": {"kind": "after_date", "date": "2026-11-13"}}
        self.assertFalse(is_due(check, today=date(2026, 11, 12), is_saturday_nightly=False, sessions_since=lambda d: 0))
        self.assertTrue(is_due(check, today=date(2026, 11, 13), is_saturday_nightly=False, sessions_since=lambda d: 0))

    def test_needs_lab_when_no_producer_and_no_output(self):
        self.assertEqual(run_check(T3, root=self.root, python="py", deadline=self.far)["state"], "needs_lab")

    def test_waiting_and_ready_verdicts(self):
        self.write(FLIP["output"], {"verdict": "insufficient_sample"})
        self.assertEqual(run_check(FLIP, root=self.root, python="py", deadline=self.far)["state"], "waiting")
        self.write(FLIP["output"], {"verdict": "passes"})
        out = run_check(FLIP, root=self.root, python="py", deadline=self.far)
        self.assertEqual((out["state"], out["verdict"]), ("ready", "passes"))

    def test_missing_output_is_unknown(self):
        self.assertEqual(run_check(FLIP, root=self.root, python="py", deadline=self.far)["state"], "unknown")

    def test_producer_runs_with_list_args_and_reads_output(self):
        calls = []

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            self.write(EXIT["output"], {"curve": []})
            return subprocess.CompletedProcess(args, 0, "", "")

        out = run_check(EXIT, root=self.root, python="py", deadline=self.far, runner=runner)
        self.assertEqual((out["state"], out["verdict"]), ("ready", "refreshed"))
        self.assertEqual(calls[0][0], ["py", "exit_lab.py", "--horizon-curve"])
        self.assertNotIn("shell", calls[0][1])

    def test_producer_failure(self):
        def runner(args, **kwargs):
            return subprocess.CompletedProcess(args, 2, "", "boom")
        self.assertEqual(run_check(EXIT, root=self.root, python="py", deadline=self.far, runner=runner)["state"], "failed")

    def test_producer_timeout_is_recorded(self):
        def runner(*a, **k):
            raise subprocess.TimeoutExpired("x", 1)
        self.assertEqual(run_check(EXIT, root=self.root, python="py", deadline=self.far, runner=runner)["state"], "timeout")

    def test_budget_skips_producer(self):
        def runner(*a, **k):
            raise AssertionError("must not run")
        out = run_check(EXIT, root=self.root, python="py", deadline=time.time() + 10, runner=runner)
        self.assertEqual(out["state"], "skipped_budget")

    def test_ready_check_becomes_proposal_but_report_only_does_not(self):
        p = check_proposal({"id": "flip_shadow_s8", "state": "ready", "verdict": "passes", "detail": "d",
                            "evidence": ["x"]}, FLIP)
        self.assertEqual((p.source, p.source_key, p.rules_review, p.evidence["verdict"]),
                         ("check", "flip_shadow_s8", "section 8", "passes"))
        self.assertIsNone(check_proposal({"id": "exit", "state": "ready", "verdict": "refreshed", "detail": "",
                                          "evidence": []}, EXIT))
        self.assertIsNone(check_proposal({"id": "flip", "state": "waiting", "verdict": "x", "detail": "",
                                          "evidence": []}, FLIP))

    def test_shipped_registry_loads(self):
        ids = {c["id"] for c in load_registry(ROOT / "jarvis_checks.json")}
        self.assertTrue({"flip_shadow_s8", "news_shadow", "exit_lab_horizon", "T3_volume_basis", "F4_held_positions",
                         "shock_longs_1105"} <= ids)


class ReadyValuesTests(ChecksTests):
    """Review: only the verdicts that ask for a decision reach the owner."""

    def test_ready_values_allow_list(self):
        check = {**FLIP, "ready_values": ["the opposite entry would have helped"]}
        self.write(FLIP["output"], {"verdict": "no clear gain"})
        self.assertEqual(run_check(check, root=self.root, python="py", deadline=self.far)["state"], "waiting")
        self.write(FLIP["output"], {"verdict": "the opposite entry would have helped"})
        self.assertEqual(run_check(check, root=self.root, python="py", deadline=self.far)["state"], "ready")

    def test_shipped_flip_and_news_have_ready_values(self):
        checks = {c["id"]: c for c in load_registry(ROOT / "jarvis_checks.json")}
        self.assertEqual(checks["flip_shadow_s8"]["ready_values"], ["the opposite entry would have helped"])
        self.assertEqual(checks["news_shadow"]["ready_values"], ["news-flagged did better"])


if __name__ == "__main__":
    unittest.main()
