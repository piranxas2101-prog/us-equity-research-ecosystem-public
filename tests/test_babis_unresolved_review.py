from __future__ import annotations

import unittest

from babis_unresolved_review import rebase_proposal


class BabisUnresolvedReviewTests(unittest.TestCase):
    def source(self, proposed="technology.software.applications.enterprise"):
        return {
            "ticker": "TEST", "venue_code": "Q", "status": "classification_proposal_review",
            "source_status": "ok", "reason": "explicit_sic_crosswalk", "relation": "new_assignment",
            "proposed_node": proposed, "proposed_instrument_type": "equity_candidate",
            "source_ref": {"provider":"sec","url":"https://data.sec.gov/submissions/",
                           "observed_at":"2026-09-17T00:00:00Z","hash":"abc",
                           "details":{"cik":"1","sic":"7372"}},
        }

    def test_rebase_uses_current_effective_baseline(self):
        row = rebase_proposal(self.source(), "equity_candidate", None)
        self.assertIsNone(row["old_node"])
        self.assertEqual(row["relation"], "new_assignment")
        self.assertEqual(row["proposed_instrument_type"], "equity_candidate")

    def test_cross_sector_remains_review(self):
        row = rebase_proposal(self.source(), "equity_candidate", "healthcare.therapeutics")
        self.assertEqual(row["status"], "classification_conflict_review")
        self.assertEqual(row["relation"], "cross_sector_conflict")

    def test_sector_only_candidate_is_not_promoted(self):
        row = rebase_proposal(self.source("technology"), "equity_candidate", None)
        self.assertEqual(row["status"], "unresolved")


if __name__ == "__main__":
    unittest.main()
