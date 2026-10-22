import unittest
import agent_model_validation as validation

class AgentModelValidationTests(unittest.TestCase):
    def test_calibration_and_manual_gate(self):
        error = validation.calibration_error([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9], bins=2)
        gate = validation.promotion_gate(rows=200, balanced_accuracy=.62,
                                         calibration=error, drift_psi=.1)
        self.assertTrue(gate["eligible_for_manual_shadow_review"])
        self.assertFalse(gate["automatic_promotion"])

    def test_drift_blocks_gate(self):
        gate = validation.promotion_gate(rows=200, balanced_accuracy=.7,
                                         calibration=.1, drift_psi=.3)
        self.assertFalse(gate["eligible_for_manual_shadow_review"])

    def test_cpcv_pbo_is_bounded(self):
        result = validation.probability_backtest_overfit({
            "stable": [.01, .02, .01, .02, .01, .02, .01, .02, .01, .02, .01, .02],
            "noisy": [.10, -.09, .08, -.07, .06, -.05, .04, -.03, .02, -.01, 0, 0],
        }, groups=4, test_groups=1)
        self.assertEqual(result["status"], "assessed")
        self.assertGreaterEqual(result["pbo"], 0)
        self.assertLessEqual(result["pbo"], 1)

if __name__ == "__main__":
    unittest.main()
