from __future__ import annotations

import sqlite3
import unittest

from research_registry.babis_gap_followup import due_gaps, followup_schema, record_result


class BabisGapFollowupTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        followup_schema(self.db)

    def tearDown(self):
        self.db.close()

    def add_gap(self, gap_id="g1", provider="fmp", eligible="2026-01-01T00:00:00+00:00"):
        self.db.execute("INSERT INTO ecosystem_evidence_gap VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            gap_id, "p1", "ABC", "sector_id", provider, "provider_rate_limited", "shared_cooldown:rate_limited",
            "2026-01-01T00:00:00+00:00", "babis", "[]", "classification", "cooldown", eligible,
            "run1", None, "{}", "2026-01-01T00:00:00+00:00", None,
        ))
        self.db.commit()

    def test_only_due_provider_gaps_are_selected(self):
        self.add_gap()
        self.add_gap("g2", eligible="2099-01-01T00:00:00+00:00")
        rows = due_gaps(self.db, "fmp", 10, "2026-01-02T00:00:00+00:00")
        self.assertEqual([row["gap_id"] for row in rows], ["g1"])

    def test_success_closes_only_matching_gap(self):
        self.add_gap()
        gap = due_gaps(self.db, "fmp", 1, "2026-01-02T00:00:00+00:00")[0]
        record_result(self.db, gap, {"status": "ok", "evidence_sha256": "hash", "data": {"sector": "Technology"}})
        state = self.db.execute("SELECT resolution_state,resolved_at FROM ecosystem_evidence_gap WHERE gap_id='g1'").fetchone()
        self.assertEqual(state[0], "resolved")
        self.assertIsNotNone(state[1])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM ecosystem_evidence_gap_attempt").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
