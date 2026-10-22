import unittest

from research_registry.babis_problem_enrichment import analyze


class BabisProblemEnrichmentTests(unittest.TestCase):
    def record(self, **changes):
        row = {"problem_id":"p","group_id":"g","ticker":"ABC","category":"semantic_taxonomy_review",
               "current_node":"healthcare","proposed_node":"healthcare.therapeutics.medicines",
               "sec":{"cik":"1"}}
        row.update(changes)
        return row

    def source(self, provider, data, status="ok"):
        return {"provider":provider,"status":status,"observed_at":"now","evidence_sha256":"hash","data":data}

    def test_fmp_mapping_can_corroborate_sec_candidate(self):
        result = analyze(self.record(), self.source("fmp",{"cik":"1","sector":"Healthcare","industry":"Biotechnology"}), self.source("polygon",{"cik":"1","type":"CS"}))
        self.assertEqual(result["assessment"],"corroborated_taxonomy_candidate")
        self.assertTrue(result["requires_human_approval"])

    def test_identity_conflict_stops_classification(self):
        result = analyze(self.record(), self.source("fmp",{"cik":"2","sector":"Healthcare","industry":"Biotechnology"}), self.source("polygon",{"cik":"1"}))
        self.assertEqual(result["assessment"],"identity_conflict")

    def test_instrument_signal_is_reported_not_applied(self):
        record = self.record(category="instrument_type_review", proposed_node=None)
        result = analyze(record, self.source("fmp",{"cik":"1","isEtf":True}), self.source("polygon",{"cik":"1"}))
        self.assertEqual(result["assessment"],"corroborated_instrument_type")
        self.assertEqual(result["instrument_type_signals"],["etf"])


if __name__ == "__main__":
    unittest.main()
