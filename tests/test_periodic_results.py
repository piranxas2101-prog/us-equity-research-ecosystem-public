"""Results per period: year-to-date figures become quarters, the fourth quarter is the year minus nine months,
averages are never differenced, trends are tagged and unknown stays unknown."""
from __future__ import annotations

from datetime import date
import unittest

import periodic_results as pr

AS_OF = date(2026, 10, 22)


def fact(start, end, val, form="10-Q", filed="2026-08-01"):
    return {"start": start, "end": end, "val": val, "form": form, "filed": filed}


def companyfacts(concepts: dict[str, list[dict]], unit: str = "USD") -> dict:
    return {"facts": {"us-gaap": {name: {"units": {unit: rows}} for name, rows in concepts.items()}}}


def quarters(values_by_year: dict[int, list[float]]) -> list[dict]:
    """Year-to-date reporting: Q1 as 3 months, then 6M and 9M cumulative, and the fiscal year."""
    rows = []
    for year, (q1, q2, q3, q4) in values_by_year.items():
        start = f"{year}-01-01"
        rows += [fact(start, f"{year}-03-31", q1), fact(start, f"{year}-06-30", q1 + q2),
                 fact(start, f"{year}-09-30", q1 + q2 + q3),
                 fact(start, f"{year}-12-31", q1 + q2 + q3 + q4, form="10-K", filed=f"{year + 1}-02-15")]
    return rows


class DiscreteTests(unittest.TestCase):
    def test_year_to_date_becomes_quarters_and_q4_is_the_year_minus_nine_months(self):
        raw = companyfacts({"Revenues": quarters({2024: [10, 11, 12, 13], 2025: [14, 16, 18, 20]})})
        series = pr.discrete(pr.concept_facts(raw, ("Revenues",), AS_OF))
        self.assertEqual([series["quarter"][date(2025, m, d)] for m, d in ((3, 31), (6, 30), (9, 30), (12, 31))],
                         [14, 16, 18, 20])

    def test_averages_are_never_differenced(self):
        rows = [fact("2025-01-01", "2025-03-31", 100.0), fact("2025-01-01", "2025-06-30", 101.0)]
        raw = companyfacts({"WeightedAverageNumberOfDilutedSharesOutstanding": rows}, unit="shares")
        facts = pr.concept_facts(raw, ("WeightedAverageNumberOfDilutedSharesOutstanding",), AS_OF, shares=True)
        self.assertEqual(pr.discrete(facts, derive=False)["quarter"], {date(2025, 3, 31): 100.0})


class AnalyseTests(unittest.TestCase):
    def test_trend_margins_and_runway(self):
        raw = companyfacts({
            "Revenues": quarters({2024: [10, 10, 10, 10], 2025: [11, 11, 12, 15]}),
            "GrossProfit": quarters({2024: [4, 4, 4, 4], 2025: [5, 5, 5, 7.5]}),
            "NetIncomeLoss": quarters({2024: [-5, -5, -5, -5], 2025: [-4, -4, -4, -3]}),
            "NetCashProvidedByUsedInOperatingActivities": quarters({2024: [-3, -3, -3, -3], 2025: [-3, -3, -3, -3]}),
            "CashAndCashEquivalentsAtCarryingValue": [{"end": "2025-12-31", "val": 60.0, "form": "10-K",
                                                      "filed": "2026-02-15"}],
        })
        result = pr.analyse(raw, AS_OF)
        self.assertEqual(result["cadence"], "quarterly")
        last = result["periods"][-1]
        self.assertEqual((last["end"], last["revenue_yoy_pct"], last["gross_margin_pct"]), ("2025-12-31", 50.0, 50.0))
        tags = result["tags"]
        self.assertEqual(tags["revenue_trend"], "accelerating")      # +20% -> +50% year on year
        self.assertEqual(tags["gross_margin"], "improving")          # 40% -> 50%
        self.assertEqual(tags["losses"], "narrowing")
        self.assertEqual(tags["cash_runway_months"], 60.0)           # 60 cash / 12 a year burned
        self.assertIsNone(tags["dilution"])                          # no share data: unknown

    def test_an_annual_filer_and_a_company_without_revenue(self):
        rows = [fact("2024-01-01", "2024-12-31", 30.0, form="20-F"), fact("2025-01-01", "2025-12-31", 18.0, form="20-F")]
        result = pr.analyse(companyfacts({"Revenues": rows}), AS_OF)
        self.assertEqual((result["cadence"], result["periods"][-1]["revenue_yoy_pct"]), ("annual", -40.0))
        self.assertEqual(pr.analyse({"facts": {}}, AS_OF)["cadence"], None)


if __name__ == "__main__":
    unittest.main()
