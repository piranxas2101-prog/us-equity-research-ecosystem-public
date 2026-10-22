import unittest

import sec_xbrl_pilot


def fact(val, end, accn, start="2025-01-01", form="10-K", filed="2026-02-20"):
    return {"val": val, "start": start, "end": end, "accn": accn, "form": form, "filed": filed,
            "fy": 2025, "fp": "FY"}


def payload(**concepts):
    return {"cik": 1, "entityName": "Bank", "facts": {"us-gaap": {
        name: {"units": {"USD": facts}} for name, facts in concepts.items()}}}


class BankRevenueProxyTests(unittest.TestCase):
    def extract(self, doc, as_of="2026-10-01"):
        return sec_xbrl_pilot.extract_companyfacts(doc, as_of)

    def test_bank_without_revenue_concept_gets_a_labelled_proxy(self):
        doc = payload(InterestIncomeExpenseNet=[fact(700, "2025-12-31", "a")],
                      NoninterestIncome=[fact(300, "2025-12-31", "a")])
        revenue = self.extract(doc)["fields"]["revenue"]
        self.assertEqual(revenue["value"], 1000)
        self.assertTrue(revenue["proxy"])
        self.assertEqual(revenue["proxy_method"], "bank_net_interest_plus_noninterest_income")
        self.assertEqual([c["value"] for c in revenue["components"]], [700, 300])
        self.assertEqual(revenue["period_end"], "2025-12-31")

    def test_reported_revenue_always_wins_over_the_proxy(self):
        doc = payload(Revenues=[fact(5000, "2025-12-31", "a")],
                      InterestIncomeExpenseNet=[fact(700, "2025-12-31", "a")],
                      NoninterestIncome=[fact(300, "2025-12-31", "a")])
        revenue = self.extract(doc)["fields"]["revenue"]
        self.assertEqual(revenue["value"], 5000)
        self.assertNotIn("proxy", revenue)

    def test_components_from_different_filings_stay_unknown(self):
        doc = payload(InterestIncomeExpenseNet=[fact(700, "2025-12-31", "a")],
                      NoninterestIncome=[fact(300, "2025-09-30", "b", start="2025-07-01", form="10-Q")])
        result = self.extract(doc)
        self.assertNotIn("revenue", result["fields"])
        self.assertIn("revenue", {e["field"] for e in result["unknown_events"]})

    def test_a_single_component_is_not_enough(self):
        result = self.extract(payload(InterestIncomeExpenseNet=[fact(700, "2025-12-31", "a")]))
        self.assertNotIn("revenue", result["fields"])

    def test_proxy_respects_the_as_of_cutoff(self):
        doc = payload(InterestIncomeExpenseNet=[fact(700, "2025-12-31", "a", filed="2026-02-20")],
                      NoninterestIncome=[fact(300, "2025-12-31", "a", filed="2026-02-20")])
        self.assertNotIn("revenue", self.extract(doc, as_of="2026-01-15")["fields"])

    def test_newest_complete_period_is_used(self):
        doc = payload(
            InterestIncomeExpenseNet=[fact(700, "2025-12-31", "a"),
                                      fact(200, "2026-03-31", "b", start="2026-01-01", form="10-Q", filed="2026-05-05")],
            NoninterestIncome=[fact(300, "2025-12-31", "a"),
                               fact(80, "2026-03-31", "b", start="2026-01-01", form="10-Q", filed="2026-05-05")])
        revenue = self.extract(doc)["fields"]["revenue"]
        self.assertEqual(revenue["value"], 280)
        self.assertEqual(revenue["accession"], "b")


if __name__ == "__main__":
    unittest.main()
