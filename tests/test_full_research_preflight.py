from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

import full_research_preflight as preflight


class FullResearchPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "data/research_universe_sources").mkdir(parents=True)
        (self.root / "data/research_universe_exports").mkdir(parents=True)
        (self.root / "output").mkdir()
        (self.root / ".venv-next/Scripts").mkdir(parents=True)
        self.now = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
        self.packages = {"pandas": "3.0.6", "yfinance": "1.7.0", "scikit-learn": "1.9.1",
                         "TA-Lib": "0.8.0", "openbb": "4.7.2"}
        (self.root / "scanner_config.json").write_text(json.dumps({
            "universe": {"mode": "registry_all_listed", "max_symbols": 0},
            "scan": {"max_price": None},
            "pipeline": {"full_universe": True},
        }), encoding="utf-8")
        (self.root / "requirements-windows-py312.lock").write_text(
            "\n".join(f"{name}=={version} \\" for name, version in self.packages.items()), encoding="utf-8")
        sources = [{"name": name, "status": "ok", "retrieved_at": "2026-09-21T09:00:00+00:00"}
                   for name in ("nasdaqlisted.txt", "otherlisted.txt", "nasdaq_profiles.json")]
        (self.root / "data/research_universe_sources/manifest.json").write_text(
            json.dumps(sources), encoding="utf-8")
        (self.root / "data/research_universe_exports/coverage.json").write_text(json.dumps({
            "snapshot_id": "current", "listings": 13224, "unique_symbols": 13224,
            "exchanges": {"Nasdaq": 5615, "Texas Stock Exchange": 5},
        }), encoding="utf-8")
        (self.root / "output/universe_eligibility_latest.json").write_text(json.dumps({
            "local_snapshot_id": "current", "eligible_count": 5,
            "eligible_symbols": ["PANW", "MU", "UNH", "HWM", "PFBC"],
            "asset_classifier_exclusions": [], "special_exclusions": [],
        }), encoding="utf-8")
        (self.root / "output/all_listed_universe_routes_latest.json").write_text(json.dumps({
            "snapshot_id": "current", "listed_symbol_count": 13224,
            "company_fundamental_route_count": 5,
            "eligible_company_not_listed_count": 0,
            "instrument_observation_route_count": 13219,
            "route_counts": {"company_fundamental_research": 5,
                             "fund_or_etp_observation": 13219},
        }), encoding="utf-8")
        (self.root / "run_full_research_control_plane.ps1").write_text(
            "NoAutoTraderStart = $true\npaper_submission_requested = $false\nlive_money = $false\n",
            encoding="utf-8")

    def evaluate(self):
        return preflight.build_report(
            self.root, now=self.now,
            executable=self.root / ".venv-next/Scripts/python.exe", version_info=(3, 12),
            packages=self.packages, pip_check_ok=True,
            registry_state={"snapshot_id": "current", "unique_symbols": 13224},
        )

    def test_ready_when_runtime_sources_scope_and_safety_match(self) -> None:
        report = self.evaluate()
        self.assertEqual(report["status"], "ready")
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(report["sources"]["catalog_listings"], 13224)
        self.assertEqual(report["eligibility"]["eligible_company_symbols"], 5)

    def test_blocks_price_cap_stale_source_and_unmapped_exchange(self) -> None:
        config = json.loads((self.root / "scanner_config.json").read_text(encoding="utf-8"))
        config["scan"]["max_price"] = 8.0
        (self.root / "scanner_config.json").write_text(json.dumps(config), encoding="utf-8")
        coverage = json.loads((self.root / "data/research_universe_exports/coverage.json").read_text(encoding="utf-8"))
        coverage["exchanges"]["unmapped_exchange:F"] = 5
        (self.root / "data/research_universe_exports/coverage.json").write_text(json.dumps(coverage), encoding="utf-8")
        sources = json.loads((self.root / "data/research_universe_sources/manifest.json").read_text(encoding="utf-8"))
        sources[0]["retrieved_at"] = "2026-09-19T09:00:00+00:00"
        (self.root / "data/research_universe_sources/manifest.json").write_text(json.dumps(sources), encoding="utf-8")
        report = self.evaluate()
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["checks"]["price_uncapped"])
        self.assertFalse(report["checks"]["sources_fresh_24h"])
        self.assertFalse(report["checks"]["catalog_has_no_unmapped_exchange"])


if __name__ == "__main__":
    unittest.main()
