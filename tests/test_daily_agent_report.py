import json
import tempfile
import unittest
from pathlib import Path

import control_center as cc
import daily_agent_report as report


class DailyReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        art = self.root / "output" / "runs" / "full_research_x" / "artifacts"
        art.mkdir(parents=True)
        write = lambda name, payload: (art / name).write_text(json.dumps(payload), encoding="utf-8")  # noqa: E731
        (art.parent / "manifest.json").write_text(json.dumps({"artifacts": [{"path": "a"}]}), encoding="utf-8")
        write("babis_shortlist_latest.json", {
            "universe_total": 100, "shortlist_total": 2, "data_quality_summary": {"history_exclusion_count": 5},
            "universe_routing": {"instrument_observations_deferred_to_lane": {"etf_lane": 40},
                                 "instrument_observations_deferred_to_issuer": 7},
            "shortlist": [
                {"ticker": "ABC", "company": "ABC Corp", "composite_score": 100.0, "risk_level": "Low",
                 "catalyst_bucket": "Earnings", "catalyst_classification_reason": "8-K Item 2.02",
                 "fundamental_rule_details": [{"metric": "roe", "status": "passed"}, {"metric": "forward_pe", "status": "missing"}]},
                {"ticker": "XYZ", "composite_score": 100.0}]})
        write("giannis_rule_based_latest.json", {"results": [{"ticker": "ABC", "giannis_tier": "Core Watchlist", "score_final": 8.1}]})
        write("petros_review_latest.json", {"results": [{"ticker": "ABC", "petros_verdict": "candidate", "petros_score": 8.0,
                                                         "rationale": ["Giannis tier=Core."]}]})
        write("yankos_strategy_latest.json", {"results": [{"ticker": "ABC", "post_gate_verdict": "candidate",
                                                           "execution_eligible": True, "playbook_name": "pb"}]})
        write("periklis_strategy_latest.json", {"results": [{"ticker": "XYZ", "post_gate_verdict": "reject",
                                                             "gate_decision_reasons": ["fundamental gate blocked"]}]})
        write("fundamental_control_plane_batch_latest.json", {
            "counts": {"completed": 1, "companyfacts_from_store": 1, "sector_kpis_from_sec": 1, "sector_peer_groups": 1,
                       "business_descriptions": 1},
            "results": [{"ticker": "TRST", "financial": {"status": "blocked", "permanent": True}},
                        {"ticker": "ABC", "financial": {"status": "completed"},
                         "sector_evidence_coverage": {"answered": 2, "required": 14}}]})

    def test_report_restates_decisions_and_reasons_and_marks_unknowns(self):
        timings = {"steps": [{"key": "babis_refresh", "seconds": 3600, "finished_at": "t"},
                             {"key": "control_plane_batch", "finished_at": None}]}
        text = report.build_report(self.root / "output" / "runs" / "full_research_x", timings)["markdown"]
        self.assertIn("ETF/funds 40", text)
        self.assertIn("τίτλοι εκδότη 7", text)
        self.assertIn("2 εταιρείες έχουν τον ανώτατο βαθμό", text)
        self.assertIn("πέρασαν roe; χωρίς στοιχείο forward_pe", text)
        self.assertIn("8-K Item 2.02", text)
        self.assertIn("Giannis tier=Core.", text)
        self.assertIn("έλεγχοι: fundamental gate blocked", text)
        self.assertIn("δεν ολοκληρώθηκε", text)
        self.assertIn("Companyfacts από την αποθήκη (χωρίς νέα λήψη): 1", text)
        self.assertIn("1 είναι μόνιμα: η SEC δεν δημοσιεύει companyfacts γι' αυτές (404)", text)
        self.assertIn("**XYZ** (άγνωστο)", text)
        self.assertIn("δείκτες κλάδου σε 1, ομάδα ομοειδών (ίδιο SIC) σε 1, περιγραφή από το 10-K σε 1 εταιρείες· απαντημένα πεδία πλαισίου 2 από 14 (14.3%)", text)

    def test_report_explains_what_changed_since_the_previous_run(self):
        runs = self.root / "output" / "runs"
        (runs / "full_research_x" / "manifest.json").write_text(json.dumps({"captured_at": "2026-09-29T00:00:00+00:00"}))
        old = runs / "full_research_old" / "artifacts"
        old.mkdir(parents=True)
        (old.parent / "manifest.json").write_text(json.dumps({"captured_at": "2026-09-28T00:00:00+00:00"}))
        rule = lambda status, seen: [{"metric": "roe", "status": status, "observed": seen}]  # noqa: E731
        write = lambda name, payload: (old / name).write_text(json.dumps(payload), encoding="utf-8")  # noqa: E731
        write("babis_shortlist_latest.json", {"shortlist": [{"ticker": "ABC"}, {"ticker": "GONE"}, {"ticker": "LOST"}]})
        write("giannis_rule_based_latest.json", {"results": [
            {"ticker": "ABC", "giannis_tier": "Avoid For Now", "score_final": 5.0, "fundamental_rule_details": rule("failed", 0.05)}]})
        write("petros_review_latest.json", {"results": [{"ticker": "ABC", "petros_verdict": "watch"}]})
        write("yankos_strategy_latest.json", {"results": [{"ticker": "OLD", "post_gate_verdict": "candidate"}]})
        art = runs / "full_research_x" / "artifacts"
        (art / "all_listed_universe_routes_latest.json").write_text(json.dumps({"routes": [
            {"ticker": "GONE", "separate_lane": "etf_lane", "company_fundamental_eligible": False}]}), encoding="utf-8")
        shortlist = json.loads((art / "babis_shortlist_latest.json").read_text(encoding="utf-8"))
        shortlist["membership"] = {"removed": [{"ticker": "LOST", "reason": "below_exit_composite"}]}
        (art / "babis_shortlist_latest.json").write_text(json.dumps(shortlist), encoding="utf-8")
        (art / "giannis_rule_based_latest.json").write_text(json.dumps({"results": [
            {"ticker": "ABC", "giannis_tier": "Core Watchlist", "score_final": 8.0, "fundamental_rule_details": rule("passed", 0.2)}]}))
        text = report.build_report(runs / "full_research_x")["markdown"]
        self.assertIn("Αλλαγές από το προηγούμενο run (full_research_old)", text)
        self.assertIn("βγήκε **GONE**: τώρα στην κατηγορία ETF/funds", text)
        self.assertIn("βγήκε **LOST**: βγήκε από το μέλος του shortlist: έπεσε κάτω από το όριο εξόδου", text)
        self.assertIn("Avoid For Now -> Core Watchlist: 1", text)
        self.assertIn("**ABC** +3.00 (5.00 -> 8.00): roe: failed -> passed", text)
        self.assertIn("Yankos: υποψήφιοι 1 -> 1 · νέοι: ABC", text)
        self.assertIn("## Τι θέλει την προσοχή σου", text)
        self.assertIn("Νέοι υποψήφιοι του Petros (1): ABC.", text)  # watch -> candidate

    def test_attention_section_is_short_when_nothing_happened_and_names_problems_when_they_do(self):
        quiet = report.build_report(self.root / "output" / "runs" / "full_research_x",
                                    state={"status": "completed_review_required", "technical_errors": 0})["markdown"]
        self.assertIn("- Τίποτα καινούριο σήμερα.", quiet)
        self.assertIn("Γνωστά ανοιχτά θέματα", quiet)
        evaluation = {"results": [{"agent_id": "giannis", "horizon_days": 5, "ordering": "inverted"}]}
        loud = report.build_report(self.root / "output" / "runs" / "full_research_x",
                                   state={"status": "failed", "technical_errors": 2}, outcome_evaluation=evaluation)["markdown"]
        self.assertIn("δεν ολοκληρώθηκε κανονικά (κατάσταση failed)", loud)
        self.assertIn("Τεχνικά σφάλματα: 2.", loud)
        self.assertIn("αντίστροφη σειρά για giannis στις 5 συνεδριάσεις", loud)

    def test_unchanged_rules_still_name_the_score_parts_that_moved(self):
        old = {"score_catalyst": 0.39, "score_penalty": 0.12, "catalyst_category_id": "technical_market_structure",
               "fresh_primary_data": True}
        new = {"score_catalyst": -0.35, "score_penalty": 2.27, "catalyst_category_id": "unknown",
               "fresh_primary_data": False, "fundamentals_fresh": False, "periodic_filing_age_days": 155.0}
        text = report._flipped_rules(old, new)
        self.assertIn("καταλύτης -0.74", text)
        self.assertIn("ποινή ρίσκου +2.15", text)
        self.assertIn("καταλύτης technical_market_structure -> unknown", text)
        self.assertIn("παλιό περιοδικό report (155 ημέρες)", text)
        self.assertIn("φρέσκο filing τις τελευταίες 30 ημέρες: όχι", text)
        recovered = report._flipped_rules(new, {**old, "fundamentals_fresh": True})
        self.assertIn("το περιοδικό report έγινε φρέσκο", recovered)

    def test_unchanged_rules_without_a_score_move_keep_the_generic_text(self):
        self.assertIn("άλλαξε άλλος παράγοντας", report._flipped_rules({"score_penalty": 1.0}, {"score_penalty": 1.0}))

    def test_report_of_an_interrupted_run_says_where_it_stopped(self):
        state = {"status": "interrupted", "last_step": "control_plane_batch", "started_at": "a", "finished_at": "b"}
        text = report.build_report(self.root / "output" / "runs" / "full_research_x", None, state)["markdown"]
        self.assertIn("**Το run διακόπηκε** στο βήμα: Προφίλ, τομεακοί agents, οικονομικά SEC, ανάλυση", text)
        self.assertNotIn("Το run διακόπηκε", report.build_report(self.root / "output" / "runs" / "full_research_x", None,
                                                                {"status": "completed"})["markdown"])

    def test_cli_writes_report_even_for_a_run_without_artifacts(self):
        self.assertEqual(report.main(["--run-id", "failed_run", "--root", str(self.root)]), 0)
        self.assertTrue((self.root / "output" / "daily_reports" / "failed_run.md").is_file())
        with self.assertRaises(SystemExit):
            report.main(["--run-id", "../escape", "--root", str(self.root)])

    def test_control_center_lists_and_serves_reports(self):
        report.main(["--run-id", "full_research_x", "--root", str(self.root)])
        views = cc.Views(self.root)
        latest = views.reports(None)
        self.assertEqual((latest["run_id"], latest["reports"]), ("full_research_x", ["full_research_x"]))
        self.assertIn("Babis", latest["markdown"])
        self.assertEqual(views.reports("../../.env")["run_id"], "full_research_x")  # unknown names fall back


if __name__ == "__main__":
    unittest.main()
