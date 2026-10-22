from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sector_agent_batch_runner import prepare, run


class SectorAgentBatchRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db_path = root / "registry.db"
        self.output = root / "runs"
        db = sqlite3.connect(self.db_path)
        db.execute("""CREATE TABLE sector_activation_run(
          activation_id TEXT PRIMARY KEY,babis_pipeline_job TEXT UNIQUE,babis_run_id TEXT,
          snapshot_id TEXT,registry_version TEXT,information_taxonomy_version TEXT,status TEXT,
          queued_count INTEGER,blocked_count INTEGER,created_at TEXT,summary_json TEXT)""")
        db.execute("INSERT INTO sector_activation_run VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            "activation-test", "job", "babis", "snap1", "1.1.0", "1.1.0", "activated", 1, 0,
            "2026-01-01T00:00:00+00:00", "{}",
        ))
        db.execute("""CREATE TABLE sector_analysis_queue(
          queue_id TEXT PRIMARY KEY,activation_id TEXT,agent_id TEXT,snapshot_id TEXT,
          venue_code TEXT,ticker TEXT,instrument_type TEXT,node_id TEXT,subcategory TEXT,
          status TEXT,block_reason TEXT,created_at TEXT,UNIQUE(activation_id,venue_code,ticker))""")
        db.execute("INSERT INTO sector_analysis_queue VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            "queue1", "activation-test", "sector_technology", "snap1", "Q", "TEST",
            "equity_candidate", "technology.software.applications.enterprise", "software",
            "ready_for_sector_evidence", None, "2026-01-01T00:00:00+00:00",
        ))
        db.commit(); db.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_offline_bootstrap_creates_explicit_review_packet(self):
        folder = prepare("activation-test", self.db_path, self.output)
        self.assertEqual(run(folder, self.db_path), 0)
        state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["processed_symbols"], 1)
        self.assertEqual(state["review_required"], 1)
        self.assertEqual(state["network_calls"], 0)
        db = sqlite3.connect(self.db_path)
        packet = json.loads(db.execute("SELECT packet_json FROM sector_context_packet_store").fetchone()[0])
        queue_status = db.execute("SELECT status FROM sector_analysis_queue").fetchone()[0]
        db.close()
        self.assertEqual(packet["agent_id"], "sector_technology")
        self.assertEqual(packet["confidence"], "insufficient")
        self.assertIn("sector_kpis.arr_or_subscription_growth", packet["unknown_fields"])
        self.assertEqual(queue_status, "sector_context_review_required")


if __name__ == "__main__":
    unittest.main()
