import gzip
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

import periodic_filing
from giannis_rule_based_from_shortlist import score_row


def document(entries):
    facts = [{"end": "2026-03-31", "val": 1, "accn": "a", "fy": 2026, "fp": "Q1", "form": form, "filed": filed}
             for form, filed in entries]
    return {"cik": 1, "entityName": "X", "facts": {"us-gaap": {"Revenues": {"units": {"USD": facts}}}}}


def base_row(**extra):
    row = {"ticker": "ABC", "company": "Abc Inc", "share_price": 50.0, "cash": 100.0, "debt": 10.0,
           "fundamental_attention_score": 80.0, "risk_level": "Medium", "instrument_type": "common",
           "catalyst_category_id": "unknown", "catalyst_attention_score": 0.0, "catalyst_direction": "unknown",
           "sec_filing_age_days": None, "fresh_primary_data": True, "dilution_flags": [], "data_age_flags": []}
    row.update(extra)
    return row


class PeriodicFilingParsingTests(unittest.TestCase):
    def test_newest_periodic_report_ignores_8k_and_amendments(self):
        raw = document([("10-Q", "2026-05-01"), ("8-K", "2026-09-01"), ("10-Q/A", "2026-08-01"), ("10-K", "2026-02-20")])
        for body in (json.dumps(raw), json.dumps(raw, separators=(",", ":"))):
            self.assertEqual(periodic_filing.latest_periodic_from_bytes(body.encode()), ("2026-05-01", "10-Q"))

    def test_no_periodic_report_is_unknown(self):
        self.assertIsNone(periodic_filing.latest_periodic_from_bytes(json.dumps(document([("8-K", "2026-09-01")])).encode()))

    def test_annual_only_filers_get_a_longer_window(self):
        self.assertEqual(periodic_filing.freshness_limit("10-Q"), 135)
        self.assertEqual(periodic_filing.freshness_limit("40-F"), 400)


