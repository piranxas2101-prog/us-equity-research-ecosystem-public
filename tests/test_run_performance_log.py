import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import run_performance_log as perf


def write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def make_run(root: Path, run_id: str, *, captured: str, tiers: dict[str, int], shortlist: list[str], timings=True):
    run_dir = root / "output" / "runs" / run_id
    art = run_dir / "artifacts"
    write(run_dir / "manifest.json", {"captured_at": captured, "artifacts": []})
    write(art / "babis_shortlist_latest.json", {
        "universe_total": 100, "shortlist_total": len(shortlist), "shortlist": [{"ticker": t} for t in shortlist],
        "data_quality_summary": {"history_exclusion_count": 5, "coverage_exclusion_count": 1},
        "runtime_summary": {"market_data_seconds": 600, "fundamentals_and_news_seconds": 1200, "sec_seconds": 300},
        "universe_routing": {"instrument_observations_deferred_to_lane": {"etf_lane": 40, "spac_lane": 3},
                             "instrument_observations_deferred_to_issuer": 7}})
    write(art / "giannis_rule_based_latest.json", {"results": [
        {"ticker": f"T{i}{tier[0]}", "giannis_tier": tier} for tier, n in tiers.items() for i in range(n)]})
    write(art / "petros_review_latest.json", {"results": [{"ticker": "AAA", "petros_verdict": "candidate"},
                                                           {"ticker": "BBB", "petros_verdict": "reject"}]})
    write(art / "yankos_strategy_latest.json", {"results": [
        {"ticker": "AAA", "post_gate_verdict": "candidate"}, {"ticker": "AAA", "post_gate_verdict": "reject"},
        {"ticker": "BBB", "post_gate_verdict": "reject"}]})
    write(art / "periklis_strategy_latest.json", {"results": []})
    write(art / "fundamental_control_plane_batch_latest.json", {
        "counts": {"completed": 9, "blocked": 2, "failed": 0, "companyfacts_from_store": 4},
        "results": [{"financial": {"permanent": True}}, {"financial": {}}]})
    if timings:
        write(root / "output" / f"pipeline_step_timings_{run_id[-15:]}.json", {
            "run_id": run_id, "steps": [
                {"key": "babis_refresh", "started_at": "2026-10-03T01:30:00+03:00", "finished_at": "2026-10-03T03:30:00+03:00", "seconds": 7200},
                {"key": "control_plane_batch", "started_at": "2026-10-03T03:31:00+03:00", "finished_at": "2026-10-03T04:01:00+03:00", "seconds": 1800}]})


class PerformanceLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        db = sqlite3.connect(self.root / "data" / "market_intel.db") if (self.root / "data").mkdir() is None else None
        db.execute("CREATE TABLE ecosystem_control_plane_gap(gap_id TEXT, run_id TEXT, reason_code TEXT, status TEXT)")
        db.executemany("INSERT INTO ecosystem_control_plane_gap VALUES(?,?,?,?)", [
            ("1", "full_research_20261002_013255", "sector_evidence_missing", "open"),
            ("2", "full_research_20261002_013255", "sector_evidence_missing", "open"),
            ("3", "full_research_20261002_013255", "no_data", "open"),
            ("4", "full_research_20261002_013255", "no_data", "resolved")])
        db.commit()
        db.close()
        make_run(self.root, "full_research_20261002_013255", captured="2026-10-02T15:00:00+00:00",
                 tiers={"Core Watchlist": 3, "Avoid For Now": 2}, shortlist=["AAA", "BBB", "CCC"])

    def test_one_row_collects_timings_counts_gaps_lanes_and_batch(self):
        write(self.root / "output" / "full_research_control_plane_background_latest.json", {
            "run_id": "full_research_20261002_013255", "status": "completed_review_required", "technical_errors": 0,
            "agent_audit_status": "pass", "started_at": "2026-10-02T01:32:55+03:00", "finished_at": "2026-10-02T05:32:55+03:00"})
        row = perf.collect(self.root, "full_research_20261002_013255")
        self.assertEqual((row["status"], row["wall_hours"], row["technical_errors"]), ("completed_review_required", 4.0, 0))
        self.assertEqual((row["babis_scan_min"], row["batch_min"], row["step_yankos_min"]), (120.0, 30.0, None))
        self.assertEqual((row["universe_total"], row["shortlist"], row["deferred_etf"], row["deferred_spac"],
                          row["deferred_issuer_linked"]), (100, 3, 40, 3, 7))
        self.assertEqual((row["scan_market_min"], row["scan_fundamentals_min"], row["scan_sec_min"]), (10.0, 20.0, 5.0))
        self.assertEqual((row["giannis_core"], row["giannis_avoid"], row["giannis_selective"]), (3, 2, 0))
        self.assertEqual((row["petros_candidate"], row["petros_reject"], row["yankos_signals"], row["yankos_candidates"]), (1, 1, 3, 1))
        self.assertEqual((row["gaps_open"], row["gap_sector_evidence_missing"], row["gap_no_data"], row["gap_unsupported_instrument"]),
                         (3, 2, 1, 0))  # resolved gaps do not count; a reason with none is 0, not unknown
        self.assertEqual((row["batch_completed"], row["batch_blocked"], row["companyfacts_from_store"], row["sec_permanent_404"]), (9, 2, 4, 1))
        self.assertIsNone(row["compared_with"])

    def test_a_value_the_run_did_not_record_stays_empty_not_zero(self):
        row = perf.collect(self.root, "full_research_20261002_013255")  # no pointer for this run
        self.assertEqual(row["status"], "")                              # timings exist but no final status
        self.assertIsNone(row["technical_errors"])
        bare = perf.collect(self.root, "full_research_unknown")
        self.assertEqual(bare["status"], "unknown")
        self.assertIsNone(bare["shortlist"])
        self.assertEqual((bare["giannis_core"], bare["petros_candidate"]), (None, None))  # did not run: unknown, not 0

    def test_the_status_of_an_older_run_is_read_from_its_daily_report(self):
        report = self.root / "output" / "daily_reports" / "full_research_20261002_013255.md"
        report.parent.mkdir(parents=True)
        report.write_text("# x\n\n- Κατάσταση run: completed_review_required · ξεκίνησε a · τέλος b\n", encoding="utf-8")
        self.assertEqual(perf.collect(self.root, "full_research_20261002_013255")["status"], "completed_review_required")

    def test_changes_are_measured_against_the_previous_run(self):
        make_run(self.root, "full_research_20261003_013358", captured="2026-10-03T15:00:00+00:00",
                 tiers={"Core Watchlist": 3, "Avoid For Now": 2}, shortlist=["AAA", "BBB", "DDD"])
        # the same tickers get another tier in the newer run
        art = self.root / "output" / "runs" / "full_research_20261003_013358" / "artifacts"
        write(art / "giannis_rule_based_latest.json", {"results": [
            {"ticker": "T0C", "giannis_tier": "Avoid For Now"}, {"ticker": "T1C", "giannis_tier": "Core Watchlist"},
            {"ticker": "T2C", "giannis_tier": "Core Watchlist"}, {"ticker": "T0A", "giannis_tier": "Avoid For Now"},
            {"ticker": "T1A", "giannis_tier": "Avoid For Now"}]})
        row = perf.collect(self.root, "full_research_20261003_013358")
        self.assertEqual(row["compared_with"], "full_research_20261002_013255")
        self.assertEqual((row["shortlist_added"], row["shortlist_removed"]), (1, 1))
        self.assertEqual(row["giannis_tier_changes"], 1)  # T0C moved from Core to Avoid

    def test_record_updates_a_run_instead_of_duplicating_it_and_keeps_old_columns(self):
        perf.record(self.root, "full_research_20261002_013255")
        perf.record(self.root, "full_research_20261002_013255", row={**perf.collect(self.root, "full_research_20261002_013255"),
                                                                    "status": "completed", "brand_new_column": 5})
        with (self.root / "output" / perf.CSV_NAME).open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["status"], rows[0]["brand_new_column"], rows[0]["shortlist"]), ("completed", "5", "3"))
        header = (self.root / "output" / perf.CSV_NAME).read_text(encoding="utf-8-sig").splitlines()[0].split(",")
        self.assertEqual(header[0], "run_id")
        self.assertEqual(header[-1], "brand_new_column")  # added at the end

    def test_markdown_table_lists_the_newest_run_first(self):
        make_run(self.root, "full_research_20261003_013358", captured="2026-10-03T15:00:00+00:00",
                 tiers={"Core Watchlist": 1}, shortlist=["AAA"])
        perf.record(self.root, "full_research_20261002_013255")
        perf.record(self.root, "full_research_20261003_013358")
        lines = (self.root / "output" / perf.MD_NAME).read_text(encoding="utf-8").splitlines()
        body = [line for line in lines if line.startswith("| full_research_")]
        self.assertEqual([line.split(" | ")[0][2:] for line in body], ["full_research_20261003_013358", "full_research_20261002_013255"])

    def test_backfill_adds_only_runs_that_are_not_in_the_history_yet(self):
        make_run(self.root, "full_research_20261003_013358", captured="2026-10-03T15:00:00+00:00", tiers={}, shortlist=["AAA"])
        self.assertEqual(perf.backfill(self.root), ["full_research_20261002_013255", "full_research_20261003_013358"])
        self.assertEqual(perf.backfill(self.root), [])

    def test_cli_rejects_a_path_like_run_id(self):
        with self.assertRaises(SystemExit):
            perf.main(["--root", str(self.root), "record", "--run-id", "../x"])
        self.assertEqual(perf.main(["--root", str(self.root), "record", "--run-id", "full_research_20261002_013255"]), 0)


if __name__ == "__main__":
    unittest.main()
