"""Characterization tests for the pure helpers in us_equity_monitor.

The scanner module is large and, historically, untested. These tests pin the
observable behavior of its side-effect-free functions so the module can be
split into a package later without silently changing what it computes. They
intentionally avoid anything that touches a network provider, the filesystem
or the wall clock.
"""
from __future__ import annotations

from datetime import datetime, timezone
import unittest

import us_equity_monitor as m


class ToFloatTests(unittest.TestCase):
    def test_none_and_empty_are_none(self):
        self.assertIsNone(m.to_float(None))
        self.assertIsNone(m.to_float(""))

    def test_non_numeric_is_none(self):
        self.assertIsNone(m.to_float("abc"))
        self.assertIsNone(m.to_float(float("nan")))

    def test_numeric_strings_and_numbers(self):
        self.assertEqual(m.to_float("3.5"), 3.5)
        self.assertEqual(m.to_float(7), 7.0)
        self.assertIsInstance(m.to_float(7), float)


class SymbolTests(unittest.TestCase):
    def test_normalize_symbol_trims_and_uppercases(self):
        self.assertEqual(m.normalize_symbol("  aapl "), "AAPL")

    def test_valid_symbol_rules(self):
        self.assertTrue(m.valid_symbol("AAPL"))
        self.assertTrue(m.valid_symbol("BRK.B"))
        self.assertFalse(m.valid_symbol("aapl"))  # must be uppercase
        self.assertFalse(m.valid_symbol("1AB"))   # must start with a letter
        self.assertFalse(m.valid_symbol(""))
        self.assertFalse(m.valid_symbol("TOOOOOOOOOLONG"))  # > 10 chars


class ProviderSymbolCandidatesTests(unittest.TestCase):
    def test_preferred_share_dollar_notation(self):
        self.assertEqual(m.provider_symbol_candidates("A$A", "alpaca"), ["A.PRA", "A$A"])
        self.assertEqual(m.provider_symbol_candidates("A$A", "yfinance"), ["A-PA", "A$A"])

    def test_warrant_and_right_and_unit_suffixes(self):
        self.assertEqual(m.provider_symbol_candidates("ABC.W", "alpaca"), ["ABC.WS", "ABC.W"])
        self.assertEqual(m.provider_symbol_candidates("ABC.W", "yfinance"), ["ABC-WT", "ABC.W"])
        self.assertEqual(m.provider_symbol_candidates("ABC.R", "alpaca"), ["ABC.RT", "ABC.R"])
        self.assertEqual(m.provider_symbol_candidates("ABC.U", "yfinance"), ["ABC-UN", "ABC.U"])

    def test_canonical_ticker_always_last_and_deduplicated(self):
        result = m.provider_symbol_candidates("AAPL", "alpaca")
        self.assertEqual(result, ["AAPL"])
        # The canonical ticker is present exactly once even when an alias equals it.
        self.assertEqual(len(result), len(set(result)))


class WeightedScoreTests(unittest.TestCase):
    def test_equal_weights_average(self):
        self.assertEqual(m.weighted_score(80.0, 60.0, 1.0, 1.0), 70.0)

    def test_zero_denominator_returns_zero(self):
        self.assertEqual(m.weighted_score(50.0, 50.0, 0.0, 0.0), 0.0)

    def test_result_is_rounded_to_two_decimals(self):
        self.assertEqual(m.weighted_score(1.0, 2.0, 1.0, 2.0), round((1 + 4) / 3, 2))


class DeepMergeDictTests(unittest.TestCase):
    def test_nested_override_without_mutating_base(self):
        base = {"a": {"b": 1, "c": 2}, "k": 5}
        merged = m.deep_merge_dict(base, {"a": {"c": 9}})
        self.assertEqual(merged, {"a": {"b": 1, "c": 9}, "k": 5})
        # The original base must be untouched (deep copy semantics).
        self.assertEqual(base, {"a": {"b": 1, "c": 2}, "k": 5})

    def test_scalar_replaces_dict_and_vice_versa(self):
        self.assertEqual(m.deep_merge_dict({"a": {"b": 1}}, {"a": 3}), {"a": 3})


class EvaluateRiskLevelTests(unittest.TestCase):
    def test_no_signals_is_low(self):
        self.assertEqual(m.evaluate_risk_level({}), ("Low", []))

    def test_single_flag_is_medium(self):
        level, flags = m.evaluate_risk_level({"debt_to_equity": 200})
        self.assertEqual(level, "Medium")
        self.assertEqual(flags, ["high_debt_to_equity"])

    def test_three_flags_is_high(self):
        level, flags = m.evaluate_risk_level(
            {"debt_to_equity": 200, "current_ratio": 0.5, "operating_cashflow": -1}
        )
        self.assertEqual(level, "High")
        self.assertIn("weak_liquidity_current_ratio", flags)
        self.assertIn("negative_operating_cashflow", flags)

    def test_sub_dollar_price_flag(self):
        _, flags = m.evaluate_risk_level({"close": 0.5})
        self.assertEqual(flags, ["sub_1usd_price_instability_risk"])


class ClassifyInstrumentTests(unittest.TestCase):
    def test_common_by_default(self):
        result = m.classify_instrument({"symbol": "AAPL"})
        self.assertEqual(result["instrument_type"], "common")
        self.assertFalse(result["complex_security"])

    def test_structures_from_symbol_and_name(self):
        self.assertEqual(m.classify_instrument({"symbol": "ABCW"})["instrument_type"], "warrant")
        self.assertEqual(m.classify_instrument({"symbol": "ABCU"})["instrument_type"], "unit")
        self.assertEqual(m.classify_instrument({"symbol": "ABCR"})["instrument_type"], "right")
        self.assertEqual(
            m.classify_instrument({"symbol": "XYZ", "company_name": "Big Trust"})["instrument_type"],
            "fund_or_trust",
        )
        self.assertTrue(m.classify_instrument({"symbol": "ABCW"})["complex_security"])


class ParseIsoDateTests(unittest.TestCase):
    def test_zulu_timestamp_maps_to_same_instant(self):
        parsed = m.parse_iso_date("2026-01-02T00:00:00Z")
        self.assertIsNotNone(parsed)
        # Compare the absolute instant, not the local wall-clock representation,
        # so the assertion is independent of the Athens UTC offset / DST.
        self.assertEqual(parsed, datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc))

    def test_date_only_is_accepted(self):
        parsed = m.parse_iso_date("2026-03-15")
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed.year, parsed.month, parsed.day), (2026, 3, 15))

    def test_invalid_and_empty_are_none(self):
        self.assertIsNone(m.parse_iso_date("not-a-date"))
        self.assertIsNone(m.parse_iso_date(""))
        self.assertIsNone(m.parse_iso_date(None))


if __name__ == "__main__":
    unittest.main()
