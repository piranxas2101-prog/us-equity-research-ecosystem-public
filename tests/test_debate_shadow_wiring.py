"""Debate shadow wiring (owner 2026-11-16): one background start after Koulis in the session launcher, never waited
for; the nightly measure runs as an allow-listed Jarvis producer."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

import jarvis_checks

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = "start_trader_paper_trading_today.ps1"


class WiringTests(unittest.TestCase):
    def lines(self):
        return (ROOT / LAUNCHER).read_text(encoding="utf-8-sig").splitlines()

    def test_one_background_start_after_koulis(self):
        lines = self.lines()
        starts = [n for n, line in enumerate(lines) if "debate_shadow.py" in line]
        self.assertEqual(len(starts), 1)
        line = lines[starts[0]]
        self.assertIn("Start-Process", line)
        self.assertNotIn("-Wait", line)
        self.assertIn("-WindowStyle Hidden", line)
        self.assertIn("try {", line)
        koulis_exit = next(n for n, l in enumerate(lines) if "exit $koulisExit" in l)
        miltos = next(n for n, l in enumerate(lines) if "start_miltos_entry_stalker_today.ps1" in l)
        self.assertLess(koulis_exit, starts[0])
        self.assertLess(starts[0], miltos)

    def test_launcher_line_is_ascii(self):
        for line in self.lines():
            if "debate" in line.lower():
                line.encode("ascii")

    def test_jarvis_runs_the_measure_and_raises_only_a_pass(self):
        self.assertEqual(jarvis_checks.PRODUCERS["debate_shadow_evaluate"], ["debate_shadow.py", "evaluate"])
        checks = {c["id"]: c for c in jarvis_checks.load_registry(ROOT / "jarvis_checks.json")}
        check = checks["debate_shadow_s12"]
        self.assertEqual((check["producer"], check["ready_values"], check["due"]["kind"]),
                         ("debate_shadow_evaluate", ["veto_would_help"], "every_pass"))

    def test_knowledge_map_knows_the_debate(self):
        topics = json.loads((ROOT / "jarvis_knowledge.json").read_text(encoding="utf-8"))["topics"]
        debate = next(t for t in topics if t["id"] == "debate")
        self.assertIn("output/paper_learning/debate_shadow_latest.md", debate["files"])


if __name__ == "__main__":
    unittest.main()
