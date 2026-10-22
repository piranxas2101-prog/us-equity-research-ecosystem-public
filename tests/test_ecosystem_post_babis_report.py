from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import ecosystem_post_babis_report as report


class EcosystemPostBabisReportTests(unittest.TestCase):
    def test_incomplete_run_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp) / "job"; job.mkdir()
            (job / "state.json").write_text(json.dumps({"status":"running"}), encoding="utf-8")
            with self.assertRaises(ValueError): report.build_report(job, Path(temp)/"x.db", Path(temp)/"out")

    def test_report_contains_upgrade_and_scope_sections(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); job = root / "job"; job.mkdir()
            (job / "state.json").write_text(json.dumps({
                "status":"finished_with_review_required", "sec_job_id":"sec1",
                "enrichment_job_id":None, "enrichment_job":""
            }), encoding="utf-8")
            activation = root / "activation"; activation.mkdir()
            summary = {"activation_id":"a1","babis_pipeline_job":str(job.resolve()),"status":"activated",
                       "registered_agents":11,"information_categories":22,"queued":2,"blocked":0,"by_agent":{"sector_technology":2}}
            (activation / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
            sector_root = root / "sector"; sector_root.mkdir()
            (sector_root / "latest.json").write_text(json.dumps({"activation":str(activation)}), encoding="utf-8")
            db_path = root / "db.sqlite"; db=sqlite3.connect(db_path)
            db.execute("CREATE TABLE ecosystem_agent_registration(agent_type TEXT)")
            db.execute("INSERT INTO ecosystem_agent_registration VALUES('sector_research')")
            db.execute("CREATE TABLE sector_analysis_queue(activation_id TEXT,status TEXT,agent_id TEXT)")
            db.execute("INSERT INTO sector_analysis_queue VALUES('a1','ready_for_sector_evidence','sector_technology')")
            db.execute("CREATE TABLE research_information_category(owner TEXT)")
            db.execute("INSERT INTO research_information_category VALUES('sector_agent')")
            db.commit(); db.close()
            with patch.object(report, "SECTOR_ROOT", sector_root):
                result = report.build_report(job, db_path, root / "out")
            text = Path(result["report"]).read_text(encoding="utf-8")
            self.assertIn("Θέματα για αυριανή αναβάθμιση", text)
            self.assertIn("δεν σημαίνουν ότι οι sector agents ολοκλήρωσαν", text)


if __name__ == "__main__": unittest.main()
