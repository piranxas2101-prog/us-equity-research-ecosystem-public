import sqlite3
import unittest
from datetime import date

from research_registry import sec_filing_index as sfi

INDEX = """Description:           Daily Index of EDGAR Dissemination Feed by Form Type
Last Data Received:    September 25, 2026

Form Type   Company Name                                                  CIK
      Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
10-Q             APPLE INC                                                     320193      20260925    edgar/data/320193/0000320193-26-000101.txt
8-K              ARCH CAPITAL GROUP LTD.                                       947484      20260925    edgar/data/947484/0000947484-26-000055.txt
S-4              SHELL ACQUISITION CORP                                        1999999     20260925    edgar/data/1999999/0001999999-26-000003.txt
SC 13G           SOME HOLDER  LLC                                              1234567     20260925    edgar/data/1234567/0001234567-26-000001.txt
"""
# The SEC pads rows with trailing spaces.
INDEX = INDEX.replace("000101.txt", "000101.txt    ")


class ParseTests(unittest.TestCase):
    def test_keeps_financial_and_merger_forms_with_multiword_forms_skipped(self):
        rows = sfi.parse_form_index(INDEX)
        self.assertEqual([(r["form"], r["cik"], r["category"]) for r in rows],
                         [("10-Q", "320193", "financial"), ("8-K", "947484", "financial"), ("S-4", "1999999", "merger")])
        self.assertEqual(rows[0]["filed_date"], "2026-09-25")
        self.assertEqual(rows[0]["accession"], "0000320193-26-000101")
        self.assertEqual(rows[1]["company_name"], "ARCH CAPITAL GROUP LTD.")

    def test_rejects_a_page_that_is_not_an_index(self):
        with self.assertRaises(ValueError):
            sfi.parse_form_index("<html>Request Rate Threshold Exceeded</html>")

    def test_index_url_uses_the_calendar_quarter(self):
        self.assertTrue(sfi.index_url(date(2026, 9, 25)).endswith("/2026/QTR3/form.20260925.idx"))
        self.assertIn("/2026/QTR4/", sfi.index_url(date(2026, 10, 1)))


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.calls = []

    def fetcher(self, pages):
        def fetch(url):
            self.calls.append(url)
            for day, response in pages.items():
                if day in url:
                    if isinstance(response, Exception):
                        raise response
                    return response
            return 404, None
        return fetch

    def run_sync(self, pages, today):
        return sfi.sync(self.db, self.fetcher(pages), today=today, lookback_days=6, pause_seconds=0,
                        sleeper=lambda _s: None)

    def test_records_days_and_never_rereads_them(self):
        # Window 2026-09-22 (Tue) .. 2026-09-28 (Mon); 26-27 are a weekend.
        pages = {"20260925": (200, INDEX.encode("latin-1")), "20260924": (200, INDEX.replace("20260925", "20260924").encode()),
                 "20260923": (200, INDEX.encode()), "20260922": (200, INDEX.encode()),
                 "20260928": OSError("network down")}
        first = self.run_sync(pages, date(2026, 9, 28))
        self.assertEqual(first["no_index"], ["2026-09-26", "2026-09-27"])
        self.assertEqual([f["day"] for f in first["failed"]], ["2026-09-28"])
        calls = len(self.calls)
        self.run_sync(pages, date(2026, 9, 28))
        self.assertEqual(len(self.calls) - calls, 1)  # only the failed day is asked again

    def test_recent_weekday_without_index_is_not_yet_published_but_old_one_is_a_holiday(self):
        result = self.run_sync({}, date(2026, 9, 28))
        # Monday 28: Friday 25 is one business day back, so it may be late, not a holiday.
        self.assertEqual(result["not_yet_published"], ["2026-09-25", "2026-09-28"])
        self.assertIn("2026-09-24", result["no_index"])  # two business days back: a holiday

    def test_filed_since_is_unknown_when_a_day_is_missing(self):
        pages = {d: (200, INDEX.replace("20260925", d).replace("-26-0001", "-26-" + d[-4:]).encode()) for d in
                 ("20260922", "20260923", "20260924", "20260925")}
        self.run_sync(pages, date(2026, 9, 28))  # 28 not yet published
        self.assertIsNone(sfi.filed_since(self.db, 320193, date(2026, 9, 24), until=date(2026, 9, 28)))
        found = sfi.filed_since(self.db, "0000320193", date(2026, 9, 23), until=date(2026, 9, 27))
        self.assertEqual([(f["form"], f["filed_date"]) for f in found], [("10-Q", "2026-09-24"), ("10-Q", "2026-09-25")])
        self.assertEqual(sfi.filed_since(self.db, 947484, date(2026, 9, 25), until=date(2026, 9, 27)), [])
        merger = sfi.filed_since(self.db, 1999999, date(2026, 9, 21), until=date(2026, 9, 25), category="merger")
        self.assertEqual({f["form"] for f in merger}, {"S-4"})


if __name__ == "__main__":
    unittest.main()
