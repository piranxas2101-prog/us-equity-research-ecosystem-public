import unittest

from research_registry.babis_catalog_v2 import propose, relation, sic_candidate
from research_registry.core import taxonomy


class BabisCatalogV2Tests(unittest.TestCase):
    def row(self, **changes):
        row = {"ticker":"ABC","venue":"Nasdaq","name":"Example","instrument_type":"equity_candidate",
               "candidate_cik":"1","node_id":"technology.software.applications.enterprise"}
        row.update(changes)
        return row

    def evidence(self, **changes):
        data = {"ticker":"ABC","cik":"0000000001","type":"CS","sic_code":"7372","description":"Software"}
        data.update(changes)
        return {"provider":"polygon","status":"ok","source_url":"https://api.polygon.io/v3/reference/tickers/ABC",
                "observed_at":"2026-09-16T00:00:00+00:00","evidence_sha256":"x","data":data}

    def test_sic_crosswalk_stays_inside_taxonomy(self):
        for code in [1531,2834,2844,3533,3577,3826,4899,5961,6022,6500,6531,6798,7370,7372,8071,8200,3674,4512,4911,4813,5411,1041,9999]:
            node,_ = sic_candidate(code)
            if node:
                self.assertIn(node,taxonomy()[1])

    def test_more_specific_existing_node_is_preserved(self):
        result=propose(self.row(),self.evidence())
        self.assertEqual(result["status"],"confirmed_existing_more_specific")
        self.assertEqual(result["proposed_node"],"technology.software.applications.enterprise")

    def test_cross_sector_is_quarantined(self):
        result=propose(self.row(node_id="financials"),self.evidence())
        self.assertEqual(result["status"],"classification_conflict_review")
        self.assertEqual(result["relation"],"cross_sector_conflict")

    def test_special_security_gets_no_sector(self):
        result=propose(self.row(),self.evidence(type="ETF"))
        self.assertEqual(result["status"],"asset_type_review")
        self.assertEqual(result["proposed_instrument_type"],"etf")
        self.assertIsNone(result["proposed_node"])

    def test_missing_provider_is_not_identity_conflict(self):
        result=propose(self.row(),{"provider":"polygon","status":"rate_limited","data":{}})
        self.assertEqual(result["reason"],"provider_profile_unavailable")
        self.assertEqual(result["status"],"unresolved")

    def test_actual_identity_conflict_is_visible(self):
        result=propose(self.row(),self.evidence(cik="2"))
        self.assertEqual(result["status"],"identity_review")

    def test_relation_types(self):
        self.assertEqual(relation("technology","technology.software"),"deeper_assignment")
        self.assertEqual(relation("technology.software","technology"),"existing_more_specific")
        self.assertEqual(relation("technology","financials"),"cross_sector_conflict")


if __name__ == "__main__":
    unittest.main()
