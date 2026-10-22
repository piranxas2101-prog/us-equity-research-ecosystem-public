from __future__ import annotations

import unittest

from research_registry.babis_completion import _unsafe_cross_sector_candidate, infer_instrument_type


class BabisCompletionTests(unittest.TestCase):
    def assessment(self, signals, *, fmp=None, polygon=None):
        return {
            "ticker": "TEST", "instrument_type_signals": signals,
            "source_refs": [
                {"provider": "fmp", "data": fmp or {}},
                {"provider": "polygon", "data": polygon or {}},
            ],
        }

    def test_fund_and_adr_are_distinct(self):
        self.assertEqual(infer_instrument_type(self.assessment(["fund_candidate"])), "fund_candidate")
        self.assertEqual(infer_instrument_type(self.assessment(["adr_candidate"])), "adr_candidate")

    def test_blank_check_overrides_generic_common_stock_signal(self):
        value = self.assessment(
            ["equity_candidate"],
            polygon={"sic_code": "6770", "description": "Blank check company"},
        )
        self.assertEqual(infer_instrument_type(value), "spac")

    def test_plain_common_stock_remains_equity(self):
        self.assertEqual(infer_instrument_type(self.assessment(["equity_candidate"])), "equity_candidate")

    def test_non_sector_security_types_are_supported(self):
        self.assertEqual(infer_instrument_type(self.assessment(["right"])), "right")
        self.assertEqual(infer_instrument_type(self.assessment(["unit"])), "unit")

    def test_cross_sector_taxonomy_candidate_is_deferred(self):
        item = {
            "assessment": "corroborated_taxonomy_candidate",
            "effective_node": "industrials.capital_goods",
            "enrichment": {"sec_proposed_node": "technology.software"},
        }
        self.assertTrue(_unsafe_cross_sector_candidate(item))


if __name__ == "__main__":
    unittest.main()
