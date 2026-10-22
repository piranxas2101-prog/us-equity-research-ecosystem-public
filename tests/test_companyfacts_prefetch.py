"""Nightly companyfacts prefetch: download what is missing or outdated, within a budget, remember 404s."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import companyfacts_prefetch as cp


class FakeCache:
    def __init__(self, current=(), absent=()):
        self.current, self.absent, self.stored, self.noted = set(current), set(absent), [], []

    def is_current(self, cik):
        return cik in self.current

    def known_absent(self, cik):
        return {"cik": cik} if cik in self.absent else None

    def note_absent(self, cik, status):
        self.noted.append(cik)

    def store(self, cik, raw):
        self.stored.append(cik)


class PrefetchTests(unittest.TestCase):
    def test_only_missing_or_outdated_companies_are_downloaded_within_the_budget(self):
        cache = FakeCache(current={1, 2}, absent={3})
        report = cp.prefetch(cache, {n: f"T{n}" for n in range(1, 8)}, budget=2, fetch=lambda cik: {"cik": cik})
        self.assertEqual(cache.stored, [4, 5])
        c = report["counts"]
        self.assertEqual((c["current"], c["known_absent"], c["downloaded"], c["deferred"]), (2, 1, 2, 2))
        self.assertEqual(report["coverage_pct"], round(100 * 4 / 6, 1))

    def test_a_404_is_remembered_and_other_failures_are_counted(self):
        def fetch(cik):
            raise RuntimeError("SEC Companyfacts HTTP 404" if cik == 1 else "SEC Companyfacts HTTP 503")
        cache = FakeCache()
        report = cp.prefetch(cache, {1: "A", 2: "B"}, budget=10, fetch=fetch)
        self.assertEqual(cache.noted, [1])
        self.assertEqual((report["counts"]["absent_now"], report["counts"]["failed"]), (1, 1))

    def test_company_routes_only_one_cik_each(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "routes.json").write_text(json.dumps({"routes": [
                {"ticker": "AAA", "company_fundamental_eligible": True},
                {"ticker": "AAAW", "company_fundamental_eligible": True, "defer_market_scan": True},
                {"ticker": "FUND", "company_fundamental_eligible": False},
                {"ticker": "AAA.B", "company_fundamental_eligible": True}]}), encoding="utf-8")
            (folder / "tickers.json").write_text(json.dumps({"0": {"ticker": "AAA", "cik_str": 7},
                                                             "1": {"ticker": "AAA.B", "cik_str": 7},
                                                             "2": {"ticker": "FUND", "cik_str": 9}}), encoding="utf-8")
            self.assertEqual(cp.company_ciks(folder / "routes.json", folder / "tickers.json"), {7: "AAA"})


if __name__ == "__main__":
    unittest.main()
