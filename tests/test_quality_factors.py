"""Quality factors from SEC companyfacts: known formulas, unknown stays unknown, financials excluded from Altman."""
from __future__ import annotations

from datetime import date
import unittest

import outcome_evaluator as oe
import quality_factors as qf

FY, FY_PRIOR = ("2025-01-01", "2025-12-31"), ("2024-01-01", "2024-12-31")


def flow(val, period, form="10-K"):
    return {"val": val, "start": period[0], "end": period[1], "form": form, "filed": "2026-02-20"}


def point(val, end, form="10-K"):
    return {"val": val, "end": end, "form": form, "filed": "2026-02-20"}


def company(**override):
    facts = {
        "Revenues": [flow(1200, FY), flow(1000, FY_PRIOR)],
        "NetIncomeLoss": [flow(150, FY), flow(100, FY_PRIOR)],
        "NetCashProvidedByUsedInOperatingActivities": [flow(200, FY), flow(120, FY_PRIOR)],
        "GrossProfit": [flow(600, FY), flow(450, FY_PRIOR)],
        "OperatingIncomeLoss": [flow(220, FY), flow(160, FY_PRIOR)],
        "SellingGeneralAndAdministrativeExpense": [flow(240, FY), flow(220, FY_PRIOR)],
        "DepreciationDepletionAndAmortization": [flow(50, FY), flow(45, FY_PRIOR)],
        "WeightedAverageNumberOfSharesOutstandingBasic": [flow(100, FY), flow(100, FY_PRIOR)],
        "Assets": [point(2000, "2025-12-31"), point(1900, "2024-12-31")],
        "AssetsCurrent": [point(800, "2025-12-31"), point(700, "2024-12-31")],
        "LiabilitiesCurrent": [point(400, "2025-12-31"), point(400, "2024-12-31")],
        "Liabilities": [point(900, "2025-12-31"), point(950, "2024-12-31")],
        "LongTermDebtNoncurrent": [point(300, "2025-12-31"), point(350, "2024-12-31")],
        "AccountsReceivableNetCurrent": [point(150, "2025-12-31"), point(130, "2024-12-31")],
        "PropertyPlantAndEquipmentNet": [point(500, "2025-12-31"), point(480, "2024-12-31")],
        "RetainedEarningsAccumulatedDeficit": [point(700, "2025-12-31"), point(550, "2024-12-31")],
        "StockholdersEquity": [point(1100, "2025-12-31"), point(950, "2024-12-31")],
    }
    facts.update(override)
    return {"facts": {"us-gaap": {name: {"units": {"USD": rows}} for name, rows in facts.items()}}}


class QualityFactorTests(unittest.TestCase):
    def test_a_healthy_improving_company_scores_well_on_all_three(self):
        result = qf.compute(company(), today=date(2026, 10, 1), sector="technology")
        self.assertEqual(result["fiscal_year_end"], "2025-12-31")
        self.assertEqual(result["piotroski"]["known"], 9)
        self.assertEqual(result["piotroski"]["score"], 9)
        z = result["altman_z2"]
        expected = 6.56 * 400 / 2000 + 3.26 * 700 / 2000 + 6.72 * 220 / 2000 + 1.05 * 1100 / 900
        self.assertAlmostEqual(z["score"], round(expected, 3))
        self.assertEqual(z["zone"], "safe")
        self.assertIsNotNone(result["beneish"]["score"])
        self.assertFalse(result["beneish"]["flag"])

    def test_missing_inputs_stay_unknown_and_are_listed(self):
        result = qf.compute(company(AccountsReceivableNetCurrent=[]), today=date(2026, 10, 1))
        self.assertIsNone(result["beneish"]["score"])
        self.assertIn("dsri", result["beneish"]["missing"])
        partial = qf.compute(company(LongTermDebtNoncurrent=[]), today=date(2026, 10, 1))
        self.assertIsNone(partial["piotroski"]["score"])  # 8 of 9 signals: no score is invented
        self.assertEqual(partial["piotroski"]["known"], 8)

    def test_financials_get_no_altman_and_old_years_get_nothing(self):
        bank = qf.compute(company(), today=date(2026, 10, 1), sector="Financials")
        self.assertIsNone(bank["altman_z2"])
        self.assertFalse(bank["altman_applicable"])
        old = qf.compute(company(), today=date(2028, 1, 1))
        self.assertIsNone(old["piotroski"])

    def test_outcome_buckets_come_from_the_shadow_factors(self):
        decision = {"quality_factors": {"piotroski": {"score": 8}, "altman_z2": {"zone": "grey"},
                                        "beneish": {"flag": True}}}
        self.assertEqual(oe.factor_labels(decision), {"giannis_piotroski": "strong (7-9)", "giannis_altman": "grey",
                                                      "giannis_beneish": "manipulation warning"})
        self.assertEqual(oe.factor_labels({}), {"giannis_piotroski": None, "giannis_altman": None, "giannis_beneish": None})


if __name__ == "__main__":
    unittest.main()
