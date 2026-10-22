from __future__ import annotations

import unittest

import catalyst_taxonomy


def filing(form: str, *, items: str = "", age: int = 0) -> dict[str, object]:
    return {
        "form": form,
        "items": items,
        "filing_date": "2026-08-25",
        "filing_age_days": age,
        "url": f"https://www.sec.gov/example/{form}",
    }


class CatalystTaxonomyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = catalyst_taxonomy.load_taxonomy()

    def classify(self, row: dict[str, object]) -> dict[str, object]:
        return catalyst_taxonomy.classify_catalyst(row, self.taxonomy)

    def test_weights_are_validated_and_trade_lenses_exist(self) -> None:
        self.assertAlmostEqual(sum(self.taxonomy["scoring"]["components"].values()), 1.0)
        self.assertEqual(
            self.taxonomy["trade_lens_scoring"]["status"],
            "advisory_only_no_auto_execution",
        )

    def test_generic_8k_is_not_called_a_contract(self) -> None:
        result = self.classify({"sec_recent_filings": [filing("8-K")]})
        self.assertEqual(result["category_id"], "unknown")
        self.assertEqual(result["direction"], "unknown")
        self.assertEqual(result["long_catalyst_score"], 0.0)
        self.assertEqual(result["short_catalyst_score"], 0.0)

    def test_8k_item_creates_machine_readable_subcategory(self) -> None:
        result = self.classify({"sec_recent_filings": [filing("8-K", items="1.01, 9.01")]})
        self.assertEqual(result["category_id"], "commercial_customer")
        long_category = result["trade_lenses"]["long"]["categories"][0]
        self.assertEqual(long_category["category_id"], "commercial_customer")
        self.assertEqual(
            long_category["subcategories"][0]["subcategory_id"],
            "sec_8k_item_1_01",
        )

    def test_delisting_item_strengthens_short_lens(self) -> None:
        result = self.classify({"sec_recent_filings": [filing("8-K", items="3.01")]})
        self.assertEqual(result["category_id"], "listing_compliance")
        self.assertEqual(result["direction"], "negative")
        self.assertGreater(result["short_catalyst_score"], result["long_catalyst_score"])
        self.assertEqual(result["catalyst_trade_bias"], "catalyst_only_short")

    def test_shelf_registration_strengthens_short_lens(self) -> None:
        result = self.classify({"sec_recent_filings": [filing("S-3ASR")]})
        self.assertEqual(result["category_id"], "dilution_capital_structure")
        self.assertGreater(result["short_catalyst_score"], result["long_catalyst_score"])
        self.assertEqual(result["catalyst_trade_bias"], "catalyst_only_short")

    def test_quarterly_report_is_earnings_not_pre_earnings_event(self) -> None:
        result = self.classify({"sec_recent_filings": [filing("10-Q")]})
        self.assertEqual(result["category_id"], "earnings_guidance")
        self.assertEqual(result["event_name"], "Quarterly financial report filed")

    def test_positive_fda_news_supports_long_more_than_short(self) -> None:
        result = self.classify(
            {
                "news": [
                    {
                        "title": "FDA approval granted after positive clinical trial",
                        "published_at": "2026-08-25T10:00:00Z",
                        "link": "https://example.test/fda-approval",
                    }
                ]
            }
        )
        self.assertEqual(result["category_id"], "clinical_healthcare")
        self.assertEqual(result["direction"], "positive")
        self.assertGreater(result["long_catalyst_score"], result["short_catalyst_score"])

    def test_parent_score_aggregates_category_children(self) -> None:
        result = self.classify(
            {
                "sec_recent_filings": [
                    filing("S-3", age=1),
                    filing("8-K", items="3.01", age=2),
                ]
            }
        )
        short_lens = result["trade_lenses"]["short"]
        self.assertEqual(len(short_lens["categories"]), 2)
        child_scores = [category["score"] for category in short_lens["categories"]]
        self.assertGreaterEqual(short_lens["score"], max(child_scores))
        self.assertLessEqual(short_lens["score"], 100.0)


if __name__ == "__main__":
    unittest.main()
