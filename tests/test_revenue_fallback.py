import unittest

import sec_xbrl_pilot
from tests.test_bank_revenue_proxy import fact, payload

INCLUDING = "RevenueFromContractWithCustomerIncludingAssessedTax"


class RevenueIncludingAssessedTaxFallbackTests(unittest.TestCase):
    def extract(self, doc, as_of="2026-10-01"):
        return sec_xbrl_pilot.extract_companyfacts(doc, as_of)

    def test_an_issuer_that_reports_only_this_concept_gets_revenue_marked_as_fallback(self):
        revenue = self.extract(payload(**{INCLUDING: [fact(1200, "2025-12-31", "a")]}))["fields"]["revenue"]
        self.assertEqual(revenue["value"], 1200)
        self.assertEqual(revenue["concept"], INCLUDING)
        self.assertEqual((revenue["fallback"], revenue["fallback_method"]), (True, "revenue_including_assessed_tax_fallback"))

    def test_a_total_under_revenues_always_beats_a_segment_level_fact_of_the_same_concept(self):
        # Mercury General reports a 3.2B total and a 15.9M piece under this concept: the total must win.
        doc = payload(Revenues=[fact(3_200_000, "2025-12-31", "a")], **{INCLUDING: [fact(15_900, "2025-12-31", "a")]})
        revenue = self.extract(doc)["fields"]["revenue"]
        self.assertEqual((revenue["value"], revenue["concept"]), (3_200_000, "Revenues"))
        self.assertNotIn("fallback", revenue)

    def test_even_a_newer_fact_of_the_fallback_concept_does_not_displace_an_existing_revenue(self):
        doc = payload(Revenues=[fact(500, "2025-06-30", "a")],
                      **{INCLUDING: [fact(40, "2025-12-31", "b", start="2025-10-01", form="10-Q")]})
        self.assertEqual(self.extract(doc)["fields"]["revenue"]["concept"], "Revenues")

    def test_the_bank_proxy_is_tried_before_the_fallback(self):
        doc = payload(InterestIncomeExpenseNet=[fact(700, "2025-12-31", "a")], NoninterestIncome=[fact(300, "2025-12-31", "a")],
                      **{INCLUDING: [fact(50, "2025-12-31", "a")]})
        self.assertTrue(self.extract(doc)["fields"]["revenue"]["proxy"])

    def test_without_any_revenue_concept_it_stays_unknown_and_lists_what_was_tried(self):
        result = self.extract(payload(NetIncomeLoss=[fact(10, "2025-12-31", "a")]))
        self.assertNotIn("revenue", result["fields"])
        event = next(e for e in result["unknown_events"] if e["field"] == "revenue")
        self.assertIn(INCLUDING, event["concepts_tried"])
        self.assertIn("Revenues", event["concepts_tried"])

    def test_the_fallback_respects_the_as_of_cutoff(self):
        doc = payload(**{INCLUDING: [fact(1200, "2025-12-31", "a", filed="2026-02-20")]})
        self.assertNotIn("revenue", self.extract(doc, as_of="2026-01-15")["fields"])


if __name__ == "__main__":
    unittest.main()
