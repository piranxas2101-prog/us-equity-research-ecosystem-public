from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sector_ecosystem_integration import activate, migrate_unresolved, reconcile


class SectorEcosystemIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.job = root / "babis-job"; self.job.mkdir()
        (self.job / "state.json").write_text(json.dumps({
            "status":"finished_with_review_required", "sec_job_id":"babis-test"
        }), encoding="utf-8")
        self.db_path = root / "registry.db"
        db = sqlite3.connect(self.db_path)
        db.execute("CREATE TABLE classification_problem_run(run_id TEXT PRIMARY KEY,snapshot_id TEXT)")
        db.execute("INSERT INTO classification_problem_run VALUES('babis-test','snap1')")
        db.execute("""CREATE TABLE effective_listed_symbol(
          snapshot_id TEXT,venue_code TEXT,ticker TEXT,instrument_type TEXT,node_id TEXT,status TEXT)""")
        db.executemany("INSERT INTO effective_listed_symbol VALUES(?,?,?,?,?,?)", [
            ("snap1","Q","TECH","equity_candidate","technology.software.applications.enterprise","provisional"),
            ("snap1","N","BANK","equity_candidate","financials.banking_credit.banks.regional","provisional"),
            ("snap1","A","TOP","equity_candidate","healthcare","provisional"),
            ("snap1","Q","ETF","etf",None,"provisional"),
        ])
        db.commit(); db.close()
        self.output = root / "out"

    def tearDown(self): self.tmp.cleanup()

    def test_activation_registers_agents_categories_and_routes(self):
        result = activate(self.job, self.db_path, self.output)
        self.assertEqual(result["registered_agents"], 12)
        self.assertEqual(result["queued"], 2)
        self.assertEqual(result["unresolved_queued"], 1)
        self.assertEqual(result["blocked"], 0)
        db = sqlite3.connect(self.db_path)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM ecosystem_agent_registration WHERE agent_type='sector_research'").fetchone()[0], 11)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM ecosystem_agent_registration WHERE agent_type='classification_resolution'").fetchone()[0], 1)
        self.assertEqual(db.execute(
            "SELECT status FROM ecosystem_agent_registration WHERE agent_id='babis'"
        ).fetchone()[0], "production_ready")
        self.assertEqual(db.execute("SELECT COUNT(*) FROM research_information_category").fetchone()[0], 24)
        self.assertEqual(db.execute("SELECT agent_id FROM sector_analysis_queue WHERE ticker='TECH'").fetchone()[0], "sector_technology")
        unresolved = db.execute("SELECT agent_id,status,block_reason FROM sector_analysis_queue WHERE ticker='TOP'").fetchone()
        self.assertEqual(unresolved, ("sector_unresolved_classification", "ready_for_unresolved_evidence", "classification_missing_subcategory"))
        db.close()

    def test_activation_is_idempotent(self):
        first = activate(self.job, self.db_path, self.output)
        second = activate(self.job, self.db_path, self.output)
        self.assertEqual(first["activation_id"], second["activation_id"])
        self.assertEqual(second["status"], "already_activated")

    def test_incomplete_babis_is_rejected(self):
        (self.job / "state.json").write_text(json.dumps({"status":"running","sec_job_id":"babis-test"}), encoding="utf-8")
        with self.assertRaises(ValueError): activate(self.job, self.db_path, self.output)

    def test_existing_blocked_queue_migrates_idempotently(self):
        result = activate(self.job, self.db_path, self.output)
        db = sqlite3.connect(self.db_path)
        db.execute("UPDATE sector_analysis_queue SET agent_id=NULL,subcategory=NULL,status='blocked' WHERE ticker='TOP'")
        db.commit(); db.close()
        first = migrate_unresolved(result["activation_id"], self.db_path, self.output)
        second = migrate_unresolved(result["activation_id"], self.db_path, self.output)
        self.assertEqual(first["unresolved_migration"]["migrated_this_call"], 1)
        self.assertEqual(second["unresolved_migration"]["migrated_this_call"], 0)
        self.assertEqual(second["unresolved_queued"], 1)

    def test_reconcile_routes_new_classification_and_removes_non_corporate(self):
        result = activate(self.job, self.db_path, self.output)
        db = sqlite3.connect(self.db_path)
        db.execute("UPDATE effective_listed_symbol SET node_id='healthcare.care.providers' WHERE ticker='TOP'")
        db.execute("UPDATE effective_listed_symbol SET instrument_type='fund_candidate',node_id=NULL WHERE ticker='TECH'")
        db.commit(); db.close()
        summary = reconcile(result["activation_id"], self.db_path, self.output)
        db = sqlite3.connect(self.db_path)
        self.assertEqual(db.execute("SELECT agent_id,status FROM sector_analysis_queue WHERE ticker='TOP'").fetchone(),
                         ("sector_healthcare", "ready_for_sector_evidence"))
        self.assertEqual(db.execute("SELECT agent_id,status FROM sector_analysis_queue WHERE ticker='TECH'").fetchone(),
                         (None, "removed_non_corporate_instrument"))
        db.close()
        self.assertEqual(summary["removed_non_corporate"], 1)


if __name__ == "__main__": unittest.main()
