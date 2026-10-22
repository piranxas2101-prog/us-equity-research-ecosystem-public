"""10-K business description: verbatim Item 1 lead, stored once per 10-K, within a per-run budget."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

import filing_business_description as fbd

FILLER = " ".join(f"Filler sentence number {n} about operations and markets." for n in range(60))
HTML = ("<html><body><p>PART I</p><p>Item 1. Business</p>"
        "<p>BUSINESS OVERVIEW Acme Corp designs and sells industrial widgets to manufacturers worldwide. "
        "We operate three plants in Ohio and sell through distributors. "
        "This Annual Report on Form 10-K contains forward-looking statements. "
        "Our customers include automotive and aerospace companies.</p>"
        f"<p>{FILLER}</p>"
        "<p>Item 1A. Risk Factors</p>"
        f"<p>Our business faces many risks. {FILLER}</p>"
        "<p>Item 1B. Unresolved Staff Comments</p><p>None.</p></body></html>")
SUBMISSIONS = {"filings": {"recent": {"form": ["8-K", "10-K", "10-Q"],
                                      "accessionNumber": ["0001-26-000003", "0001-26-000002", "0001-25-000001"],
                                      "filingDate": ["2026-09-01", "2026-02-20", "2025-11-01"],
                                      "primaryDocument": ["a.htm", "acme-10k.htm", "q.htm"]}}}


class FakeSec:
    def __init__(self, submissions=SUBMISSIONS, html=HTML):
        self.calls: list[str] = []
        self.submissions, self.html = submissions, html

    def __call__(self, url: str, _agent: str) -> bytes:
        self.calls.append(url)
        if "submissions" in url:
            return json.dumps(self.submissions).encode()
        return self.html.encode()


class BusinessDescriptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Path(self.tmp.name) / "desc.sqlite3"

    def make(self, fetch, budget=5):
        return fbd.BusinessDescriptions(self.store, user_agent="test agent", max_new_per_run=budget, fetch=fetch,
                                        today=date(2026, 10, 6))

    def test_lead_is_verbatim_without_the_heading_and_skips_boilerplate(self):
        lead = fbd.lead_sentences(HTML)
        self.assertEqual(lead[0], "Acme Corp designs and sells industrial widgets to manufacturers worldwide.")
        self.assertNotIn("Form 10-K", " ".join(lead))
        self.assertEqual(len(lead), 3)

    def test_the_latest_10k_is_fetched_once_and_then_read_from_the_store(self):
        fake = FakeSec()
        first = self.make(fake).get(1234)
        self.assertEqual((first["accession"], first["filing_date"]), ("0001-26-000002", "2026-02-20"))
        self.assertTrue(fake.calls[1].endswith("/1234/000126000002/acme-10k.htm"))
        again = FakeSec()
        self.assertEqual(self.make(again).get(1234)["sentences"], first["sentences"])
        self.assertEqual(again.calls, [])  # no newer 10-K known: no network

    def test_budget_limits_new_fetches_and_the_rest_stays_unknown(self):
        fake = FakeSec()
        descriptions = self.make(fake, budget=1)
        self.assertIsNotNone(descriptions.get(1))
        self.assertIsNone(descriptions.get(2))
        self.assertEqual(descriptions.counts["deferred"], 1)

    def test_a_40f_filer_is_remembered_and_not_asked_every_night(self):
        no_10k = FakeSec(submissions={"filings": {"recent": {"form": ["40-F"], "accessionNumber": ["x"],
                                                             "filingDate": ["2026-04-01"], "primaryDocument": ["f.htm"]}}})
        self.assertIsNone(self.make(no_10k).get(7))
        later = FakeSec()
        self.assertIsNone(self.make(later).get(7))
        self.assertEqual(later.calls, [])

    def test_evidence_is_labelled_as_the_companys_own_claim(self):
        description = self.make(FakeSec()).get(1234)
        evidence = fbd.as_evidence(description)
        self.assertTrue(evidence["business_model"].startswith("Acme Corp designs"))
        source = evidence["source_references"][0]
        self.assertEqual((source["provider"], source["evidence_label"], source["as_of"]),
                         ("sec_10k", "issuer_management_claim", "2026-02-20"))
        self.assertEqual(evidence["claim_sources"]["business_model"], [source["source_id"]])
        self.assertEqual(fbd.as_evidence(None), {})

    def test_without_a_user_agent_nothing_is_fetched(self):
        fake = FakeSec()
        descriptions = fbd.BusinessDescriptions(self.store, user_agent="", fetch=fake)
        self.assertIsNone(descriptions.get(1))
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()


class SectorTextTests(unittest.TestCase):
    def test_framework_fields_come_from_the_10k_text_verbatim(self):
        import filing_sector_text as fst
        filler = " ".join(f"Filler sentence {n} about operations and markets in general." for n in range(70))
        html = ("<html><body><p>Item 1. Business</p><p>Acme Corp makes widgets for factories.</p>"
                "<p>We sell our products through distributors and directly to manufacturers.</p>"
                "<p>Competition</p><p>The widget market is highly competitive and Acme competes with Beta Corp on price.</p>"
                f"<p>{filler}</p><p>Item 1A. Risk Factors</p>"
                "<p>Changes in economic conditions could reduce demand for our products.</p>"
                f"<p>{filler} Demand is cyclical.</p>"
                "<p>The loss of our largest customer could harm our results.</p>"
                f"<p>{filler} Customers matter.</p><p>Item 1B. Unresolved Staff Comments</p><p>None.</p>"
                "<p>Item 7. Management's Discussion and Analysis</p>"
                "<p>Net sales increased 12% to $500 million, primarily due to higher widget volumes.</p>"
                "<p>Cost of sales increased 8%, driven by higher steel prices.</p>"
                "<p>Backlog was $120 million at year end.</p>"
                "<p>Fiscal Year Ended December 31, 2025 Change compared to prior year (in millions, except per share data)</p>"
                f"<p>{filler}</p><p>Item 7A. Quantitative Disclosures</p><p>None.</p><p>Item 8. Financial Statements</p></body></html>")
        result = fst.extract(html)
        self.assertEqual(result["sections_found"], ["business", "mdna", "risk_factors"])
        self.assertIn("competes with Beta Corp", result["competitive_position"][0]["text"])
        self.assertTrue(result["value_chain_position"][0]["text"].startswith("We sell our products through distributors"))
        self.assertIn("primarily due to higher widget volumes", result["revenue_drivers"][0]["text"])
        self.assertIn("driven by higher steel prices", result["cost_drivers"][0]["text"])
        self.assertEqual(result["kpis"]["backlog"][0]["text"], "Backlog was $120 million at year end.")
        self.assertEqual([r["text"] for r in result["industry_risks"]],
                         ["Changes in economic conditions could reduce demand for our products."])
        self.assertEqual([r["text"] for r in result["company_specific_risks"]],
                         ["The loss of our largest customer could harm our results."])

    def test_table_rows_and_page_furniture_are_not_statements(self):
        import filing_sector_text as fst
        self.assertIsNone(fst.clean("Total cost of revenue $ 650 $ 72 12 % $ 578 $ 1,200 $ 300 $ 9"))
        self.assertIsNone(fst.clean("Fiscal Year Ended January 31, 2026 (in millions, except per share data) Net revenue rose"))
        self.assertEqual(fst.clean("44 Table of Contents Backlog was $120 million at year end."),
                         "Backlog was $120 million at year end.")

    def test_kpis_are_limited_to_the_companys_framework(self):
        description = {"cik": 1, "accession": "a", "filing_date": "2026-02-20", "url": "u", "form": "10-K",
                       "document_sha256": "h", "sentences": ["Acme makes widgets for factories worldwide."],
                       "evidence": {"kpis": {"backlog": [{"text": "Backlog was $1 million.", "section": "Item 7", "basis": "b"}],
                                             "churn": [{"text": "Churn was 2%.", "section": "Item 7", "basis": "b"}]}}}
        evidence = fbd.as_evidence(description, ["backlog", "orders"])
        self.assertEqual(list(evidence["sector_kpis"]), ["backlog"])
        xbrl = {"sector_kpis": {"backlog": {"value": 5}}, "claim_sources": {"sector_kpis": ["x"]}, "source_references": []}
        merged = fbd.merge_evidence(xbrl, evidence)
        self.assertEqual(merged["sector_kpis"]["backlog"], {"value": 5})  # a reported XBRL value wins over text
        self.assertEqual(merged["claim_sources"]["sector_kpis"], ["x", "sec_10k:0000000001:a"])
