import json
import tempfile
import unittest
from pathlib import Path

import run_technical_validation as runner


class TechnicalValidationRunnerTests(unittest.TestCase):
    def test_selects_candidate_and_watch_but_not_reject(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.json"
            path.write_text(json.dumps({
                "agent_name": "yankos",
                "results": [
                    {"ticker": "AAA", "signal_verdict": "candidate", "signal_score": 8, "trade_direction": "long", "playbook_name": "one"},
                    {"ticker": "AAA", "signal_verdict": "watch", "signal_score": 7, "trade_direction": "long", "playbook_name": "two"},
                    {"ticker": "BBB", "signal_verdict": "reject", "signal_score": 9},
                ],
            }), encoding="utf-8")
            rows = runner.selected_setups([path])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ticker"], "AAA")
        self.assertEqual(rows[0]["playbook_name"], "one")


if __name__ == "__main__":
    unittest.main()
