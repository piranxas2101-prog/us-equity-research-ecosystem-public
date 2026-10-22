import unittest

import giannis_rule_based_from_shortlist as giannis
import monitor_core
import petros_agent
import periklis_strategy_agent
import yankos_strategy_agent
from instrument_type_rules import infer_instrument_type, instrument_profile, normalize_instrument_type

# (ticker, name as listed, registry type) of ordinary companies that the old ticker-letter / word rules
# called warrant, unit, right or fund (from the run of 2026-10-02).
ORDINARY = [
    ("ACIW", "ACI Worldwide, Inc. - Common Stock", "equity_candidate"),
    ("ACMR", "ACM Research, Inc. - Class A Common Stock", "equity_candidate"),
    ("AER", "AerCap Holdings N.V. Ordinary Shares", "equity_candidate"),
    ("AKR", "Acadia Realty Trust Common Stock", "equity_candidate"),
    ("ARW", "Arrow Electronics, Inc. Common Stock", "equity_candidate"),
    ("ATMU", "Atmus Filtration Technologies Inc. Common Stock", "equity_candidate"),
    ("AU", "AngloGold Ashanti PLC Ordinary Shares", "adr_candidate"),
    ("NFLX", "Netflix, Inc. - Common Stock", "equity_candidate"),
    ("PFBC", "Preferred Bank - Common Stock", "equity_candidate"),
    ("ARLP", "Alliance Resource Partners, L.P. - Common Units Representing Limited Partner Interests", "equity_candidate"),
]
DERIVATIVES = [
    ("AACOW", "Abony Acquisition Corp. I - Warrants", "warrant", "warrant"),
    ("AACOU", "Abony Acquisition Corp. I - Units", "unit", "unit"),
    ("AESPR", "Aeon Acquisition I Corp. - Rights", "right", "right"),
    ("ACGLN", "Arch Capital Group Ltd. - Depositary Shares, each Representing a 1/1,000th Interest in a Preferred Share", "preferred", "preferred"),
    ("FUND", "Example Equity Index ETF", "etf", "fund_or_trust"),
]


class RegistryIsTheAuthority(unittest.TestCase):
    def test_ordinary_companies_are_common_whatever_their_last_letter_or_name_says(self):
        for ticker, name, registry in ORDINARY:
            with self.subTest(ticker=ticker):
                self.assertEqual(infer_instrument_type(ticker, name, registry), ("common", "registry"))
                self.assertFalse(instrument_profile(ticker, name, registry)["complex_security"])

    def test_registry_derivatives_keep_their_specific_type(self):
        for ticker, name, registry, expected in DERIVATIVES:
            with self.subTest(ticker=ticker):
                result = infer_instrument_type(ticker, name, registry)
                self.assertEqual(result, (expected, "registry"))
        profile = instrument_profile("AACOW", "Abony Acquisition Corp. I - Warrants", "warrant")
        self.assertEqual((profile["complex_security"], profile["instrument_flags"]), (True, ["warrant_structure"]))

    def test_every_agent_gets_the_same_answer_for_a_registry_typed_row(self):
        row = {"ticker": "ARW", "symbol": "ARW", "company": "Arrow Electronics, Inc. Common Stock",
               "company_name": "Arrow Electronics, Inc. Common Stock", "instrument_type": "equity_candidate"}
        self.assertEqual(monitor_core.classify_instrument(row)["instrument_type"], "common")
        self.assertEqual(giannis.classify_instrument(row)["instrument_type"], "common")
        for module in (petros_agent, periklis_strategy_agent, yankos_strategy_agent):
            self.assertEqual(module.classify_instrument(row), "common", module.__name__)

    def test_a_row_that_already_carries_an_agent_type_keeps_it(self):
        row = {"ticker": "ARW", "company": "Arrow", "instrument_type": "common"}
        self.assertEqual(giannis.classify_instrument(row)["instrument_type"], "common")
        self.assertEqual(petros_agent.classify_instrument({"ticker": "XW", "instrument_type": "warrant"}), "warrant")
        self.assertEqual(normalize_instrument_type("equity_candidate"), "common")
        self.assertEqual(normalize_instrument_type("fund_or_trust"), "fund_or_trust")  # already the agents' word
        self.assertEqual(normalize_instrument_type(None), "")


class HeuristicFallbackWithoutRegistry(unittest.TestCase):
    def test_a_name_that_states_an_ordinary_security_is_never_turned_into_a_derivative(self):
        for ticker, name, _ in ORDINARY:
            with self.subTest(ticker=ticker):
                self.assertEqual(infer_instrument_type(ticker, name)[0], "common")

    def test_real_derivatives_are_still_recognised_from_their_names(self):
        for ticker, name, _, expected in DERIVATIVES[:4]:
            with self.subTest(ticker=ticker):
                self.assertEqual(infer_instrument_type(ticker, name)[0], expected)

    def test_bare_symbols_keep_the_old_suffix_behaviour(self):
        for symbol, expected in (("ABCW", "warrant"), ("ABCU", "unit"), ("ABCR", "right"), ("AAPL", "common")):
            self.assertEqual(infer_instrument_type(symbol, "")[0], expected)
        self.assertEqual(infer_instrument_type("XYZ", "Big Trust")[0], "fund_or_trust")
        self.assertEqual(infer_instrument_type("BAC-PL", "")[0], "preferred")

    def test_the_last_letter_is_ignored_once_the_name_is_known(self):
        for ticker, name in (("EQNR", "Equinor ASA"), ("ZZZW", "Some Company Holdings"), ("ZZZU", "Some Company"), ("ZZZR", "Some Company")):
            self.assertEqual(infer_instrument_type(ticker, name, "unknown_instrument")[0], "common", ticker)
        self.assertEqual(infer_instrument_type("ZZZW", "")[0], "warrant")  # bare symbol: last resort

    def test_words_must_be_whole_words(self):
        for name in ("Bright Horizons Family Solutions", "Copyright Clearance", "United Parcel Service", "Netflix"):
            self.assertEqual(infer_instrument_type("ZZZ", name)[0], "common", name)

    def test_unmapped_registry_types_fall_back_to_the_heuristic(self):
        self.assertEqual(infer_instrument_type("ABCW", "X - Warrants", "spac")[0], "warrant")
        self.assertEqual(infer_instrument_type("AAPL", "Apple Inc. Common Stock", "unknown_instrument")[0], "common")


if __name__ == "__main__":
    unittest.main()
