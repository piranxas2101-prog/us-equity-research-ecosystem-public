import unittest

import petros_agent
import research_readiness
from tests.test_fundamental_separation import source_payloads


def readiness(**extra):
    row = {"petros_verdict": "candidate", "fresh_primary_data": False, "risk_level": "Low",
           "instrument_type": "common", "catalyst_scoring_status": "classified"}
    row.update(extra)
    return research_readiness.assess(row)


class ReadinessFreshnessTests(unittest.TestCase):
    def test_current_periodic_report_makes_a_quiet_company_reviewable(self):
        result = readiness(fundamentals_fresh=True)
        self.assertTrue(result["technical_review_eligible"])
        self.assertNotIn("fresh_primary_evidence_missing", result["decision_blockers"])
        self.assertNotIn("fresh_primary_data", result["missing_required_fields"])

    def test_out_of_date_periodic_report_blocks_even_with_a_recent_filing(self):
        result = readiness(fundamentals_fresh=False, fresh_primary_data=True)
        self.assertFalse(result["technical_review_eligible"])
        self.assertIn("fresh_primary_evidence_missing", result["decision_blockers"])

    def test_unknown_periodic_report_keeps_the_any_filing_rule(self):
        self.assertFalse(readiness(fundamentals_fresh=None)["technical_review_eligible"])
        self.assertFalse(readiness()["technical_review_eligible"])
        self.assertTrue(readiness(fresh_primary_data=True)["technical_review_eligible"])

    def test_the_value_may_come_from_the_source_row(self):
        result = research_readiness.assess(
            {"petros_verdict": "candidate", "fresh_primary_data": False, "risk_level": "Low",
             "instrument_type": "common", "catalyst_scoring_status": "classified"},
            source_row={"fundamentals_fresh": True})
        self.assertTrue(result["technical_review_eligible"])


class PetrosFreshnessTests(unittest.TestCase):
    def score(self, **extra):
        _, giannis, _ = source_payloads()
        row = {**giannis["results"][0], **extra}
        return petros_agent.score_petros_fundamental(row, {})

    def test_stale_data_penalty_follows_the_periodic_report_when_known(self):
        recent = self.score(fresh_primary_data=True, fundamentals_fresh=None)
        quiet_but_current = self.score(fresh_primary_data=False, fundamentals_fresh=True)
        self.assertEqual(quiet_but_current["petros_score"], recent["petros_score"])
        quiet_unknown = self.score(fresh_primary_data=False, fundamentals_fresh=None)
        self.assertLess(quiet_unknown["petros_score"], recent["petros_score"])
        stale = self.score(fresh_primary_data=True, fundamentals_fresh=False)
        self.assertLess(stale["petros_score"], recent["petros_score"])

    def test_the_any_filing_flag_is_passed_on_unchanged_for_the_gates(self):
        result = self.score(fresh_primary_data=False, fundamentals_fresh=True, periodic_filing_age_days=40.0)
        self.assertFalse(result["fresh_primary_data"])
        self.assertTrue(result["fundamentals_fresh"])
        self.assertEqual(result["periodic_filing_age_days"], 40.0)


if __name__ == "__main__":
    unittest.main()
