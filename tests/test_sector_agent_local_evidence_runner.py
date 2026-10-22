from __future__ import annotations

import unittest

from sector_agent_local_evidence_runner import _best_description, _build_evidence


class FakeRow(dict):
    def __getitem__(self, key):
        return super().__getitem__(key)


class SectorAgentLocalEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.queue = FakeRow(
            ticker="TEST", venue_code="Q", snapshot_id="snap", node_id="technology.software.applications.enterprise"
        )

    def test_rich_stored_description_has_provenance(self):
        enrichment = {"source_refs": [{
            "provider": "polygon", "status": "ok", "observed_at": "2026-01-01T00:00:00Z",
            "hash": "abc", "data": {"description": "TEST supplies enterprise software."},
        }]}
        evidence, mode = _build_evidence(self.queue, {}, None, enrichment, "2026-01-01", ["PEER"], [])
        self.assertEqual(mode, "stored_rich_description")
        self.assertEqual(evidence["business_model"], "TEST supplies enterprise software.")
        self.assertTrue(evidence["claim_sources"]["business_model"])
        self.assertEqual(evidence["peer_group"], ["PEER"])

    def test_exchange_profile_is_explicit_context_not_revenue_claim(self):
        payload = {"raw_profile": {"industry": "Computer Software", "sector": "Technology", "url": "/stocks/test"}}
        evidence, mode = _build_evidence(self.queue, payload, None, None, "2026-01-01", [], [])
        self.assertEqual(mode, "exchange_profile_context")
        self.assertIn("not a verified issuer revenue-mix narrative", evidence["business_model"])
        self.assertEqual(evidence["peer_group"], [])

    def test_unusable_profile_source_is_skipped(self):
        self.assertEqual(_best_description({"source_refs": [{"status": "rate_limited", "data": {"description": "x"}}]}), (None, None))


if __name__ == "__main__":
    unittest.main()
