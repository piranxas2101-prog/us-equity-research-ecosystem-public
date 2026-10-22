from __future__ import annotations

import json
import unittest

from sector_agents.unresolved_classification import analyze
from unresolved_classification_runner import _build_evidence


class UnresolvedClassificationRunnerTests(unittest.TestCase):
    def queue(self, node_id=None):
        return {
            "snapshot_id": "s1", "venue_code": "N", "ticker": "AAP", "node_id": node_id,
            "payload_json": json.dumps({
                "source_directory": "otherlisted.txt",
                "raw_profile": {"country": "United States", "industry": "Auto & Home Supply Stores", "url": "/stocks/aap"},
            }),
        }

    def catalog(self, proposed_node="consumer_discretionary.goods_retail.retail"):
        return {
            "status": "classification_proposal_review", "source_status": "ok",
            "reason": "sic_range_sector_or_group", "relation": "new_assignment",
            "proposed_node": proposed_node,
            "source_ref": {
                "url": "https://data.sec.gov/submissions/", "observed_at": "2026-09-16T00:00:00+00:00",
                "hash": "a" * 64,
                "details": {"sic": "5531", "sicDescription": "Retail-Auto & Home Supply Stores"},
            },
        }

    def test_local_sec_candidate_creates_babis_review_packet(self):
        evidence = _build_evidence(self.queue(), self.catalog(), "2026-09-15T00:00:00+00:00", ["gap1"])
        packet = analyze({
            "run_id": "r1", "snapshot_id": "s1", "symbol": "AAP",
            "venue_code": "N", "classification_id": "q1",
        }, evidence)
        self.assertEqual(packet["status"], "ready_for_babis_review")
        self.assertEqual(packet["sector_candidates"][0]["sector"], "consumer_discretionary")
        self.assertEqual(packet["subcategory_candidates"][0]["subcategory"], "goods_retail")
        self.assertFalse(packet["classification_write_allowed"])
        self.assertIn("gap1", packet["evidence_gap_ids"])

    def test_sector_only_candidate_stays_review_required(self):
        evidence = _build_evidence(self.queue("financials"), self.catalog("financials"), "2026-09-15T00:00:00+00:00", [])
        packet = analyze({
            "run_id": "r1", "snapshot_id": "s1", "symbol": "ACTG",
            "venue_code": "Q", "classification_id": "q2",
        }, evidence)
        self.assertEqual(packet["status"], "review_required")
        self.assertEqual(packet["subcategory_candidates"], [])


if __name__ == "__main__":
    unittest.main()
