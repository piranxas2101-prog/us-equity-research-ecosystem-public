from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from research_registry.babis_pipeline import provider_readiness


class BabisPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.policy = root / "policy.json"
        self.rate_db = root / "rate.sqlite3"
        self.policy.write_text(json.dumps({"providers": {
            "fmp": {"maximum_requests_per_utc_day": 225},
            "polygon": {"maximum_requests_per_utc_day": 5000},
        }}), encoding="utf-8")
        db = sqlite3.connect(self.rate_db)
        db.execute("""CREATE TABLE provider_rate_state(
          provider TEXT PRIMARY KEY,utc_day TEXT NOT NULL,day_requests INTEGER NOT NULL,
          minute_window_start REAL NOT NULL,minute_requests INTEGER NOT NULL,
          last_request_at REAL NOT NULL,blocked_until REAL NOT NULL,last_status TEXT NOT NULL)""")
        db.commit(); db.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_active_shared_cooldown_blocks_new_run(self):
        at = datetime(2026, 9, 16, 12, tzinfo=timezone.utc).timestamp()
        db = sqlite3.connect(self.rate_db)
        db.execute("INSERT INTO provider_rate_state VALUES(?,?,?,?,?,?,?,?)",
                   ("fmp", "2026-09-16", 5, at, 1, at, at + 3600, "rate_limited"))
        db.commit(); db.close()
        result = provider_readiness(self.rate_db, self.policy, at)
        self.assertFalse(result["fmp"]["ready"])
        self.assertEqual(result["fmp"]["reason"], "shared_cooldown")
        self.assertTrue(result["polygon"]["ready"])

    def test_daily_budget_blocks_even_without_cooldown(self):
        at = datetime(2026, 9, 16, 12, tzinfo=timezone.utc).timestamp()
        db = sqlite3.connect(self.rate_db)
        db.execute("INSERT INTO provider_rate_state VALUES(?,?,?,?,?,?,?,?)",
                   ("fmp", "2026-09-16", 225, at, 0, at, 0, "ok"))
        db.commit(); db.close()
        result = provider_readiness(self.rate_db, self.policy, at)
        self.assertEqual(result["fmp"]["reason"], "shared_daily_budget_exhausted")

    def test_new_utc_day_resets_daily_count(self):
        at = datetime(2026, 9, 17, 12, tzinfo=timezone.utc).timestamp()
        db = sqlite3.connect(self.rate_db)
        db.execute("INSERT INTO provider_rate_state VALUES(?,?,?,?,?,?,?,?)",
                   ("fmp", "2026-09-16", 225, at - 86400, 0, at - 86400, 0, "ok"))
        db.commit(); db.close()
        result = provider_readiness(self.rate_db, self.policy, at)
        self.assertTrue(result["fmp"]["ready"])
        self.assertEqual(result["fmp"]["day_requests"], 0)


if __name__ == "__main__":
    unittest.main()
