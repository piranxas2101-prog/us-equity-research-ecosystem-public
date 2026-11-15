"""Fundamental factors (owner 2026-11-15): formulas, point in time, unknown stays unknown, composites, lab ranking."""
from __future__ import annotations

from datetime import date
import random
import unittest

import fundamental_factors as ff
import fundamental_lab as fl
import outcome_evaluator as oe

FY, FY_PRIOR = ("2025-01-01", "2025-12-31"), ("2024-01-01", "2024-12-31")
AS_OF = date(2026, 3, 15)


def flow(val, period, form="10-K", filed="2026-02-20"):
    return {"val": val, "start": period[0], "end": period[1], "form": form, "filed": filed}


def point(val, end, form="10-K", filed="2026-02-20"):
    return {"val": val, "end": end, "form": form, "filed": filed}


def company(**override):
    facts = {
        "NetIncomeLoss": [flow(150, FY), flow(100, FY_PRIOR)],
        "NetCashProvidedByUsedInOperatingActivities": [flow(200, FY), flow(120, FY_PRIOR)],
        "OperatingIncomeLoss": [flow(220, FY), flow(160, FY_PRIOR)],
        "ProceedsFromIssuanceOfCommonStock": [flow(50, FY)],
        "PaymentsForRepurchaseOfCommonStock": [flow(30, FY)],
        "PaymentsOfDividends": [flow(40, FY)],
        "ProceedsFromIssuanceOfLongTermDebt": [flow(100, FY)],
        "RepaymentsOfLongTermDebt": [flow(60, FY)],
        "Assets": [point(2000, "2025-12-31"), point(1900, "2024-12-31")],
    }
    facts.update(override)
    facts = {name: rows for name, rows in facts.items() if rows is not None}
    return {"facts": {"us-gaap": {name: {"units": {"USD": rows}} for name, rows in facts.items()}}}


class FactorTests(unittest.TestCase):
    def test_formulas_on_a_complete_annual_report(self):
        result = ff.compute(company(), AS_OF)
        v = result["values"]
        self.assertEqual(result["period_end"], "2025-12-31")
        self.assertAlmostEqual(v["cfo_to_assets"], 200 / 1900, places=5)
        self.assertAlmostEqual(v["cash_op_profit"], (220 - (150 - 200)) / 1900, places=5)  # operating income - accruals
        self.assertAlmostEqual(v["net_equity_finance"], (50 - 30) / 1900, places=5)
        self.assertAlmostEqual(v["net_external_finance"], (50 - 30 - 40 + 100 - 60) / 1900, places=5)
        self.assertAlmostEqual(v["asset_growth"], 2000 / 1900 - 1, places=5)
        self.assertEqual(result["partial"], {})

    def test_unreported_financing_is_unknown_or_listed_never_a_silent_zero(self):
        none = ff.compute(company(ProceedsFromIssuanceOfCommonStock=None, PaymentsForRepurchaseOfCommonStock=None), AS_OF)
        self.assertIsNone(none["values"]["net_equity_finance"])
        self.assertIsNone(none["values"]["net_external_finance"])
        only_buyback = ff.compute(company(ProceedsFromIssuanceOfCommonStock=None, PaymentsOfDividends=None), AS_OF)
        self.assertAlmostEqual(only_buyback["values"]["net_equity_finance"], -30 / 1900, places=5)
        self.assertEqual(only_buyback["partial"]["net_equity_finance"], ["issuance"])
        self.assertEqual(only_buyback["partial"]["net_external_finance"], ["issuance", "dividends"])

    def test_an_item_from_an_older_period_never_enters_todays_value(self):
        result = ff.compute(company(ProceedsFromIssuanceOfCommonStock=[flow(500, FY_PRIOR)]), AS_OF)
        self.assertEqual(result["partial"]["net_equity_finance"], ["issuance"])  # 2024 issuance is not 2025's
        self.assertAlmostEqual(result["values"]["net_equity_finance"], -30 / 1900, places=5)

    def test_issuance_parts_are_added_only_without_a_total(self):
        parts = ff.compute(company(ProceedsFromIssuanceOfCommonStock=[flow(50, FY)],
                                   ProceedsFromStockOptionsExercised=[flow(10, FY)]), AS_OF)
        self.assertAlmostEqual(parts["values"]["net_equity_finance"], (50 + 10 - 30) / 1900, places=5)
        total = ff.compute(company(ProceedsFromIssuanceOrSaleOfEquity=[flow(70, FY)],
                                   ProceedsFromStockOptionsExercised=[flow(10, FY)]), AS_OF)
        self.assertAlmostEqual(total["values"]["net_equity_finance"], (70 - 30) / 1900, places=5)

    def test_point_in_time_a_report_filed_later_is_not_seen(self):
        before = ff.compute(company(), date(2026, 2, 1))  # the 2025 10-K was filed 2026-02-20
        self.assertIsNone(before["period_end"])
        self.assertTrue(all(value is None for value in before["values"].values()))

    def test_stale_reports_give_nothing(self):
        late = ff.compute(company(), date(2027, 6, 1))
        self.assertIsNone(late["values"]["cfo_to_assets"])
        self.assertIsNone(late["values"]["asset_growth"])

    def test_quarters_derive_q4_and_d_roa_compares_the_same_quarter(self):
        q = lambda s, e, v: flow(v, (s, e), form="10-Q")  # noqa: E731
        income = [flow(100, FY_PRIOR), q("2024-01-01", "2024-09-30", 70), q("2024-10-01", "2024-12-31", 30),
                  flow(160, FY), q("2025-01-01", "2025-09-30", 100)]
        quarters = ff.quarters([{**f, "start": date.fromisoformat(f["start"]), "end": date.fromisoformat(f["end"]),
                                 "unit": "USD"} for f in income])
        self.assertEqual(quarters[date(2025, 12, 31)], 60)  # 160 - nine months 100
        self.assertEqual(quarters[date(2024, 12, 31)], 30)  # the 3-month fact itself
        result = ff.compute(company(NetIncomeLoss=income), AS_OF)
        self.assertAlmostEqual(result["values"]["d_roa"], 60 / 2000 - 30 / 1900, places=5)


