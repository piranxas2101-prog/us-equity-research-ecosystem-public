import unittest
import us_equity_monitor as monitor


class ProviderGapClassificationTests(unittest.TestCase):
    def test_company_provider_gap_is_not_a_technical_error(self):
        record = {
            "error_code": "all_providers_exhausted",
            "company_fundamental_eligible": True,
            "instrument_type": "equity_candidate",
            "details": {"failure_class": "provider_coverage_gap"},
        }
        self.assertTrue(monitor.is_expected_instrument_coverage_record(record))

    def test_validation_failure_remains_technical(self):
        record = {
            "error_code": "all_providers_exhausted",
            "company_fundamental_eligible": True,
            "instrument_type": "equity_candidate",
            "details": {"failure_class": "technical_failure"},
        }
        self.assertFalse(monitor.is_expected_instrument_coverage_record(record))


if __name__ == "__main__":
    unittest.main()
