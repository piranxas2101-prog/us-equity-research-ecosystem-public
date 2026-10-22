from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from research_registry.evidence_gaps import build_gaps, import_enrichment, schema


class EvidenceGapTests(unittest.TestCase):
    def test_rate_limit_is_shared_and_nonretrying(self):
        rows = [{
            "problem_id": "p1", "ticker": "ABC", "category": "missing_sic",
            "assessment": "insufficient_supplemental_evidence",
            "source_refs": [{
                "provider": "polygon", "status": "provider_disabled:rate_limited",
                "observed_at": "2026-09-16T00:00:00+00:00", "hash": "hash", "data": {},
            }],
        }]
        gap = build_gaps(rows, "job", Path("missing-rate-state.sqlite3"))[0]
        self.assertEqual(gap["reason_code"], "provider_rate_limited")
        self.assertEqual(gap["resolution_state"], "cooldown")
        self.assertIn("giannis", json.loads(gap["consumer_agents_json"]))
        self.assertIn("thymios", json.loads(gap["consumer_agents_json"]))

    def test_import_is_reconciled_and_idempotent(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        schema(db)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            row = {
                "problem_id": "p1", "ticker": "ABC", "category": "missing_sic",
                "assessment": "supplemental_source_conflict",
                "source_refs": [{"provider": "fmp", "status": "ok", "data": {}}],
            }
            (folder / "state.json").write_text(json.dumps({
                "status": "finished_with_review_required", "processed_symbols": 1, "job_id": "job"
            }), encoding="utf-8")
            (folder / "assessments.json").write_text(json.dumps([row]), encoding="utf-8")
            first = import_enrichment(db, folder, Path(directory) / "missing.sqlite3")
            second = import_enrichment(db, folder, Path(directory) / "missing.sqlite3")
        self.assertEqual(first["inserted"], 1)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM ecosystem_evidence_gap").fetchone()[0], 1)
        db.close()


if __name__ == "__main__":
    unittest.main()
