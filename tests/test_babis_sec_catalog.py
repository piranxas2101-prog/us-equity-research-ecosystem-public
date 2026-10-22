import unittest
import json
import sqlite3

from research_registry.babis_sec_catalog import canonical_ticker, load_effective_records, propose


class BabisSecCatalogTests(unittest.TestCase):
    def row(self, **changes):
        row = {"ticker": "ABC", "venue": "Nasdaq", "venue_code": "Q", "name": "Example",
               "instrument_type": "equity_candidate", "candidate_cik": "1", "node_id": "technology"}
        row.update(changes); return row

    def evidence(self, **changes):
        data = {"cik": "0000000001", "sic": "7372", "sicDescription": "SERVICES-PREPACKAGED SOFTWARE"}
        data.update(changes)
        return {"provider": "sec", "status": "ok", "observed_at": "2026-09-16T00:00:00+00:00",
                "evidence_sha256": "x", "data": data}

    def test_sec_identity_and_exact_sic_produce_review_proposal(self):
        result = propose(self.row(), self.evidence())
        self.assertEqual(result["status"], "classification_proposal_review")
        self.assertEqual(result["proposed_node"], "technology.software")
        self.assertEqual(result["relation"], "deeper_assignment")

    def test_sec_cik_conflict_is_quarantined(self):
        self.assertEqual(propose(self.row(), self.evidence(cik="2"))["status"], "identity_review")

    def test_unavailable_sec_record_stays_unresolved(self):
        result = propose(self.row(), {"provider": "sec", "status": "timeout_or_network", "data": {}})
        self.assertEqual(result["status"], "unresolved")

    def test_spac_is_caught_before_sector_mapping(self):
        result = propose(self.row(), self.evidence(sic="6770", entityType="operating"))
        self.assertEqual(result["status"], "asset_type_review")
        self.assertEqual(result["proposed_instrument_type"], "spac")
        self.assertIsNone(result["proposed_node"])

    def test_investment_company_is_caught_before_sector_mapping(self):
        result = propose(self.row(name="Example Income Fund"), self.evidence(sic="6726", entityType="investment company"))
        self.assertEqual(result["status"], "asset_type_review")
        self.assertEqual(result["proposed_instrument_type"], "fund_candidate")

    def test_share_class_ticker_normalization(self):
        self.assertEqual(canonical_ticker("BRK.A"), canonical_ticker("BRK-A"))

    def test_new_run_uses_effective_override_as_baseline(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE effective_listed_symbol(snapshot_id TEXT,payload_json TEXT,instrument_type TEXT,node_id TEXT,status TEXT)")
        payload = {"ticker": "ABC", "instrument_type": "equity_candidate", "node_id": "technology"}
        db.execute("INSERT INTO effective_listed_symbol VALUES(?,?,?,?,?)",
                   ("s", json.dumps(payload), "spac", None, "approved_provisional"))
        row = load_effective_records(db, "s")[0]
        self.assertEqual(row["instrument_type"], "spac")
        self.assertIsNone(row["node_id"])
        self.assertEqual(row["status"], "approved_provisional")


if __name__ == "__main__": unittest.main()
