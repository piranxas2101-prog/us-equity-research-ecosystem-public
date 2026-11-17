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

    def test_the_session_launcher_no_longer_starts_the_debate(self):
        # owner 2026-11-18: not during the session (it must not weigh on it); Jarvis runs it after the close
        self.assertFalse([line for line in self.lines() if "debate_shadow" in line])

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
