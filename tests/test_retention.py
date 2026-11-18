"""Owner 2026-11-18 (ADR-002): retention reports first; it deletes only one rule at a time with --yes, never the
audit record or the databases, never anything newer than its limit."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
import unittest

import retention as rt

NOW = time.time()


def touch(path: Path, days_old: float, size: int = 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    stamp = NOW - days_old * 86400
    os.utime(path, (stamp, stamp))
    return path


class RetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for i in range(5):
            touch(self.root / "output" / f"baseline_compare_2026110{i}_000000" / "a.json", 30 - i)
        touch(self.root / "output" / "ecosystem_test_reports" / "old_run" / "r.md", 90)
        touch(self.root / "output" / "ecosystem_test_reports" / "new_run" / "r.md", 5)
        os.utime(self.root / "output" / "ecosystem_test_reports" / "old_run", (NOW - 90 * 86400,) * 2)
        touch(self.root / "logs" / "old.log", 90)
        touch(self.root / "logs" / "new.log", 1)
        touch(self.root / "output" / "runs" / "full_research_1" / "manifest.json", 400)
        touch(self.root / "data" / "market_intel.db", 400)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_report_lists_and_deletes_nothing(self) -> None:
        got = rt.report(self.root, now=NOW)
        self.assertEqual((got["rules"]["baseline_compare"]["items"], got["rules"]["test_reports"]["items"],
                          got["rules"]["logs"]["items"]), (2, 1, 1))
        self.assertEqual(len(list((self.root / "output").glob("baseline_compare_*"))), 5)

    def test_baselines_keep_the_newest_three(self) -> None:
        names = [p.name for p in rt.candidates(self.root, "baseline_compare", now=NOW)]
        self.assertEqual(names, ["baseline_compare_20261100_000000", "baseline_compare_20261101_000000"])

    def test_apply_removes_one_rule_only_and_never_the_audit_record(self) -> None:
        result = rt.apply(self.root, "logs", now=NOW)
        self.assertEqual(result["removed"], 1)
        self.assertFalse((self.root / "logs" / "old.log").exists())
        self.assertTrue((self.root / "logs" / "new.log").exists())
        self.assertEqual(len(list((self.root / "output").glob("baseline_compare_*"))), 5)  # other rules untouched
        for rule in rt.RULES:
            rt.apply(self.root, rule, now=NOW)
        self.assertTrue((self.root / "output" / "runs" / "full_research_1" / "manifest.json").exists())
        self.assertTrue((self.root / "data" / "market_intel.db").exists())
        self.assertTrue((self.root / "output" / "ecosystem_test_reports" / "new_run").exists())

    def test_apply_needs_the_owners_yes(self) -> None:
        with self.assertRaises(SystemExit):
            rt.main(["apply", "--rule", "logs", "--root", str(self.root)])
        self.assertTrue((self.root / "logs" / "old.log").exists())


if __name__ == "__main__":
    unittest.main()
