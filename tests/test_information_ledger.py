from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sqlite3

import information_ledger


class InformationLedgerTests(unittest.TestCase):
    def test_ledger_records_without_evaluating(self) -> None:
        babis = {"run_id": "run_1", "results": [{"ticker": "ABC", "sector": "Technology", "industry": "Software"}]}
        giannis = {"run_id": "run_1", "run_timestamp": "2026-08-25T12:00:00+00:00", "results": [{"ticker": "ABC", "catalyst_category_id": "earnings"}]}
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "db.sqlite"
            summary = information_ledger.record_information(db_path, babis, giannis)
            db = sqlite3.connect(db_path)
            try:
                stored = db.execute("SELECT subject_id,capability,run_id FROM ecosystem_observation_store").fetchone()
            finally:
                db.close()
        self.assertEqual(summary["inserted"], 1)
        self.assertEqual(summary["evaluation_status"], "not_evaluated")
        self.assertNotIn("score", summary)
        self.assertEqual(stored, ("ticker:ABC", "research.information_ledger", "run_1"))


if __name__ == "__main__":
    unittest.main()
