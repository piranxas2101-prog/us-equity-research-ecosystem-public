from __future__ import annotations

import unittest

from sector_sec_ir_pilot import _latest_filings


class SectorSecIrPilotTests(unittest.TestCase):
    def test_latest_filings_keeps_only_research_forms_and_builds_canonical_urls(self):
        payload = {"cik": "0000123456", "filings": {"recent": {
            "form": ["8-K", "10-Q", "10-K"],
            "accessionNumber": ["1-1", "2-2", "3-3"],
            "primaryDocument": ["eight.htm", "quarter.htm", "annual.htm"],
            "filingDate": ["2026-03-01", "2026-02-01", "2026-01-01"],
            "reportDate": ["2026-03-01", "2025-12-31", "2025-12-31"],
        }}}
        rows = _latest_filings(payload)
        self.assertEqual([row["form"] for row in rows], ["10-Q", "10-K"])
        self.assertEqual(rows[0]["url"], "https://www.sec.gov/Archives/edgar/data/123456/22/quarter.htm")

    def test_missing_payload_has_no_filings(self):
        self.assertEqual(_latest_filings(None), [])


if __name__ == "__main__": unittest.main()
