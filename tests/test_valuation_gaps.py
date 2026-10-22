"""A value without meaning fails and opens a gap; the gap is chased every night and closed when filled."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest

import sec_fundamental_ratios as sfr
import us_equity_monitor as m
import valuation_gaps as vg

RULE = {"metric": "forward_pe", "operator": "<=", "value": 35, "weight": 1.0, "invalid_at_or_below": 0}


class RuleTests(unittest.TestCase):
    def test_a_negative_forward_pe_fails_with_a_gap_and_a_real_one_is_judged(self):
        score, coverage, details = m.evaluate_rules({"forward_pe": -6.07}, [RULE])  # LAES 2026-10-22
        self.assertEqual((score, coverage, details[0]["status"], details[0]["gap"]),
                         (0.0, 100.0, "failed", vg.REASON))
        score, _, details = m.evaluate_rules({"forward_pe": 20.0}, [RULE])
        self.assertEqual((score, details[0]["status"]), (100.0, "passed"))
        self.assertNotIn("gap", details[0])
        _, _, details = m.evaluate_rules({"forward_pe": None}, [RULE])
        self.assertEqual(details[0]["status"], "missing_data")  # unknown stays unknown

    def test_the_sec_first_path_keeps_the_same_judgement(self):
        _, _, details = m.evaluate_rules({"forward_pe": -6.07}, [RULE])
        out, score, _ = sfr.sec_first_rules(details, {})
        self.assertEqual((out[0]["status"], out[0]["gap"], score), ("failed", vg.REASON, 0.0))

    def test_forward_pe_from_price_and_forward_eps_when_yahoo_has_none(self):
        self.assertEqual(m.forward_pe_from({"forwardPE": None, "forwardEps": 0.5, "currentPrice": 10.0}), 20.0)
        self.assertIsNone(m.forward_pe_from({"forwardEps": -0.2, "currentPrice": 10.0}))
        self.assertEqual(m.forward_pe_from({"forwardPE": -6.0}), -6.0)


class GapTests(unittest.TestCase):
    def test_open_keep_and_close_with_the_value(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "gaps.json"
            failing = [{"metric": "forward_pe", "status": "failed", "observed": -6.0, "gap": vg.REASON}]
            gaps = vg.Gaps(path, today=date(2026, 10, 22))
            gaps.observe("laes", failing)
            self.assertEqual(gaps.save(), {"open": 1, "resolved": 0})
            gaps = vg.Gaps(path, today=date(2026, 10, 23))
            gaps.observe("LAES", failing)
            self.assertEqual(gaps.state["LAES"]["forward_pe"]["nights"], 2)
            self.assertEqual(gaps.open_tickers(), frozenset({"LAES"}))
            gaps = vg.Gaps(path, today=date(2026, 11, 20))
            gaps.observe("LAES", [{"metric": "forward_pe", "status": "passed", "observed": 28.0}])
            self.assertEqual(gaps.save(), {"open": 0, "resolved": 1})
            entry = vg.Gaps(path).state["LAES"]["forward_pe"]
            self.assertEqual((entry["resolved"], entry["resolved_value"]), ("2026-11-20", 28.0))

    def test_an_open_gap_is_asked_afresh_by_the_stage1_cache(self):
        from stage1_info_cache import Stage1InfoCache

        cache = Stage1InfoCache(Path("."), Path("x.db"), {}, mode="reuse", today=date(2026, 10, 22))
        cache.always_fresh = frozenset({"LAES"})
        self.assertEqual(cache.decide("laes", 1.64)[1]["reason"], "open_valuation_gap")


if __name__ == "__main__":
    unittest.main()
