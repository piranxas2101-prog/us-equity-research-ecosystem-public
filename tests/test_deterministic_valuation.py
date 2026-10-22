import unittest
import deterministic_valuation as valuation

class DeterministicValuationTests(unittest.TestCase):
    def test_fails_closed_without_positive_fcf(self):
        result = valuation.assess({"share_price": 10, "shares_outstanding": 100, "free_cash_flow_ttm": -2})
        self.assertEqual(result["status"], "not_assessed")

    def test_scenarios_are_ordered_and_auditable(self):
        result = valuation.assess({"share_price": 10, "shares_outstanding": 100, "free_cash_flow_ttm": 100,
                                   "cash": 20, "debt": 10, "risk_free_rate": .04, "beta": 1.1,
                                   "market_cap": 1000, "interest_expense": 1, "effective_tax_rate": .2})
        self.assertEqual(result["status"], "assessed")
        self.assertLess(result["valuation_range"]["low"], result["valuation_range"]["high"])
        self.assertEqual(result["wacc"]["status"], "assessed")
        self.assertEqual(result["monte_carlo"]["simulations"], 1000)
        self.assertIn(result["reverse_dcf"]["status"], {"assessed", "outside_solvable_range"})
        self.assertFalse(result["execution_authority"])
