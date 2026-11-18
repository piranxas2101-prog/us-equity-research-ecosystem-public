"""Owner 2026-11-18 (ADR-002): the release fingerprint of what reaches orders - stable, unknown when it cannot be
known, a change inside the measurement period visible, and the ledger records stamped with it."""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

import paper_trade_ledger as ledger
import release_guard as rg
from jarvis_report import render

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = {"period": {"id": "P1", "start": "2026-11-23", "end": "2026-12-18"},
            "files": ["koulis_risk_gate.py", "yankos_playbooks.json"],
            "config_keys": {"scanner_config.json": ["paper_session"]}}


class FingerprintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "release_manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
        (self.root / "koulis_risk_gate.py").write_bytes(b"LIMIT = 5\n")
        (self.root / "yankos_playbooks.json").write_text("{}", encoding="utf-8")
        self.config({"paper_session": {"new_orders_limit": 5}, "pipeline": {"stage1_limit": 1}})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def config(self, payload: dict) -> None:
        (self.root / "scanner_config.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_stable_and_blind_to_line_endings(self) -> None:
        first = rg.fingerprint(self.root)
        (self.root / "koulis_risk_gate.py").write_bytes(b"LIMIT = 5\r\n")
        self.assertEqual(rg.fingerprint(self.root)["release_id"], first["release_id"])

    def test_a_rule_change_changes_it_research_does_not(self) -> None:
        first = rg.fingerprint(self.root)
        self.config({"paper_session": {"new_orders_limit": 5}, "pipeline": {"stage1_limit": 99}})  # research
        self.assertEqual(rg.fingerprint(self.root)["release_id"], first["release_id"])
        self.config({"paper_session": {"new_orders_limit": 6}, "pipeline": {"stage1_limit": 99}})
        second = rg.fingerprint(self.root)
        self.assertNotEqual(second["release_id"], first["release_id"])
        self.assertEqual(rg.changed_files(first, second), ["scanner_config.json:paper_session"])

    def test_a_missing_file_is_recorded_as_missing(self) -> None:
        (self.root / "yankos_playbooks.json").unlink()
        self.assertEqual(rg.fingerprint(self.root)["files"]["yankos_playbooks.json"], "missing")

    def test_the_shipped_manifest_names_real_files(self) -> None:
        current = rg.fingerprint(ROOT)
        self.assertEqual([f for f, h in current["files"].items() if h == "missing"], [])
        self.assertIn("koulis_risk_gate.py", current["files"])
        self.assertNotIn("giannis_rule_based_from_shortlist.py", current["files"])  # research changes freely

    def test_snapshots_and_period_changes(self) -> None:
        at = lambda d, h: datetime(2026, 11, d, h, tzinfo=timezone.utc)  # noqa: E731
        rg.snapshot(self.root, "after-nightly", now=at(20, 10))
        rg.snapshot(self.root, "after-nightly", now=at(24, 10))
        (self.root / "koulis_risk_gate.py").write_bytes(b"LIMIT = 4\n")
        rg.snapshot(self.root, "after-session", now=at(25, 23))
        log = rg.read_log(self.root)
        report = rg.period_changes(log, MANIFEST)
        self.assertEqual((report["period"], len(report["changes"])), ("P1", 1))
        self.assertEqual(report["changes"][0]["files"], ["koulis_risk_gate.py"])
        self.assertEqual(report["start_release"], log[0]["release_id"])


class SessionReleaseTests(unittest.TestCase):
    def entry(self, iso: str, rid: str) -> dict:
        return {"at": iso, "release_id": rid}

    def test_the_release_before_the_session_when_nothing_changed(self) -> None:
        log = [self.entry("2026-11-24T11:00:00+00:00", "A"), self.entry("2026-11-24T22:00:00+00:00", "A")]
        self.assertEqual(rg.for_session(log, date(2026, 11, 24)), {"release_id": "A", "basis": "snapshot"})

    def test_a_change_on_the_day_leaves_it_unknown(self) -> None:
        log = [self.entry("2026-11-24T11:00:00+00:00", "A"), self.entry("2026-11-24T22:00:00+00:00", "B")]
        got = rg.for_session(log, date(2026, 11, 24))
        self.assertEqual((got["release_id"], got["basis"], got["before"], got["after"]), (None, "changed_on_day", "A", "B"))

    def test_no_snapshot_before_is_unknown_not_guessed(self) -> None:
        log = [self.entry("2026-11-24T22:00:00+00:00", "B")]
        self.assertEqual(rg.for_session(log, date(2026, 11, 24)), {"release_id": None, "basis": "unknown"})

    def test_the_ledger_stamps_every_record_once(self) -> None:
        records = [{"record_id": "a"}, {"record_id": "b", "release_id": "OLD", "release_basis": "snapshot"}]
        ledger.stamp_release(records, {"release_id": "A", "basis": "snapshot"})
        self.assertEqual([(r["release_id"], r["release_basis"]) for r in records], [("A", "snapshot"), ("OLD", "snapshot")])

    def test_the_ledger_checks_the_files_as_they_are_after_the_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "release_manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
            (root / "koulis_risk_gate.py").write_bytes(b"LIMIT = 5\n")
            rg.snapshot(root, "after-nightly", now=datetime(2026, 11, 24, 11, tzinfo=timezone.utc))
            evening = datetime(2026, 11, 24, 22, tzinfo=timezone.utc)
            self.assertEqual(ledger.session_release(root, date(2026, 11, 24), now=evening)["basis"], "snapshot")
            (root / "koulis_risk_gate.py").write_bytes(b"LIMIT = 4\n")  # changed during the day
            self.assertEqual(ledger.session_release(root, date(2026, 11, 24), now=evening)["basis"], "changed_on_day")


class ReportTests(unittest.TestCase):
    def test_the_report_shows_the_release_and_a_change_is_a_problem(self) -> None:
        quiet = {"release_id": "A", "period": "P1", "start": "2026-11-23", "end": "2026-12-18", "changes": []}
        md, data = render({"day": "2026-11-24", "sections": {"release": quiet}})
        self.assertIn("Έκδοση (ό,τι φτάνει στις εντολές): A; στην περίοδο P1", md)
        self.assertIn("καμία αλλαγή", md)
        self.assertEqual(data["problems"], 0)
        changed = {**quiet, "changes": [{"at": "2026-12-02T22:00:00+00:00", "from": "A", "to": "B",
                                         "files": ["koulis_risk_gate.py"]}]}
        md, data = render({"day": "2026-12-02", "sections": {"release": changed}})
        self.assertEqual(data["problems"], 1)
        self.assertIn("άλλαξε μέσα στην περίοδο P1 στις 2026-12-02: koulis_risk_gate.py", md)


if __name__ == "__main__":
    unittest.main()
