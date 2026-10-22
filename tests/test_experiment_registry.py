from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import experiment_registry


class ExperimentRegistryTests(unittest.TestCase):
    def payloads(self):
        giannis = {
            "run_id": "run_test", "run_timestamp": "2026-08-25T12:00:00+00:00",
            "results": [{"ticker": "ABC", "share_price": 10, "sector": "Technology", "industry": "Software"}],
        }
        trader = {
            "run_id": "run_test", "agent_name": "yankos",
            "results": [{"ticker": "ABC", "playbook_name": "bounce", "trade_direction": "long", "signal_verdict": "candidate", "signal_score": 8}],
        }
        return giannis, trader

    def test_registry_is_idempotent_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "test.db"
            giannis, trader = self.payloads()
            first = experiment_registry.record_experiments(db, giannis, [trader])
            second = experiment_registry.record_experiments(db, giannis, [trader])
            self.assertEqual(first["inserted"], 1)
            self.assertEqual(second["existing"], 1)
            trader["results"][0]["signal_score"] = 2
            with self.assertRaises(experiment_registry.ExperimentConflictError):
                experiment_registry.record_experiments(db, giannis, [trader])
            trader["results"][0]["trade_direction"] = "short"
            with self.assertRaises(experiment_registry.ExperimentConflictError):
                experiment_registry.record_experiments(db, giannis, [trader])


if __name__ == "__main__":
    unittest.main()
