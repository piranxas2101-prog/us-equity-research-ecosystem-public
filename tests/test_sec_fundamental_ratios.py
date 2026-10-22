import unittest
from datetime import date

import sec_fundamental_ratios as sfr


def _filed(form):
    return "2026-02-20" if form == "10-K" else "2026-08-01"


def flow_fact(val, start, end, form="10-Q", filed=None):
    return {"val": val, "start": start, "end": end, "form": form, "filed": filed or _filed(form), "accn": "a"}


def point(val, end, form="10-Q", filed=None):
    return {"val": val, "end": end, "form": form, "filed": filed or _filed(form), "accn": "a"}


def doc(**concepts):
    return {"facts": {"us-gaap": {name: {"units": {"USD": facts}} for name, facts in concepts.items()}}}


FY = ("2025-01-01", "2025-12-31")
H1 = ("2026-01-01", "2026-06-30")
H1_PRIOR = ("2025-01-01", "2025-06-30")
FY_PRIOR = ("2024-01-01", "2024-12-31")


def company():
    return doc(
        Revenues=[flow_fact(1000, *FY, form="10-K"), flow_fact(900, *FY_PRIOR, form="10-K"),
                  flow_fact(600, *H1), flow_fact(500, *H1_PRIOR)],
        NetIncomeLoss=[flow_fact(120, *FY, form="10-K"), flow_fact(100, *FY_PRIOR, form="10-K"),
                       flow_fact(80, *H1), flow_fact(50, *H1_PRIOR)],
        GrossProfit=[flow_fact(500, *FY, form="10-K"), flow_fact(330, *H1), flow_fact(240, *H1_PRIOR)],
        StockholdersEquity=[point(400, "2025-12-31", "10-K"), point(500, "2026-06-30")],
        LongTermDebt=[point(250, "2026-06-30")],
        AssetsCurrent=[point(300, "2026-06-30")], LiabilitiesCurrent=[point(200, "2026-06-30")],
    )


class RatioTests(unittest.TestCase):
    def test_ratios_use_trailing_twelve_months_and_the_latest_balance_sheet(self):
        v = sfr.compute_ratios(company(), date(2026, 10, 1))["values"]
        # TTM revenue 1000 + 600 - 500 = 1100; TTM net income 120 + 80 - 50 = 150; TTM gross 500 + 330 - 240 = 590
        self.assertAlmostEqual(v["profit_margin"], 150 / 1100)
        self.assertAlmostEqual(v["gross_margin"], 590 / 1100)
        self.assertAlmostEqual(v["roe"], 150 / 500)
        self.assertAlmostEqual(v["revenue_growth"], 0.2)         # 600 vs 500
        self.assertAlmostEqual(v["earnings_growth"], 0.6)        # 80 vs 50
        self.assertAlmostEqual(v["current_ratio"], 1.5)
        self.assertAlmostEqual(v["debt_to_equity"], 50.0)        # in yfinance's percent unit

    def test_without_year_to_date_the_fiscal_year_is_used(self):
        raw = doc(Revenues=[flow_fact(1000, *FY, form="10-K"), flow_fact(800, *FY_PRIOR, form="10-K")],
                  NetIncomeLoss=[flow_fact(100, *FY, form="10-K")], StockholdersEquity=[point(400, "2025-12-31", "10-K")])
        v = sfr.compute_ratios(raw, date(2026, 3, 1))["values"]
        self.assertAlmostEqual(v["profit_margin"], 0.1)
        self.assertAlmostEqual(v["revenue_growth"], 0.25)
        self.assertIsNone(v["earnings_growth"])  # no prior-year net income: unknown, not zero

    def test_missing_inputs_stay_unknown_and_negative_equity_gives_no_roe(self):
        raw = company()
        raw["facts"]["us-gaap"]["StockholdersEquity"]["units"]["USD"] = [point(-5, "2026-06-30")]
        v = sfr.compute_ratios(raw, date(2026, 10, 1))["values"]
        self.assertIsNone(v["roe"])
        self.assertIsNone(v["debt_to_equity"])
        self.assertTrue(all(value is None for value in sfr.compute_ratios({"facts": {}}, date(2026, 10, 1))["values"].values()))

    def test_year_to_date_without_the_prior_year_comparative_is_unknown(self):
        raw = company()
        raw["facts"]["us-gaap"]["Revenues"]["units"]["USD"] = [
            flow_fact(1000, *FY, form="10-K"), flow_fact(600, *H1)]
        v = sfr.compute_ratios(raw, date(2026, 10, 1))["values"]
        self.assertIsNone(v["revenue_growth"])
        self.assertIsNone(v["profit_margin"])

    def test_filings_after_the_as_of_date_are_ignored(self):
        v = sfr.compute_ratios(company(), date(2026, 3, 1))["values"]  # the 10-Qs were filed in August
        self.assertAlmostEqual(v["profit_margin"], 0.12)


