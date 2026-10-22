"""The owner's two notes, followed in shadow: ideas that come back after a pre-open take-down carry a tag; the
holding class that suits a kind of company or environment is found from the shadow holds."""
from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest

import holding_shadow as hs
import paper_trade_ledger as ledger


class ReturnedTests(unittest.TestCase):
    def test_the_previous_checks_take_downs_and_their_kind(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            history = out / "preopen" / "history"
            history.mkdir(parents=True)
            (history / "preopen_check_20261021.json").write_text(json.dumps({"rows": [
                {"agent": "yankos", "ticker": "VRTX", "playbook": "p", "direction": "long", "decision": "demoted_to_watch",
                 "demote_reasons": ["New SEC event after the last close (8-K item 5.02)"]},
                {"agent": "yankos", "ticker": "KARO", "playbook": "p", "direction": "long", "decision": "demoted_to_watch",
                 "demote_reasons": ["Pre-market gap -6.52% against the long trade"]}]}), encoding="utf-8")
            previous = ledger.previous_demotions(out, date(2026, 10, 22))
            key = ("yankos", "VRTX", "p", "long")
            self.assertEqual(ledger.returned_after_demotion(previous, key, "candidate"), "after_filing")
            self.assertEqual(ledger.returned_after_demotion(previous, ("yankos", "KARO", "p", "long"), "candidate"),
                             "after_gap")
            self.assertEqual(ledger.returned_after_demotion(previous, key, "watch"), "no")
            self.assertEqual(ledger.returned_after_demotion(previous, ("yankos", "AAA", "p", "long"), "candidate"), "no")
            self.assertIsNone(ledger.previous_demotions(out, date(2026, 10, 21)))  # nothing before the first check
            self.assertEqual(ledger.returned_after_demotion(None, key, "candidate"), "unknown")


class HoldingByCategoryTests(unittest.TestCase):
    def test_a_kind_of_company_where_longer_holds_win_is_found(self):
        records = []
        start = date(2026, 9, 1)
        for i in range(240):
            day = start + timedelta(days=i % 30)
            strong = i % 2 == 0
            longer_better = (i % 10 != 0) if strong else (i % 10 == 0)  # 90% for strong, 10% for weak
            records.append({"session_date": str(day),
                            "categories": {"piotroski": "strong" if strong else "weak", "sector": "x"},
                            "holds": {"day": {"fixed": {"excess_pct": 0.0}},
                                      "week": {"fixed": {"excess_pct": 1.0 if longer_better else -1.0}}}})
        result = hs.category_findings(records, min_records=30, min_days=10)
        found = {(f["class"], f["tag"], f["value"]) for f in result["findings"]}
        self.assertIn(("week", "piotroski", "strong"), found)
        self.assertNotIn(("week", "sector", "x"), found)  # one value only: no rest to compare with
        self.assertEqual(hs.category_findings([], min_records=30, min_days=10)["findings"], [])


if __name__ == "__main__":
    unittest.main()
