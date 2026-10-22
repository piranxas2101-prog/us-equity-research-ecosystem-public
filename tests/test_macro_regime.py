import unittest
import macro_regime

class MacroRegimeTests(unittest.TestCase):
    def test_expansion_from_point_in_time_series(self):
        series = {
            "T10Y2Y": {"observations": [{"value": "0.5"}]},
            "UNRATE": {"observations": [{"value": "4.0"}, {"value": "4.0"}]},
            "INDPRO": {"observations": [{"value": "105"}, {"value": "100"}]},
            "BAA10Y": {"observations": [{"value": "2.0"}]},
            "CPIAUCSL": {"observations": [{"value": "310"}, {"value": "309"}]},
        }
        result = macro_regime.classify({"series": series})
        self.assertEqual(result["regime"], "expansion")
        self.assertIn("technology", result["sector_impacts"])

    def test_insufficient_data_is_explicit(self):
        self.assertEqual(macro_regime.classify({"series": {}})["regime"], "insufficient_data")
