from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from prepare_full_research_universe import build_route_manifest


class PrepareFullResearchUniverseTests(unittest.TestCase):
    def test_route_manifest_reconciles_every_listed_symbol(self) -> None:
        routes = {
            "ABC": {"ticker": "ABC", "instrument_type": "equity_candidate",
                    "research_route": "company_fundamental_research",
                    "company_fundamental_eligible": True,
                    "sec_eligibility_ticker": "ABC"},
            "ETF": {"ticker": "ETF", "instrument_type": "etf",
                    "research_route": "fund_or_etp_observation",
                    "company_fundamental_eligible": False},
        }
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "routes.json"
            payload = build_route_manifest(
                routes=routes, snapshot_id="dated", eligible_symbols=["ABC"], output_path=output
            )
            stored = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["listed_symbol_count"], 2)
        self.assertEqual(payload["instrument_observation_route_count"], 1)
        self.assertEqual(stored["snapshot_id"], "dated")

    def test_route_manifest_blocks_company_count_mismatch(self) -> None:
        routes = {
            "ABC": {"ticker": "ABC", "instrument_type": "equity_candidate",
                    "research_route": "company_fundamental_research",
                    "company_fundamental_eligible": True},
        }
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "reconciliation failed"):
                build_route_manifest(
                    routes=routes, snapshot_id="dated", eligible_symbols=["ABC", "XYZ"],
                    output_path=Path(temp) / "routes.json",
                )


if __name__ == "__main__":
    unittest.main()
