from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import market_intel_db
import periklis_strategy_agent
import yankos_strategy_agent


class StrategySignalStorageTests(unittest.TestCase):
    def test_both_traders_persist_direction_and_classification(self) -> None:
        row = {
            "ticker": "ABC", "playbook_name": "test", "signal_score": 7.5,
            "signal_verdict": "candidate", "confidence": "Medium",
            "trade_direction": "short", "sector_id": "technology",
            "industry_id": "technology.software", "catalyst_category_id": "earnings",
            "rationale": ["fixture"],
        }
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "signals.db"
            yankos_strategy_agent.store_signals(db, "2026-08-25T12:00:00+00:00", [row], "yankos")
            periklis_strategy_agent.store_signals(db, "2026-08-25T12:00:00+00:00", [row], "periklis")
            with closing(market_intel_db.connect(db)) as conn:
                stored = conn.execute("SELECT agent_name, trade_direction, sector_id FROM strategy_signals ORDER BY agent_name").fetchall()
            self.assertEqual(len(stored), 2)
            self.assertEqual({item["trade_direction"] for item in stored}, {"short"})
            self.assertEqual({item["sector_id"] for item in stored}, {"technology"})


if __name__ == "__main__":
    unittest.main()