class PeriodicFilingIndexTests(unittest.TestCase):
    def store(self, directory, cik, raw, sha):
        (directory / f"CIK{cik:010d}.json.gz").write_bytes(gzip.compress(json.dumps(raw).encode()))
        (directory / f"CIK{cik:010d}.meta.json").write_text(json.dumps({"cik": cik, "sha256": sha}))

    def test_index_reads_only_changed_documents_and_drops_removed_ones(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            self.store(directory, 1, document([("10-Q", "2026-05-01")]), "s1")
            self.store(directory, 2, document([("10-K", "2026-02-01")]), "s2")
            entries = periodic_filing.refresh_index(directory)
            self.assertEqual(entries["1"]["filed"], "2026-05-01")
            self.assertEqual(entries["2"]["form"], "10-K")
            (directory / "CIK0000000001.json.gz").write_bytes(b"not gzip")  # unchanged sha: not read again
            self.assertEqual(periodic_filing.refresh_index(directory)["1"]["filed"], "2026-05-01")
            self.store(directory, 1, document([("10-Q", "2026-08-01")]), "s1b")
            self.assertEqual(periodic_filing.refresh_index(directory)["1"]["filed"], "2026-08-01")
            (directory / "CIK0000000002.meta.json").unlink()
            self.assertNotIn("2", periodic_filing.refresh_index(directory))

    def test_annotation_leaves_unknown_rows_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = [{"ticker": "ZZZ"}]
            count = periodic_filing.annotate_rows(rows, today=date(2026, 10, 1), directory=Path(folder),
                                                  tickers_file=Path(folder) / "missing.json",
                                                  registry_db=Path(folder) / "missing.db")
            self.assertEqual(count, 0)
            self.assertNotIn("periodic_filing_age_days", rows[0])


class CalendarCombinationTests(unittest.TestCase):
    def setup(self, folder):
        directory = Path(folder)
        (directory / "CIK0000000001.json.gz").write_bytes(gzip.compress(json.dumps(document([("10-Q", "2026-04-29")])).encode()))
        (directory / "CIK0000000001.meta.json").write_text(json.dumps({"cik": 1, "sha256": "s"}))
        tickers = directory / "t.json"
        tickers.write_text(json.dumps({"fields": ["cik", "name", "ticker", "exchange"],
                                       "data": [[1, "A", "AAA", "N"], [2, "B", "BBB", "N"]]}))
        db = sqlite3.connect(directory / "r.db")
        db.execute("CREATE TABLE sec_filing_event(accession, cik, form, filed_date, category, company_name, index_day)")
        db.executemany("INSERT INTO sec_filing_event VALUES(?,?,?,?,?,?,?)", [
            ("a1", "1", "10-Q", "2026-07-29", "financial", "A", "d"),
            ("a2", "1", "8-K", "2026-09-20", "financial", "A", "d"),     # not a periodic report
            ("a3", "1", "10-Q/A", "2026-09-25", "financial", "A", "d"),  # amendments do not count
            ("b1", "2", "10-K", "2026-08-10", "financial", "B", "d")])
        db.commit()
        db.close()
        return directory, tickers, directory / "r.db"

    def test_calendar_wins_when_companyfacts_lags_and_adds_unknown_companies(self):
        with tempfile.TemporaryDirectory() as folder:
            directory, tickers, registry = self.setup(folder)
            rows = [{"ticker": "AAA"}, {"ticker": "BBB"}, {"ticker": "CCC"}]
            count = periodic_filing.annotate_rows(rows, today=date(2026, 10, 1), directory=directory,
                                                  tickers_file=tickers, registry_db=registry)
            self.assertEqual(count, 2)
            self.assertEqual((rows[0]["periodic_filing_date"], rows[0]["periodic_filing_age_days"]), ("2026-07-29", 64))
            self.assertEqual(rows[1]["periodic_filing_form"], "10-K")  # no companyfacts, calendar only
            self.assertNotIn("periodic_filing_date", rows[2])

    def test_older_calendar_never_replaces_a_newer_companyfacts_date(self):
        with tempfile.TemporaryDirectory() as folder:
            directory, tickers, registry = self.setup(folder)
            (directory / "CIK0000000001.json.gz").write_bytes(gzip.compress(json.dumps(document([("10-Q", "2026-09-01")])).encode()))
            (directory / "CIK0000000001.meta.json").write_text(json.dumps({"cik": 1, "sha256": "s2"}))
            rows = [{"ticker": "AAA"}]
            periodic_filing.annotate_rows(rows, today=date(2026, 10, 1), directory=directory,
                                          tickers_file=tickers, registry_db=registry)
            self.assertEqual(rows[0]["periodic_filing_date"], "2026-09-01")


class InconclusiveEvidenceTests(unittest.TestCase):
    def test_old_annual_report_stays_unknown_when_the_calendar_is_shorter_than_the_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            tickers = directory / "t.json"
            tickers.write_text(json.dumps({"fields": ["cik", "name", "ticker", "exchange"],
                                           "data": [[1, "F", "FOR", "N"], [2, "Q", "QTR", "N"]]}))
            for cik, form, filed in ((1, "20-F", "2025-04-01"), (2, "10-Q", "2026-04-29")):
                (directory / f"CIK{cik:010d}.json.gz").write_bytes(gzip.compress(json.dumps(document([(form, filed)])).encode()))
                (directory / f"CIK{cik:010d}.meta.json").write_text(json.dumps({"cik": cik, "sha256": f"s{cik}"}))
            db = sqlite3.connect(directory / "r.db")
            db.execute("CREATE TABLE sec_filing_event(accession, cik, form, filed_date, category, company_name, index_day)")
            db.execute("CREATE TABLE sec_filing_index_day(day PRIMARY KEY, status)")
            db.execute("INSERT INTO sec_filing_index_day VALUES('2026-05-14','ok')")
            db.commit()
            db.close()
            rows = [{"ticker": "FOR"}, {"ticker": "QTR"}]
            periodic_filing.annotate_rows(rows, today=date(2026, 10, 1), directory=directory,
                                          tickers_file=tickers, registry_db=directory / "r.db")
            self.assertNotIn("periodic_filing_age_days", rows[0])  # 20-F, 570 days: not conclusive with 140 days of calendar
            self.assertEqual(rows[0]["periodic_filing_note"], "calendar_window_shorter_than_limit")
            self.assertEqual(rows[1]["periodic_filing_age_days"], 155)  # 10-Q: calendar reaches back past 135 days
            self.assertTrue(score_row(base_row(**rows[0]))["fundamentals_fresh"] is None)
            self.assertFalse(score_row(base_row(**rows[1]))["fundamentals_fresh"])


class GiannisSecFirstTests(unittest.TestCase):
    def row(self, **extra):
        rules = [{"metric": "roe", "operator": ">=", "threshold": 0.12, "weight": 2.0, "observed": 0.05, "status": "failed"},
                 {"metric": "current_ratio", "operator": ">=", "threshold": 1.0, "weight": 1.0, "observed": 2.0, "status": "passed"}]
        return base_row(fundamental_attention_score=33.33, fundamental_rule_details=rules, **extra)

    def test_sec_first_is_reported_in_shadow_and_drives_the_score_only_when_switched_on(self):
        values = {"roe": 0.3, "current_ratio": 2.0}
        shadow = score_row(self.row(sec_ratio_values=values))
        self.assertEqual(shadow["fundamental_score_basis"], "babis_stage1")
        self.assertEqual(shadow["fundamental_score_sec_first"], 100.0)
        self.assertEqual(shadow["fundamental_attention_score"], 33.33)
        self.assertEqual(shadow["fundamental_value_sources"], {"sec_edgar": 2, "yfinance_secondary": 0, "missing": 0})
        live = score_row(self.row(sec_ratio_values=values), sec_first=True)
        self.assertEqual(live["fundamental_score_basis"], "sec_first")
        self.assertEqual(live["fundamental_attention_score"], 100.0)
        self.assertGreater(live["score_base"], shadow["score_base"])

    def test_without_sec_ratios_nothing_changes_even_when_switched_on(self):
        plain = score_row(self.row())
        switched = score_row(self.row(), sec_first=True)
        self.assertEqual(switched["score_final"], plain["score_final"])
        self.assertEqual(switched["fundamental_score_basis"], "babis_stage1")
        self.assertIsNone(switched["fundamental_score_sec_first"])


class GiannisFreshnessTests(unittest.TestCase):
    def score(self, **extra):
        return score_row(base_row(**extra))

    def test_without_periodic_date_the_old_any_filing_rule_is_unchanged(self):
        stale = self.score()
        self.assertIn("no_fresh_sec_filing_30d", stale["dilution_flags"])
        self.assertFalse(stale["fresh_primary_data"])
        self.assertIsNone(stale["fundamentals_fresh"])
        recent = self.score(sec_filing_age_days=5)
        self.assertEqual(round(stale["score_penalty"] - recent["score_penalty"], 2), 1.55)  # 1.2 + one flag

    def test_current_10q_no_longer_costs_points_for_a_quiet_month(self):
        unknown = self.score()
        current = self.score(periodic_filing_age_days=45, periodic_filing_form="10-Q")
        self.assertTrue(current["fundamentals_fresh"])
        self.assertEqual(round(unknown["score_penalty"] - current["score_penalty"], 2), 1.55)
        self.assertGreater(current["score_final"], unknown["score_final"])

    def test_legacy_mode_no_filing_is_not_negative_but_an_unclassified_filing_still_is(self):
        def legacy(**extra):
            return score_row(base_row(**extra), catalyst_mode="legacy")
        quiet = legacy()  # category unknown, no filing in the window
        self.assertEqual(quiet["score_catalyst"], 0.0)
        unclassified = legacy(sec_filing_age_days=5)  # a filing existed but could not be classified
        self.assertEqual(unclassified["score_catalyst"], -0.35)
        with_evidence = legacy(catalyst_evidence=[{"form": "8-K"}])
        self.assertEqual(with_evidence["score_catalyst"], -0.35)
        self.assertEqual(self.score(sec_filing_age_days=5)["score_catalyst"], 0.0)  # content_events: no penalty
        self.assertEqual(self.score(sec_filing_age_days=5)["score_catalyst_legacy"], -0.35)

    def test_downstream_flags_keep_their_meaning(self):
        current = self.score(periodic_filing_age_days=45, periodic_filing_form="10-Q")
        self.assertFalse(current["fresh_primary_data"])  # execution gates still see "no filing in 30 days"
        self.assertIn("no_fresh_sec_filing_30d", current["dilution_flags"])

    def test_stale_periodic_report_keeps_the_penalty_even_with_a_recent_8k(self):
        stale = self.score(periodic_filing_age_days=200, periodic_filing_form="10-Q", sec_filing_age_days=3)
        self.assertFalse(stale["fundamentals_fresh"])
        self.assertIn("stale_periodic_filing", stale["dilution_flags"])
        fresh = self.score(periodic_filing_age_days=45, periodic_filing_form="10-Q", sec_filing_age_days=3)
        self.assertEqual(round(stale["score_penalty"] - fresh["score_penalty"], 2), 1.55)

    def test_annual_filer_is_fresh_at_300_days(self):
        self.assertTrue(self.score(periodic_filing_age_days=300, periodic_filing_form="40-F")["fundamentals_fresh"])
        self.assertFalse(self.score(periodic_filing_age_days=300, periodic_filing_form="10-Q")["fundamentals_fresh"])


if __name__ == "__main__":
    unittest.main()
