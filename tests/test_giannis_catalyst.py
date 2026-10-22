"""Giannis catalyst term: only content-bearing SEC events move the fundamental score."""
import unittest

from giannis_rule_based_from_shortlist import catalyst_mode_from, content_catalyst, score_row


def event(**extra):
    item = {"category_id": "dilution_capital_structure", "source_kind": "sec_item", "sec_form": "8-K",
            "sec_item": "3.02", "direction": "negative", "attention_score": 83.0, "event_date": "2026-10-05"}
    item.update(extra)
    return item


FORM_4 = event(category_id="technical_market_structure", source_kind="sec_form", sec_form="4", sec_item=None,
               direction="mixed", attention_score=73.0)


def row(evidence, **extra):
    base = {"ticker": "ABC", "company": "Abc Inc", "share_price": 50.0, "cash": 100.0, "debt": 10.0,
            "fundamental_attention_score": 80.0, "risk_level": "Medium", "instrument_type": "common",
            "catalyst_category_id": "technical_market_structure", "catalyst_attention_score": 73.0,
            "catalyst_direction": "mixed", "catalyst_classification_confidence": "high",
            "sec_filing_age_days": 1, "fresh_primary_data": True, "dilution_flags": [], "data_age_flags": [],
            "catalyst_evidence": evidence}
    base.update(extra)
    return base


class ContentCatalystTests(unittest.TestCase):
    def test_a_form_without_content_is_no_catalyst(self):
        for form_only in (FORM_4, event(source_kind="sec_form", sec_form="424B3", sec_item=None),
                          event(source_kind="sec_form", sec_form="25-NSE", sec_item=None, category_id="listing_compliance")):
            self.assertEqual(content_catalyst({"catalyst_evidence": [form_only]}), (0.0, None))

    def test_news_and_undirected_items_do_not_count(self):
        news = event(source_kind="market_news", direction="positive")
        mixed = event(direction="mixed")
        self.assertEqual(content_catalyst({"catalyst_evidence": [news, mixed]})[0], 0.0)

    def test_the_strongest_directed_8k_item_decides_whatever_was_filed_last(self):
        points, chosen = content_catalyst({"catalyst_evidence": [FORM_4, event()]})
        self.assertEqual((points, chosen["sec_item"]), (-1.66, "3.02"))
        positive = event(category_id="commercial_customer", sec_item="1.01", direction="positive", attention_score=60.0)
        self.assertEqual(content_catalyst({"catalyst_evidence": [positive, event()]})[0], -1.66)

    def test_a_new_form_4_no_longer_moves_the_score(self):
        quiet = score_row(row([]))
        with_form_4 = score_row(row([FORM_4]))
        self.assertEqual(quiet["score_final"], with_form_4["score_final"])
        self.assertEqual(with_form_4["score_catalyst"], 0.0)
        self.assertGreater(with_form_4["score_catalyst_legacy"], 0.0)  # what the old rule would have given
        self.assertIsNone(with_form_4["giannis_catalyst_event"])

    def test_a_material_negative_event_still_costs_points(self):
        scored = score_row(row([FORM_4, event()]))
        self.assertEqual(scored["score_catalyst"], -1.66)
        self.assertEqual(scored["giannis_catalyst_event"]["sec_item"], "3.02")

    def test_legacy_mode_keeps_the_old_term(self):
        legacy = score_row(row([FORM_4]), catalyst_mode="legacy")
        self.assertEqual(legacy["score_catalyst"], legacy["score_catalyst_legacy"])
        self.assertGreater(legacy["score_catalyst"], 0.0)
        self.assertEqual(legacy["catalyst_scoring_mode"], "legacy")

    def test_freshness_of_any_filing_does_not_reach_the_score_when_the_periodic_report_is_known(self):
        kw = {"periodic_filing_age_days": 40, "periodic_filing_form": "10-Q", "fresh_primary_data": False}
        busy = score_row(row([], **kw, sec_filing_age_days=3))
        quiet = score_row(row([], **kw, sec_filing_age_days=45))
        self.assertEqual(busy["score_final"], quiet["score_final"])
        self.assertIn("fresh_primary_data", quiet["unknown_fields"])  # still reported, just not scored
        unknown_periodic = score_row(row([], fresh_primary_data=False, sec_filing_age_days=45))
        self.assertGreater(unknown_periodic["score_penalty"], quiet["score_penalty"])  # old rule without a 10-Q date

    def test_mode_comes_from_the_switch(self):
        self.assertEqual(catalyst_mode_from("legacy"), "legacy")
        self.assertEqual(catalyst_mode_from("config"), "content_events")


if __name__ == "__main__":
    unittest.main()
