from __future__ import annotations

import unittest
import sqlite3
from pathlib import Path
import tempfile

from company_profile_agent import build_profile, entity_kind, publish_profile, publish_profile_to_control_plane


class CompanyProfileAgentTests(unittest.TestCase):
    def test_rich_profile_preserves_source_backed_description(self):
        listing = {"ticker": "ABC", "venue_code": "Q", "name": "ABC Inc.",
                   "instrument_type": "equity_candidate", "node_id": "technology.software",
                   "payload": {"venue": "Nasdaq", "raw_profile": {"country": "United States"}}}
        packet = {"business_model": "ABC provides enterprise workflow software.",
                  "source_references": [{"provider": "fmp", "url": "https://example.test"}]}
        profile = build_profile(listing, packet, "snap", "run")
        self.assertEqual(profile["status"], "rich_profile")
        self.assertEqual(profile["overview"], packet["business_model"])
        self.assertEqual(profile["financial_analysis"], "not_part_of_company_profile")

    def test_instrument_profile_does_not_invent_a_company(self):
        listing = {"ticker": "ETF", "venue_code": "P", "name": "Example ETF",
                   "instrument_type": "etf", "node_id": None,
                   "payload": {"venue": "NYSE Arca", "raw_profile": {}}}
        profile = build_profile(listing, None, "snap", "run")
        self.assertEqual(entity_kind("etf"), "fund_or_exchange_traded_product")
        self.assertEqual(profile["status"], "identity_only")
        self.assertIn("verified operating description", profile["overview"])

    def test_profile_is_published_as_a_reusable_control_plane_packet(self):
        listing = {"ticker": "ABC", "venue_code": "Q", "name": "ABC Inc.",
                   "instrument_type": "equity_candidate", "node_id": "technology.software",
                   "payload": {"venue": "Nasdaq", "raw_profile": {"country": "United States"}}}
        profile = build_profile(listing, None, "snapshot_test", "run_profile_test")
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        try:
            first = publish_profile(db, profile)
            repeated = publish_profile(db, profile)
        finally:
            db.close()
        self.assertEqual(first["status"], "completed")
        self.assertEqual(repeated["status"], "reused")
        self.assertEqual(first["control_packet_id"], repeated["control_packet_id"])

    def test_profile_uses_the_shared_control_plane_database(self):
        listing = {"ticker": "ABC", "venue_code": "Q", "name": "ABC Inc.",
                   "instrument_type": "equity_candidate", "node_id": "technology.software",
                   "payload": {"venue": "Nasdaq", "raw_profile": {"country": "United States"}}}
        profile = build_profile(listing, None, "snapshot_test", "run_profile_test")
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "market_intel.db"
            result = publish_profile_to_control_plane(profile, target)
            db = sqlite3.connect(target)
            try:
                stored = db.execute(
                    "SELECT kind,subject_id FROM ecosystem_evidence_packet_store WHERE packet_id=?",
                    (result["control_packet_id"],),
                ).fetchone()
            finally:
                db.close()
        self.assertEqual(stored, ("company_profile", "listing:snapshot_test:Q:ABC"))


if __name__ == "__main__":
    unittest.main()
