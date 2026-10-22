import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from research_registry import sec_filing_index as sfi
from spac_watch import merger_filings


class SpacWatchTests(unittest.TestCase):
    def test_reports_recent_merger_filings_of_spac_issuers_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "sec.json").write_text(json.dumps({"fields": ["cik", "name", "ticker", "exchange"], "data": [
                [111, "Shell Acq", "SHEL", "Nasdaq"], [222, "Quiet Acq", "QUIE", "Nasdaq"]]}), encoding="utf-8")
            db = sqlite3.connect(root / "registry.db")
            sfi.schema(db)
            with db:
                db.execute("INSERT INTO sec_filing_index_day VALUES('2026-09-25','ok','u',NULL,0,'t')")
                db.executemany("INSERT INTO sec_filing_event VALUES(?,?,?,?,?,?,?)", [
                    ("a1", "111", "S-4", "2026-09-25", "merger", "Shell Acq", "2026-09-25"),
                    ("a2", "111", "8-K", "2026-09-25", "financial", "Shell Acq", "2026-09-25"),
                    ("a3", "111", "425", "2026-07-01", "merger", "Shell Acq", "2026-09-25"),  # outside 30 days
                ])
            db.close()
            found = merger_filings(["SHEL", "QUIE", "NOPE"], until=date(2026, 9, 29),
                                   sec_tickers=root / "sec.json", registry_db=root / "registry.db")
        self.assertEqual(list(found), ["SHEL"])
        self.assertEqual([(f["form"], f["filed_date"]) for f in found["SHEL"]], [("S-4", "2026-09-25")])

    def test_securities_of_one_spac_are_one_issuer_and_filings_are_summarised(self):
        from spac_watch import issuer_count, merger_issuers, summarize_filings

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "sec.json").write_text(json.dumps({"fields": ["cik", "name", "ticker", "exchange"], "data": [
                [111, "Shell Acq", "SHEL", "Nasdaq"], [111, "Shell Acq", "SHELU", "Nasdaq"], [111, "Shell Acq", "SHELW", "Nasdaq"],
                [222, "Other Acq", "OTHR", "Nasdaq"]]}), encoding="utf-8")
            db = sqlite3.connect(root / "registry.db")
            sfi.schema(db)
            with db:
                db.execute("INSERT INTO sec_filing_index_day VALUES('2026-09-25','ok','u',NULL,0,'t')")
                db.executemany("INSERT INTO sec_filing_event VALUES(?,?,?,?,?,?,?)", [
                    ("a1", "111", "425", "2026-09-25", "merger", "Shell Acq", "2026-09-25"),
                    ("a2", "111", "425", "2026-09-25", "merger", "Shell Acq", "2026-09-25"),
                    ("a3", "111", "S-4", "2026-09-10", "merger", "Shell Acq", "2026-09-25"),
                    ("a4", "222", "425", "2026-09-27", "merger", "Other Acq", "2026-09-25"),
                ])
            db.close()
            tickers = ["SHEL", "SHELU", "SHELW", "OTHR"]
            self.assertEqual(issuer_count(tickers, root / "sec.json"), 2)
            issuers = merger_issuers(tickers, until=date(2026, 9, 29), sec_tickers=root / "sec.json",
                                     registry_db=root / "registry.db")
        self.assertEqual([i["tickers"] for i in issuers], [["OTHR"], ["SHEL", "SHELU", "SHELW"]])  # latest filing first
        self.assertEqual(summarize_filings(issuers[1]["filings"]), "425 2026-09-25 (x2), S-4 2026-09-10")

    def test_missing_calendar_reports_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(merger_filings(["SHEL"], until=date(2026, 9, 29), sec_tickers=Path(folder) / "none.json",
                                            registry_db=Path(folder) / "none.db"), {})


if __name__ == "__main__":
    unittest.main()
