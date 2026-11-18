"""Owner 2026-11-18 (ADR-002): the research files the session reads come from one run, or the mix is reported."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import run_handoff_check as rh
from jarvis_report import render


class HandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "output").mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, name: str, payload: dict) -> None:
        (self.root / "output" / name).write_text(json.dumps(payload), encoding="utf-8")

    def test_one_run(self) -> None:
        for name in rh.PRINCIPAL:
            self.write(name, {"run_id": "r1"})
        self.assertEqual(rh.check(self.root)["state"], "one_run")

    def test_a_mix_is_named_file_by_file(self) -> None:
        for name in rh.PRINCIPAL:
            self.write(name, {"run_id": "r1"})
        self.write("yankos_strategy_latest.json", {"run_context": {"run_id": "fallback_1"}})
        got = rh.check(self.root)
        self.assertEqual((got["state"], got["runs"]["fallback_1"]), ("mixed", ["yankos_strategy_latest.json"]))
        md, data = render({"day": "2026-11-24", "sections": {"handoff": got}})
        self.assertEqual(data["problems"], 1)
        self.assertIn("fallback_1: yankos_strategy_latest.json", md)

    def test_missing_or_unlabelled_is_unknown_not_a_match(self) -> None:
        for name in rh.PRINCIPAL[:-1]:
            self.write(name, {"run_id": "r1"})
        self.write(rh.PRINCIPAL[-1], {"no": "id"})
        got = rh.check(self.root)
        self.assertEqual((got["state"], got["unknown"]), ("unknown", [rh.PRINCIPAL[-1]]))
        self.assertEqual(rh.check(Path(self.tmp.name) / "nowhere")["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
