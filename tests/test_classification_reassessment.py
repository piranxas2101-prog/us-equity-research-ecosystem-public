"""Tests for the multi-source classification reassessment rules."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from research_registry import classification_reassessment as r
from research_registry.classification_governance import POLICY_PATH, disposition, governance_schema, make_change, read_json
from research_registry.core import taxonomy

POLICY = read_json(POLICY_PATH)
NODES = taxonomy()[1]
V = r.Vote


class CorroborationTests(unittest.TestCase):
    def test_chain_agreement_reaches_the_deepest_shared_node(self):
        votes = [V("directory", "technology.software.applications", "x"), V("sec", "technology.software", "x")]
        self.assertEqual(r.corroborated_node(votes), ("technology.software", "agreement_ends"))

    def test_branching_sources_stop_at_their_common_ancestor(self):
        votes = [V("directory", "healthcare.therapeutics.research_support.cro_cdmo", "x"),
                 V("profile", "healthcare.therapeutics.medicines.biotech", "x")]
        self.assertEqual(r.corroborated_node(votes)[0], "healthcare.therapeutics")

    def test_no_two_sources_agree(self):
        votes = [V("directory", "real_estate", "x"), V("profile", "consumer_staples", "x"),
                 V("sec", "consumer_discretionary.services_housing.other.education_personal", "x")]
        self.assertEqual(r.corroborated_node(votes), (None, "no_two_sources_agree"))

    def test_two_competing_pairs_is_not_agreement(self):
        votes = [V("directory", "energy", "x"), V("profile", "energy", "x"),
                 V("sec", "utilities", "x"), V("description", "utilities", "x")]
        self.assertEqual(r.corroborated_node(votes), (None, "competing_pairs"))


class DecisionTests(unittest.TestCase):
    def test_current_more_specific_node_is_kept_when_no_agreeing_source_contradicts(self):
        votes = [V("directory", "industrials.business_services.commercial", "x"),
                 V("sec", "industrials.business_services", "x"),
                 V("profile", "technology", "x")]  # outvoted on the sector: cannot veto depth
        outcome = r.decide(votes, "industrials.business_services.commercial", "provisional")
        self.assertEqual(outcome["decision"], "confirmed")

    def test_wrong_sector_is_corrected_when_two_sources_agree(self):
        votes = [V("directory", "healthcare.therapeutics.medicines", "x"), V("profile", "consumer_staples", "x"),
                 V("sec", "consumer_staples.products.beverages_tobacco.tobacco", "x")]
        outcome = r.decide(votes, "healthcare.therapeutics.medicines", "provisional")
        self.assertEqual((outcome["decision"], outcome["node"]), ("change", "consumer_staples"))

    def test_a_prior_approved_correction_is_never_overturned_automatically(self):
        votes = [V("directory", "real_estate", "x"), V("profile", "real_estate", "x")]
        outcome = r.decide(votes, "consumer_discretionary.services_housing.other.education_personal", "approved_provisional")
        self.assertEqual((outcome["decision"], outcome["reason"]), ("review", "would_contradict_prior_approval"))


class SourceVoteTests(unittest.TestCase):
    def test_catch_all_sic_does_not_vote(self):
        self.assertIsNone(r.sec_vote({"sic": "7389", "sic_description": "Services-Business Services, NEC"}, POLICY, NODES))

    def test_ambiguous_sic_votes_only_for_its_parent(self):
        vote = r.sec_vote({"sic": "2834", "sic_description": "Pharmaceutical Preparations"}, POLICY, NODES)
        self.assertEqual(vote.node, "healthcare.therapeutics.medicines")

    def test_spac_needs_sic_and_name(self):
        self.assertEqual(r.instrument_decision({"name": "EGH Acquisition Corp."}, {"sic": "6770"}, None)[0], "spac")
        self.assertEqual(r.instrument_decision({"name": "Plain Holdings"}, {"sic": "6770"}, None),
                         (None, "sic_6770_without_name_signal"))

    def test_shell_company_profile_is_the_second_spac_signal(self):
        shell = {"sector": "Financial Services", "industry": "Shell Companies"}
        self.assertEqual(r.instrument_decision({"name": "Yorkville Acquisition Corp."}, {"sic": "6199"}, shell),
                         ("spac", "shell_company_profile_and_name"))
        self.assertEqual(r.instrument_decision({"name": "Plum Acquisition Corp. IV"}, {"sic": "4911"}, None),
                         (None, "probable_spac_needs_second_signal"))

    def test_probable_spac_is_not_moved_between_sectors(self):
        listing = {"ticker": "PLMK", "venue_code": "Q", "name": "Plum Acquisition Corp. IV", "instrument_type": "equity_candidate",
                   "node_id": "utilities", "status": "provisional", "base_node": "utilities", "base_reason": "crosswalk"}
        row = r.reassess_listing(listing, {"sector": "Utilities", "industry": "Regulated Electric", "hash": "p"},
                                 {"sic": "4911", "sic_description": "Electric Services", "hash": "s"}, POLICY, NODES)
        self.assertEqual((row["status"], row["reason"]), ("reassessment_review", "probable_spac_needs_second_signal"))

    def test_fund_needs_two_of_three_signals(self):
        sec = {"sic": "6726", "entity_type": "other"}
        self.assertEqual(r.instrument_decision({"name": "BlackRock Income Trust"}, sec, None)[0], "fund_candidate")
        self.assertIsNone(r.instrument_decision({"name": "Acme Industries"}, sec, None)[0])


class GovernanceIntegrationTests(unittest.TestCase):
    def listing(self, **extra):
        base = {"ticker": "PM", "venue": "NYSE", "venue_code": "N", "name": "Philip Morris International",
                "instrument_type": "equity_candidate", "node_id": "healthcare.therapeutics.medicines",
                "status": "provisional", "base_node": "healthcare.therapeutics.medicines", "base_reason": "crosswalk"}
        base.update(extra)
        return base

    def test_agreement_row_becomes_an_approval_candidate(self):
        row = r.reassess_listing(self.listing(), {"sector": "Consumer Defensive", "industry": "Tobacco", "hash": "p"},
                                 {"sic": "2111", "sic_description": "Cigarettes", "hash": "s"}, POLICY, NODES)
        self.assertEqual(row["status"], "reassessment_agreement")
        self.assertEqual(disposition(row, NODES)[0], "approval_candidate")
        change = make_change(row, "snapshot", NODES)
        self.assertTrue(change["changes_value"])
        self.assertTrue(change["new_node"].startswith("consumer_staples"))

    def test_fmp_education_alias_agrees_with_sec_across_provider_sector(self):
        row = r.reassess_listing(self.listing(ticker="EDU", node_id=None, base_node=None),
                                 {"sector": "Consumer Defensive", "industry": "Education & Training Services", "hash": "p"},
                                 {"sic": "8200", "sic_description": "Services-Educational Services", "hash": "s"}, POLICY, NODES)
        self.assertEqual(row["status"], "reassessment_agreement")
        self.assertEqual(row["proposed_node"], "consumer_discretionary.services_housing.other.education_personal")

    def test_disagreement_row_goes_to_human_review_with_evidence(self):
        row = r.reassess_listing(self.listing(ticker="EDU", node_id="real_estate", base_node="real_estate"),
                                 {"sector": "Technology", "industry": "Software - Application", "hash": "p"},
                                 {"sic": "8200", "sic_description": "Services-Educational Services", "hash": "s"}, POLICY, NODES)
        self.assertEqual(disposition(row, NODES), ("human_review", "no_two_sources_agree"))
        self.assertEqual(len(row["source_ref"]["details"]["votes"]), 3)

    def test_description_vote_is_advisory_and_never_creates_agreement(self):
        advisory = [r.Vote("description", "consumer_discretionary.services_housing", "local_llm")]
        row = r.reassess_listing(self.listing(ticker="EDU", node_id="real_estate", base_node="real_estate"),
                                 {"sector": "Technology", "industry": "Software - Application", "hash": "p"},
                                 {"sic": "8200", "sic_description": "Services-Educational Services", "hash": "s"},
                                 POLICY, NODES, advisory)
        self.assertEqual(row["status"], "reassessment_review")
        self.assertEqual(row["source_ref"]["details"]["advisory_votes"][0]["source"], "description")


class ReviewDeskTests(unittest.TestCase):
    @staticmethod
    def row(ticker, status, reason="no_two_sources_agree"):
        return {"venue_code": "Q", "ticker": ticker, "name": ticker + " Inc", "status": status, "reason": reason,
                "old_node": "real_estate", "source_ref": {"details": {
                    "votes": [{"source": "directory", "node": "real_estate", "basis": "x"}],
                    "advisory_votes": [{"source": "description", "node": "consumer_discretionary", "basis": "llm"}]}}}

    def test_disagreements_open_refresh_and_close_by_themselves(self):
        db = sqlite3.connect(":memory:")
        first = r.update_review_desk(db, "s1", [self.row("EDU", "reassessment_review"), self.row("OK", "confirmed_same_node")])
        self.assertEqual((first["opened"], first["open"]), (1, 1))
        second = r.update_review_desk(db, "s2", [self.row("EDU", "reassessment_review")])
        self.assertEqual((second["still_open"], second["open"]), (1, 1))
        self.assertEqual(db.execute("SELECT times_seen FROM classification_review_item WHERE ticker='EDU'").fetchone()[0], 2)
        third = r.update_review_desk(db, "s3", [self.row("EDU", "reassessment_agreement")])
        self.assertEqual((third["resolved"], third["open"]), (1, 0))
        self.assertEqual(db.execute("SELECT resolution FROM classification_review_item WHERE ticker='EDU'").fetchone()[0],
                         "sources_now_agree")

    def test_report_lists_every_vote_and_the_advisory_model(self):
        db = sqlite3.connect(":memory:")
        r.update_review_desk(db, "s1", [self.row("EDU", "reassessment_review")])
        with tempfile.TemporaryDirectory() as tmp:
            text = r.write_review_report(db, Path(tmp)).read_text(encoding="utf-8")
        self.assertIn("| EDU | EDU Inc | real_estate | directory=real_estate | consumer_discretionary |", text)


class OwnerDecisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = sqlite3.connect(":memory:"); self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE universe_snapshot(snapshot_id TEXT PRIMARY KEY, retrieved_at TEXT, provenance_json TEXT, summary_json TEXT);
            CREATE TABLE listed_symbol(snapshot_id TEXT, venue_code TEXT, ticker TEXT, name TEXT, instrument_type TEXT,
                                       scheme TEXT, version TEXT, node_id TEXT, status TEXT, payload_json TEXT);
            INSERT INTO universe_snapshot VALUES('s','2026-09-28','{}','{}');
            INSERT INTO listed_symbol VALUES('s','Q','ABNB','Airbnb','equity_candidate','x','2.1.0',
              'industrials.business_services.commercial','provisional','{"venue":"Nasdaq"}');
        """)
        from research_registry.classification_governance import governance_schema
        governance_schema(self.db)
        r.update_review_desk(self.db, "s", [ReviewDeskTests.row("ABNB", "reassessment_review")])
        self.db.execute("UPDATE classification_review_item SET venue_code='Q'")

    def tearDown(self):
        self.tmp.cleanup()

    def decide(self, **kwargs):
        return r.owner_decision(self.db, "Q", "ABNB", jobs_root=self.root / "jobs", batches_root=self.root / "batches", **kwargs)

    def effective(self):
        return tuple(self.db.execute("SELECT node_id, status FROM effective_listed_symbol WHERE ticker='ABNB'").fetchone())

    def test_owner_choice_is_applied_through_a_reversible_batch(self):
        node = "consumer_discretionary.services_housing.leisure.travel"
        result = self.decide(node=node, note="Online travel marketplace")
        self.assertEqual(self.effective(), (node, "approved_provisional"))
        self.assertEqual(self.db.execute("SELECT resolution FROM classification_review_item").fetchone()[0], "owner_chose_node")
        from research_registry.classification_governance import rollback_batch
        rollback_batch(self.db, result["batch_id"], "tester")
        self.assertEqual(self.effective(), ("industrials.business_services.commercial", "provisional"))

    def test_owner_can_confirm_the_current_node(self):
        self.decide(node=None, note="Checked the 10-K")
        self.assertEqual(self.effective(), ("industrials.business_services.commercial", "approved_provisional"))

    def test_owner_decision_needs_a_reason_and_a_real_node(self):
        with self.assertRaisesRegex(ValueError, "reason"):
            self.decide(node="technology", note=" ")
        with self.assertRaisesRegex(ValueError, "taxonomy"):
            self.decide(node="made.up.node", note="x")

    def test_approved_value_is_not_reopened_by_disagreeing_sources(self):
        votes = [r.Vote("directory", "real_estate", "x"), r.Vote("profile", "consumer_staples", "x")]
        outcome = r.decide(votes, "consumer_discretionary.services_housing.other.education_personal", "approved_provisional")
        self.assertEqual(outcome["decision"], "confirmed")


class DailyJobTests(unittest.TestCase):
    def test_daily_job_writes_nothing_when_nothing_changes(self):
        db = sqlite3.connect(":memory:"); db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE listed_symbol(snapshot_id TEXT, venue_code TEXT, ticker TEXT, name TEXT, instrument_type TEXT,
                                       scheme TEXT, version TEXT, node_id TEXT, status TEXT, payload_json TEXT);
            INSERT INTO listed_symbol VALUES('s','Q','ABC','Example','equity_candidate','x','2.1.0','technology',
                                             'provisional','{"venue":"Nasdaq","reason":"provider_sector_only"}');
        """)
        governance_schema(db)
        with tempfile.TemporaryDirectory() as tmp:  # no local evidence at all -> only the directory votes
            self.assertIsNone(r.build_job(db, "s", root=Path(tmp), out_root=Path(tmp) / "jobs",
                                          keep={"reassessment_agreement"}))
            self.assertFalse((Path(tmp) / "jobs").exists())


if __name__ == "__main__":
    unittest.main()
