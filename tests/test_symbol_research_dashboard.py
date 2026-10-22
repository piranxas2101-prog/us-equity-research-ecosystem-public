from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest

from symbol_research_dashboard import LocalPacketReader, build_dashboard, load_run


class SymbolResearchDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run_id = "research-test-1"
        self.run_dir = self.root / "output" / "runs" / self.run_id
        self.artifact_dir = self.run_dir / "artifacts"
        self.artifact_dir.mkdir(parents=True)
        self.records: list[dict] = []

    def artifact(self, name: str, payload: dict) -> None:
        path = self.artifact_dir / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.records.append({
            "source": str(self.root / "output" / name),
            "snapshot": str(path), "status": "captured",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })

    def manifest(self) -> Path:
        path = self.run_dir / "manifest.json"
        path.write_text(json.dumps({"schema": "ecosystem.run_manifest.v1", "run_id": self.run_id,
                                    "artifacts": self.records, "captured_at": "2026-09-21T00:00:00Z"}), encoding="utf-8")
        return path

    def test_dashboard_escapes_untrusted_text_and_keeps_same_run_symbols(self) -> None:
        bad_name = 'Example </script><script>alert("x")</script>'
        self.artifact("babis_shortlist_latest.json", {"run_id": self.run_id, "shortlist": [
            {"ticker": "ABC", "company": bad_name, "rank": 1, "sector": "Healthcare"},
            {"ticker": "XYZ", "company": "Second", "rank": 2},
        ]})
        self.artifact("giannis_rule_based_latest.json", {"run_id": self.run_id, "results": [
            {"ticker": "ABC", "giannis_tier": "Watchlist", "score_final": 4.0}
        ]})
        self.artifact("yankos_strategy_latest.json", {"run_id": self.run_id, "results": [
            {"ticker": "ABC", "trade_direction": "long", "signal_verdict": "watch"},
            {"ticker": "ABC", "trade_direction": "short", "signal_verdict": "reject"},
        ]})
        output = self.root / "dashboard.html"
        result = build_dashboard(manifest_path=self.manifest(), output_path=output)
        html = output.read_text(encoding="utf-8")
        self.assertEqual(result["symbols"], 2)
        self.assertNotIn(bad_name, html)
        self.assertIn("\\u003c/script\\u003e", html)
        embedded = re.search(r'<script type="application/json" id="dashboard-data">(.*?)</script>', html, re.DOTALL)
        self.assertIsNotNone(embedded)
        data = json.loads(embedded.group(1))
        self.assertEqual(data["symbols"][0]["company"], bad_name)
        self.assertEqual(data["symbols"][0]["giannis"]["giannis_tier"], "Watchlist")
        self.assertEqual([row["trade_direction"] for row in data["symbols"][0]["other_agents"]["yankos"]], ["long", "short"])
        self.assertEqual(data["symbols"][1]["giannis"]["control_status"], "not_in_run")

    def test_changed_snapshot_and_cross_run_payload_are_rejected(self) -> None:
        self.artifact("babis_shortlist_latest.json", {"run_id": self.run_id, "shortlist": [{"ticker": "ABC"}]})
        manifest = self.manifest()
        (self.artifact_dir / "babis_shortlist_latest.json").write_text('{"run_id":"different"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            load_run(manifest)
        self.records[0]["sha256"] = hashlib.sha256((self.artifact_dir / "babis_shortlist_latest.json").read_bytes()).hexdigest()
        manifest = self.manifest()
        with self.assertRaisesRegex(ValueError, "Cross-run"):
            load_run(manifest)

    def test_routes_use_the_exact_babis_universe_snapshot_without_run_id(self) -> None:
        self.artifact("babis_shortlist_latest.json", {
            "run_id": self.run_id, "universe_snapshot_id": "snapshot-1",
            "shortlist": [{"ticker": "ABC"}],
        })
        self.artifact("all_listed_universe_routes_latest.json", {
            "schema": "ecosystem.all_listed_universe_routes.v1",
            "snapshot_id": "snapshot-1", "routes": [{"ticker": "ABC"}],
        })
        _, loaded, verified = load_run(self.manifest())
        self.assertEqual(loaded["routes"]["snapshot_id"], "snapshot-1")
        self.assertIn("all_listed_universe_routes_latest.json", verified)

    def test_routes_reject_a_different_universe_snapshot(self) -> None:
        self.artifact("babis_shortlist_latest.json", {
            "run_id": self.run_id, "universe_snapshot_id": "snapshot-1",
            "shortlist": [{"ticker": "ABC"}],
        })
        self.artifact("all_listed_universe_routes_latest.json", {
            "schema": "ecosystem.all_listed_universe_routes.v1",
            "snapshot_id": "snapshot-2", "routes": [{"ticker": "ABC"}],
        })
        with self.assertRaisesRegex(ValueError, "Cross-universe"):
            load_run(self.manifest())

    def test_control_packet_requires_exact_run_subject_kind_and_ticker(self) -> None:
        db_path = self.root / "flow.sqlite3"
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE ecosystem_evidence_packet_store (packet_id TEXT,kind TEXT,subject_id TEXT,run_id TEXT,payload_json TEXT)")
        db.execute("INSERT INTO ecosystem_evidence_packet_store VALUES (?,?,?,?,?)", (
            "packet-1", "sector_context", "sec:cik:1", self.run_id,
            json.dumps({"payload": {"symbol": "ABC", "business_model": "Sourced description"}}),
        ))
        db.commit(); db.close()
        reader = LocalPacketReader(db_path, self.run_id)
        try:
            self.assertEqual(reader.payload("packet-1", kind="sector_context", subject_id="sec:cik:1", ticker="ABC")["business_model"], "Sourced description")
            self.assertIsNone(reader.payload("packet-1", kind="sector_context", subject_id="sec:cik:2", ticker="ABC"))
            self.assertIsNone(reader.payload("packet-1", kind="fundamental_analysis", subject_id="sec:cik:1", ticker="ABC"))
            self.assertIsNone(reader.payload("packet-1", kind="sector_context", subject_id="sec:cik:1", ticker="XYZ"))
        finally:
            reader.close()

    def test_full_listing_catalog_is_distinct_from_analyzed_shortlist(self) -> None:
        self.artifact("babis_shortlist_latest.json", {
            "run_id": self.run_id, "universe_total": 3,
            "filters": {"max_price": 8.0}, "runtime_summary": {"processed_symbols": 3},
            "shortlist": [{"ticker": "ABC", "company": "Analyzed Inc", "rank": 1}],
        })
        catalog_dir = self.root / "data" / "research_universe_exports"
        catalog_dir.mkdir(parents=True)
        (catalog_dir / "coverage.json").write_text(json.dumps({
            "snapshot_id": "dated", "listings": 2, "unique_symbols": 2,
            "source_file_times": {"nasdaqlisted.txt": "File Creation Time: 0904202621:31"},
        }), encoding="utf-8")
        (catalog_dir / "all_us_listed_symbols.csv").write_text(
            "ticker,venue,name,instrument_type,sector\n"
            "ABC,Nasdaq,Analyzed Inc,equity_candidate,Technology\n"
            "XYZ,NYSE Arca,Unanalyzed ETF,etf,\n", encoding="utf-8")
        output = self.root / "dashboard.html"
        result = build_dashboard(manifest_path=self.manifest(), output_path=output,
                                 catalog_dir=catalog_dir)
        html = output.read_text(encoding="utf-8")
        embedded = re.search(r'<script type="application/json" id="dashboard-data">(.*?)</script>', html, re.DOTALL)
        data = json.loads(embedded.group(1))
        self.assertEqual(result["catalog_symbols"], 2)
        self.assertEqual(result["symbols"], 1)
        self.assertEqual([row["ticker"] for row in data["catalog"]], ["ABC", "XYZ"])
        self.assertEqual(data["catalog_metadata"]["source_date"], "2026-09-04")
        self.assertEqual(data["run_coverage"], {"eligible_universe": 3, "processed_symbols": 3,
                                                 "shortlisted_symbols": 1, "max_price": 8.0})


if __name__ == "__main__":
    unittest.main()
