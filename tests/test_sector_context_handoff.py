from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from company_profile_agent import publish_profile_to_control_plane
from sector_context_handoff import (
    build_from_profile, publish_sector_context, record_unroutable_sector_context,
)


class SectorContextHandoffTests(unittest.TestCase):
    def profile(self) -> dict:
        return {
            "schema": "ecosystem.company_profile.v1",
            "profile_id": "profile-1", "run_id": "run-1", "snapshot_id": "snapshot-1",
            "ticker": "ABC", "venue_code": "Q", "taxonomy_node": "technology.software",
            "status": "contextual_profile", "business_model": "Exchange classifies ABC as software.",
            "created_at": "2026-09-20T10:00:00+00:00", "unknown_fields": ["verified_company_description"],
            "source_references": [{
                "source_id": "exchange-1", "provider": "exchange_bulk_profile",
                "as_of": "2026-09-07", "evidence_label": "fact_provider_standardized",
                "url": "https://www.nasdaq.com/market-activity/stocks/abc",
            }],
        }

    def test_same_run_sector_packet_links_to_profile_without_inventing_kpis(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "flow.db"
            profile = self.profile()
            source = publish_profile_to_control_plane(
                profile, db_path, canonical_subject_id="sec:cik:0000000001"
            )
            packet = build_from_profile(profile, "run-1")
            self.assertEqual(packet["agent_id"], "sector_technology")
            self.assertEqual(packet["status"], "review_required")
            self.assertIn("sector_kpis", packet["unknown_fields"])
            published = publish_sector_context(
                packet=packet, control_plane_db=db_path,
                subject_id="sec:cik:0000000001",
                profile_control_packet_id=source["control_packet_id"],
            )
            db = sqlite3.connect(db_path)
            try:
                row = db.execute(
                    "SELECT kind,subject_id,run_id,quality_state FROM ecosystem_evidence_packet_store WHERE packet_id=?",
                    (published["control_packet_id"],),
                ).fetchone()
                parent = db.execute(
                    "SELECT input_id FROM ecosystem_packet_input WHERE packet_id=? AND input_kind='packet'",
                    (published["control_packet_id"],),
                ).fetchone()
                gap = db.execute(
                    "SELECT reason_code FROM ecosystem_control_plane_gap WHERE run_id='run-1' AND field='sector_context'"
                ).fetchone()
            finally:
                db.close()
            self.assertEqual(row, ("sector_context", "sec:cik:0000000001", "run-1", "conditional"))
            self.assertEqual(parent[0], source["control_packet_id"])
            self.assertEqual(gap[0], "sector_evidence_missing")

    def test_unroutable_profile_remains_explicit(self) -> None:
        profile = self.profile()
        profile["taxonomy_node"] = None
        self.assertIsNone(build_from_profile(profile, "run-1"))
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "flow.db"
            published = publish_profile_to_control_plane(
                profile, db_path, canonical_subject_id="sec:cik:0000000001"
            )
            gap = record_unroutable_sector_context(
                profile=profile, control_plane_db=db_path,
                subject_id="sec:cik:0000000001",
                profile_control_packet_id=published["control_packet_id"],
            )
            db = sqlite3.connect(db_path)
            try:
                row = db.execute(
                    "SELECT field,reason_code,owner,status FROM ecosystem_control_plane_gap "
                    "WHERE gap_id=?", (gap["gap_id"],)
                ).fetchone()
            finally:
                db.close()
            self.assertEqual(row, ("sector_context", "classification_unresolved", "babis", "open"))


if __name__ == "__main__":
    unittest.main()