class CompositeTests(unittest.TestCase):
    def test_percentiles_keep_unknown_and_share_ties(self):
        self.assertEqual(ff.percentiles([3.0, None, 1.0, 2.0]), [1.0, None, 0.0, 0.5])
        self.assertEqual(ff.percentiles([1.0, 1.0, 2.0]), [0.25, 0.25, 1.0])
        self.assertEqual(ff.percentiles([5.0, None]), [None, None])

    def test_composites_signs_and_minimum_components(self):
        good = {"values": {"cash_op_profit": 0.3, "d_roa": 0.02, "net_equity_finance": -0.1, "asset_growth": 0.0}}
        bad = {"values": {"cash_op_profit": -0.2, "d_roa": -0.02, "net_equity_finance": 0.5, "asset_growth": 1.0}}
        thin = {"values": {"cash_op_profit": 0.1}}  # one component known: no composite is invented
        combos = ff.composites([good, bad, thin, None])
        self.assertEqual(combos[0]["long_quality"], 100.0)
        self.assertEqual(combos[0]["avoid"], 0.0)
        self.assertEqual(combos[1]["long_quality"], 0.0)
        self.assertEqual(combos[1]["avoid"], 100.0)
        self.assertIsNone(combos[2]["long_quality"])
        self.assertEqual(combos[2]["long_quality_components"], 1)
        self.assertIsNone(combos[3])

    def test_cfo_stands_in_when_cash_op_profit_is_unknown(self):
        combos = ff.composites([{"values": {"cfo_to_assets": 0.4, "d_roa": 0.0}},
                                {"values": {"cfo_to_assets": -0.1, "d_roa": 0.0}}])
        self.assertGreater(combos[0]["long_quality"], combos[1]["long_quality"])

    def test_outcome_labels_read_the_composites(self):
        decision = {"fundamental_factors": {"composites": {"long_quality": 85.0, "avoid": 10.0}}}
        labels = oe.factor_labels(decision)
        self.assertEqual((labels["giannis_long_quality"], labels["giannis_avoid"]), ("top 20%", "bottom 20%"))
        self.assertEqual(oe.LABEL_ORDER["giannis_avoid"][1][0], "bottom 20%")  # a low avoid score is the good end
        self.assertIsNone(ff.quintile_label(None))
        self.assertEqual(ff.quintile_label(50.0), "middle 60%")

    def test_annotate_ranks_across_the_run_and_keeps_unknown(self):
        class Source:
            def factors(self, ticker):
                return None if ticker == "NONE" else {"values": {"cfo_to_assets": {"A": 0.3, "B": 0.1}[ticker],
                                                                  "net_equity_finance": {"A": -0.1, "B": 0.2}[ticker]}}

            def save(self):
                pass

        rows = [{"ticker": "A"}, {"ticker": "B"}, {"ticker": "NONE"}]
        self.assertEqual(ff.annotate(rows, Source()), 2)
        self.assertEqual(rows[0]["fundamental_factors"]["composites"]["long_quality"], 100.0)
        self.assertIsNone(rows[2]["fundamental_factors"])


class LabFactorTests(unittest.TestCase):
    def rows(self, predictive=True):
        rng = random.Random(1)
        out = []
        for month in range(1, 13):
            for year in (2023, 2024):
                for k in range(60):
                    value = rng.random()
                    ret = (0.1 * value if predictive else 0.0) + rng.gauss(0, 0.02)
                    out.append({"ticker": f"T{k}", "date": f"{year}-{month:02d}-01", "score": 70,
                                "market_cap": 1e9 if k % 2 else 1e8, "factor_partial": [],
                                "factors": {"cfo_to_assets": value}, "forward": {h: ret for h in fl.HORIZONS}})
        fl.add_excess(out)
        fl.add_factor_ranks(out)
        return out

    def test_a_predictive_factor_passes_and_noise_does_not(self):
        rows = self.rows()
        report = {str(h): fl.factor_report(rows, "cfo_to_assets", h) for h in fl.HORIZONS}
        self.assertGreater(report["60"]["rank_ic"]["ic"], 0.5)
        self.assertGreater(report["60"]["best_vs_worst"]["spread_pct"], 0)
        self.assertEqual(fl.verdict(report), "passes (to shadow)")
        noise = self.rows(predictive=False)
        report = {str(h): fl.factor_report(noise, "cfo_to_assets", h) for h in fl.HORIZONS}
        self.assertNotEqual(fl.verdict(report), "passes (to shadow)")

    def test_the_literatures_sign_is_applied(self):
        rows = self.rows()
        for row in rows:  # the same numbers read as equity issuance: more issuance went with *higher* returns here
            row["factors"]["net_equity_finance"] = row["factors"]["cfo_to_assets"]
        fl.add_factor_ranks(rows)
        self.assertLess(fl.rank_ic(rows, "net_equity_finance", 60)["ic"], 0)


if __name__ == "__main__":
    unittest.main()