class ComparisonTests(unittest.TestCase):
    def test_agreement_is_counted_per_rule_and_missing_data_is_kept_apart(self):
        rule = lambda metric, status, op=">=", thr=0.1: {"metric": metric, "status": status, "operator": op, "threshold": thr}  # noqa: E731
        rows = [{"ticker": "AAA", "fundamental_rule_details": [
                    rule("profit_margin", "passed"), rule("roe", "failed"), rule("current_ratio", "missing_data", thr=1.0)]},
                {"ticker": "BBB", "fundamental_rule_details": [rule("profit_margin", "failed")]},
                {"ticker": "NOSEC", "fundamental_rule_details": [rule("profit_margin", "passed")]}]
        ratios = {"AAA": {"values": {"profit_margin": 0.15, "roe": 0.30, "current_ratio": 1.5}},
                  "BBB": {"values": {"profit_margin": 0.05}}}
        out = sfr.compare_rows(rows, ratios)
        self.assertEqual(out["compared_rows"], 2)
        self.assertEqual(out["by_metric"]["profit_margin"]["agree"], 2)
        self.assertEqual(out["by_metric"]["roe"]["disagree"], 1)
        self.assertEqual(out["by_metric"]["current_ratio"]["yfinance_missing"], 1)
        self.assertEqual(out["rows_with_a_disagreeing_rule"], 1)
        self.assertIn("roe", sfr.render_markdown(out))


RULES = [
    {"metric": "roe", "operator": ">=", "threshold": 0.12, "weight": 2.0, "observed": 0.05, "status": "failed"},
    {"metric": "profit_margin", "operator": ">=", "threshold": 0.1, "weight": 1.5, "observed": None, "status": "missing_data"},
    {"metric": "current_ratio", "operator": ">=", "threshold": 1.0, "weight": 1.0, "observed": 2.0, "status": "passed"},
    {"metric": "forward_pe", "operator": "<=", "threshold": 35, "weight": 1.0, "observed": 20.0, "status": "passed"},
    {"metric": "gross_margin", "operator": ">=", "threshold": 0.3, "weight": 1.0, "observed": None, "status": "missing_data"},
]


class SecFirstTests(unittest.TestCase):
    def test_sec_value_wins_yfinance_is_a_labelled_fallback_and_missing_stays_missing(self):
        details, score, sources = sfr.sec_first_rules(RULES, {"roe": 0.3, "profit_margin": 0.2, "current_ratio": None})
        by = {d["metric"]: d for d in details}
        self.assertEqual((by["roe"]["status"], by["roe"]["value_source"]), ("passed", "sec_edgar"))          # yfinance said failed
        self.assertEqual(by["profit_margin"]["value_source"], "sec_edgar")                                    # yfinance had nothing
        self.assertEqual((by["current_ratio"]["status"], by["current_ratio"]["value_source"]), ("passed", "yfinance_secondary"))
        self.assertEqual(by["forward_pe"]["value_source"], "yfinance_secondary")                              # analyst data
        self.assertEqual((by["gross_margin"]["status"], by["gross_margin"]["value_source"]), ("missing_data", "missing"))
        self.assertEqual(sources, {"sec_edgar": 2, "yfinance_secondary": 2, "missing": 1})
        # matched 2.0 + 1.5 + 1.0 + 1.0 of a total 6.5: missing data stays in the denominator
        self.assertEqual(score, round(5.5 / 6.5 * 100, 2))

    def test_rows_without_sec_values_get_the_yfinance_verdicts_back(self):
        details, score, sources = sfr.sec_first_rules(RULES, {})
        self.assertEqual([d["status"] for d in details], ["failed", "missing_data", "passed", "passed", "missing_data"])
        self.assertEqual(sources["sec_edgar"], 0)
        self.assertEqual(score, round(2.0 / 6.5 * 100, 2))


if __name__ == "__main__":
    unittest.main()


class ConceptChoiceTests(unittest.TestCase):
    def test_the_most_recent_concept_wins_and_periods_are_never_mixed(self):
        old_revenue = [flow_fact(26914, "2021-02-01", "2022-01-30", form="10-K", filed="2022-03-18")]
        raw = company()
        raw["facts"]["us-gaap"]["Revenues"]["units"]["USD"] = old_revenue  # stopped in 2022
        raw["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"] = {
            "units": {"USD": company()["facts"]["us-gaap"]["Revenues"]["units"]["USD"]}}
        values = sfr.compute_ratios(raw, date(2026, 10, 1))["values"]
        self.assertAlmostEqual(values["profit_margin"], 150 / 1100)  # TTM income / TTM revenue of the same period

    def test_margins_with_revenue_and_income_from_different_periods_stay_unknown(self):
        raw = company()
        raw["facts"]["us-gaap"]["Revenues"]["units"]["USD"] = [
            flow_fact(1000, "2021-01-01", "2021-12-31", form="10-K", filed="2022-02-20")]
        values = sfr.compute_ratios(raw, date(2026, 10, 1))["values"]
        self.assertIsNone(values["profit_margin"])
        self.assertIsNone(values["gross_margin"])

    def test_the_ratio_source_leaves_old_statements_unknown_and_reuses_computed_ratios(self):
        import gzip
        import hashlib
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            body = json.dumps(company()).encode()
            (folder / "CIK0000000001.json.gz").write_bytes(gzip.compress(body))
            (folder / "CIK0000000001.meta.json").write_text(json.dumps({"sha256": hashlib.sha256(body).hexdigest()}))
            (folder / "tickers.json").write_text(json.dumps({"fields": ["cik", "name", "ticker", "exchange"],
                                                             "data": [[1, "Acme", "ACME", "NYSE"]]}))
            source = sfr.SecRatioSource(folder, folder / "tickers.json", today=date(2026, 10, 1))
            self.assertIsNotNone(source.values("ACME")["profit_margin"])
            source.save()
            later = sfr.SecRatioSource(folder, folder / "tickers.json", today=date(2028, 1, 1))
            self.assertIsNone(later.values("ACME")["profit_margin"])  # the statement is now too old: unknown
            self.assertFalse(later.changed)  # read from the ratio cache, not recomputed
            self.assertIsNone(source.values("NOPE"))
