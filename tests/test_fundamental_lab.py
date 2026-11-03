"""Fundamental rules lab (owner 2026-11-03, F1-F5): cross-section baseline, clipping, pass/fail spread, buckets."""
from __future__ import annotations

import unittest

import fundamental_lab as fl


def row(ticker, day, value, forward, pe=None):
    return {"ticker": ticker, "date": day, "values": {"roe": value, "trailing_pe": pe},
            "days_since_report": 40, "forward": {h: forward for h in fl.HORIZONS}}


class LabTests(unittest.TestCase):
    def test_excess_is_against_the_days_median_and_a_bad_print_is_clipped(self):
        rows = [row(f"T{k}", "2024-01-02", 0.2, 0.01 * k) for k in range(200)]
        rows.append(row("BAD", "2024-01-02", 0.2, 500.0))           # a reverse split read as +50,000%
        fl.add_excess(rows)
        self.assertAlmostEqual(rows[100]["excess"][60], 0.0)
        self.assertLess(rows[-1]["excess"][60], 2.0)

    def test_pass_against_fail_spread_per_date_and_unknown_in_neither_group(self):
        rows = []
        for month in range(1, 13):
            day = f"2024-{month:02d}-01"
            rows += [row(f"P{k}", day, 0.2, 0.05) for k in range(6)]
            rows += [row(f"F{k}", day, 0.05, 0.0) for k in range(6)]
            rows += [row(f"U{k}", day, None, 9.0) for k in range(6)]
        fl.add_excess(rows)
        result = fl.spread(rows, lambda r: r["values"]["roe"] is not None and r["values"]["roe"] >= 0.12,
                           lambda r: r["values"]["roe"] is not None and r["values"]["roe"] < 0.12, 60)
        self.assertEqual((result["dates"], result["n_pass"], result["n_fail"]), (12, 72, 72))
        self.assertAlmostEqual(result["spread_pct"], 5.0)
        self.assertEqual(result["years_positive"], "1/1")

    def test_negative_pe_is_its_own_bucket_never_below_35(self):
        rows = [row("L", "2024-01-02", None, 0.0, pe=-5.0), row("C", "2024-01-02", None, 0.0, pe=12.0)]
        fl.add_excess(rows)
        buckets = fl.by_bucket(rows, lambda r: (v if (v := r["values"]["trailing_pe"]) is None or v > 0 else -1.0),
                               fl.PE_BUCKETS, 60)
        self.assertEqual([b["n"] for b in buckets[:2]], [1, 1])


if __name__ == "__main__":
    unittest.main()
