import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import pandas as pd

import us_equity_monitor
from research_registry import sec_filing_index as sfi
from stage1_info_cache import Stage1InfoCache, scale_to_price

INFO = {"currentPrice": 100.0, "marketCap": 1000.0, "enterpriseValue": 1200.0, "trailingPE": 20.0,
        "forwardPE": 16.0, "priceToBook": 4.0, "priceToSalesTrailing12Months": 5.0, "pegRatio": 1.5,
        "grossMargins": 0.6, "returnOnEquity": 0.25, "longName": "ABC Corp", "sector": "Technology"}
TODAY = date(2026, 9, 29)


class ScaleTests(unittest.TestCase):
    def test_price_fields_follow_the_close_and_filing_fields_do_not(self):
        scaled = scale_to_price(INFO, 100.0, 110.0)
        self.assertAlmostEqual(scaled["marketCap"], 1100.0)
        self.assertAlmostEqual(scaled["trailingPE"], 22.0)
        self.assertAlmostEqual(scaled["enterpriseValue"], 1300.0)  # debt and cash unchanged
        self.assertEqual((scaled["grossMargins"], scaled["returnOnEquity"]), (0.6, 0.25))


class DecideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.filing_db = self.root / "registry.db"
        db = sqlite3.connect(self.filing_db)
        sfi.schema(db)
        with db:
            for n in range(15, 29):
                day = date(2026, 9, n)
                db.execute("INSERT INTO sec_filing_index_day VALUES(?,?,?,?,?,?)",
                           (day.isoformat(), "ok" if day.weekday() < 5 else "no_index", "u", None, 0, "t"))
        db.close()

    def cache(self, mode="reuse", today=TODAY, stored_on=date(2026, 9, 25), watchlist=frozenset()):
        Stage1InfoCache(self.root / "s", self.filing_db, {"ABC": 1}, mode="populate", today=stored_on).store("ABC", INFO)
        return Stage1InfoCache(self.root / "s", self.filing_db, {"ABC": 1}, mode=mode, today=today,
                               news_watchlist=watchlist, stagger_refresh=False)

    def file(self, form, day):
        db = sqlite3.connect(self.filing_db)
        with db:
            db.execute("INSERT INTO sec_filing_event VALUES(?,?,?,?,?,?,?)", (f"a-{day}", "1", form, day, "financial", "ABC", day))
        db.close()

    def test_reuse_rescales_to_todays_close(self):
        info, provenance = self.cache().decide("ABC", 90.0)
        self.assertAlmostEqual(info["marketCap"], 900.0)
        self.assertEqual((provenance["source"], provenance["reference_price"]), ("cached_info_price_scaled", 100.0))

    def test_an_8k_even_a_few_days_before_storage_forces_a_fresh_ask(self):
        self.file("8-K", "2026-09-21")  # 4 days before storage: yfinance may not have caught up
        self.assertEqual(self.cache().decide("ABC", 90.0)[1]["reason"], "financial_filing_since_storage")

    def test_a_field_yahoo_left_out_keeps_the_stored_value_when_nothing_was_filed(self):
        cache = self.cache()  # stored on 2026-09-25 with returnOnEquity 0.25 and priceToBook 4.0 at 100
        fresh = {k: v for k, v in INFO.items() if k not in ("returnOnEquity", "priceToBook")}
        fresh["currentPrice"] = 110.0
        merged, carried = cache.fill_gaps("ABC", fresh)
        self.assertEqual(merged["returnOnEquity"], 0.25)
        self.assertAlmostEqual(merged["priceToBook"], 4.4)                # rescaled to today's price
        self.assertEqual(carried, {"returnOnEquity": "2026-09-25", "priceToBook": "2026-09-25"})
        self.assertEqual(cache.fill_gaps("ABC", INFO), (INFO, {}))         # nothing missing: unchanged

    def test_no_carry_after_a_filing_or_without_identity(self):
        self.file("10-Q", "2026-09-26")
        fresh = {k: v for k, v in INFO.items() if k != "returnOnEquity"}
        self.assertEqual(self.cache().fill_gaps("ABC", fresh), (fresh, {}))  # a new report may change it: unknown
        self.assertEqual(self.cache().fill_gaps("XYZ", fresh), (fresh, {}))

    def test_a_carried_value_keeps_its_first_date_and_expires(self):
        cache = self.cache()
        fresh = {k: v for k, v in INFO.items() if k != "returnOnEquity"}
        merged, carried = cache.fill_gaps("ABC", fresh)
        Stage1InfoCache(self.root / "s", self.filing_db, {"ABC": 1}, mode="reuse", today=TODAY).store("ABC", merged, carried)
        stored = json.loads((self.root / "s" / "ABC.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["carried_fields"], {"returnOnEquity": "2026-09-25"})
        later = Stage1InfoCache(self.root / "s", self.filing_db, {"ABC": 1}, mode="reuse", today=date(2026, 10, 27),
                                stagger_refresh=False)
        with mock.patch.object(later, "_filings", return_value=[]):
            self.assertEqual(later.fill_gaps("ABC", fresh)[1], {})          # 32 days after it was really read

    def test_old_copy_gap_missing_price_or_identity_force_a_fresh_ask(self):
        self.assertEqual(self.cache(today=date(2026, 10, 5)).decide("ABC", 90.0)[1]["reason"],
                         "cached_copy_older_than_max_age")
        self.assertEqual(self.cache(today=date(2026, 10, 1)).decide("ABC", 90.0)[1]["reason"], "filing_calendar_gap")
        self.assertEqual(self.cache().decide("ABC", None)[1]["reason"], "no_price_to_rescale")
        self.assertEqual(self.cache().decide("XYZ", 90.0)[1]["reason"], "no_cached_copy")
        self.assertIsNone(self.cache(mode="populate").decide("ABC", 90.0)[0])

    def test_populate_keeps_a_young_copy_so_it_can_age_but_reuse_replaces(self):
        folder = self.root / "keep"
        Stage1InfoCache(folder, self.filing_db, {}, mode="populate", today=date(2026, 9, 24)).store("ABC", INFO)
        read = lambda: json.loads((folder / "ABC.json").read_text())["fetched_date_et"]  # noqa: E731
        self.assertFalse(Stage1InfoCache(folder, self.filing_db, {}, mode="populate", today=date(2026, 9, 28)).store("ABC", INFO))
        self.assertEqual(read(), "2026-09-24")
        self.assertTrue(Stage1InfoCache(folder, self.filing_db, {}, mode="populate", today=date(2026, 9, 30)).store("ABC", INFO))
        self.assertEqual(read(), "2026-09-30")
        self.assertTrue(Stage1InfoCache(folder, self.filing_db, {}, mode="reuse", today=date(2026, 10, 1)).store("ABC", INFO))
        self.assertEqual(read(), "2026-10-01")

    def test_a_copy_without_reference_price_is_not_stored(self):
        cache = Stage1InfoCache(self.root / "t", self.filing_db, {}, mode="populate", today=TODAY)
        self.assertFalse(cache.store("NOP", {"marketCap": 1.0}))

    def test_news_only_for_the_watchlist_and_only_in_reuse_mode(self):
        self.assertTrue(self.cache(mode="populate").wants_news("ZZZ"))
        lean = self.cache(watchlist=frozenset({"ABC"}))
        self.assertTrue(lean.wants_news("abc"))
        self.assertFalse(lean.wants_news("ZZZ"))


class ShadowTests(DecideTests):
    def test_shadow_only_compares_aged_copies(self):
        import sys
        from unittest import mock as m

        import stage1_info_cache as s1

        cache = self.cache(stored_on=date(2026, 9, 24))  # 5 days old at TODAY
        Stage1InfoCache(self.root / "s", self.filing_db, {"ABC": 1}, mode="populate", today=TODAY).store("NEW", INFO)
        fake = m.MagicMock()
        fake.Ticker.return_value.get_info.return_value = dict(INFO, currentPrice=90.0)
        with m.patch.dict(sys.modules, {"yfinance": fake}):
            result = s1.shadow(cache, ["ABC", "NEW"], min_age_days=3)
        self.assertEqual((result["compared"], result["too_recent"]), (1, 1))
        self.assertIn("marketCap", result["fields"])


class SnapshotTests(DecideTests):
    def snapshot(self, cache, route=None):
        history = pd.DataFrame({"Close": [90.0] * 260, "Volume": [1000] * 260})
        ticker = mock.MagicMock()
        ticker.get_info.return_value = dict(INFO)
        ticker.fast_info = {}
        ticker.news = [{"title": "n"}]
        with mock.patch.object(us_equity_monitor, "fetch_history_with_routing", return_value=(history, "test", [])), \
                mock.patch.object(us_equity_monitor.yf, "Ticker", return_value=ticker), \
                mock.patch.object(us_equity_monitor, "fetch_sec_recent_filings", return_value=[]):
            result = us_equity_monitor.fetch_snapshot(
                "ABC", 30, 1, data_sources_cfg={"openbb_research": {"enabled": False}},
                listing_context=route or {"company_fundamental_eligible": True}, info_cache=cache)
        return result, ticker

    def test_reuse_skips_info_and_news_and_marks_the_source(self):
        result, ticker = self.snapshot(self.cache())
        ticker.get_info.assert_not_called()
        self.assertAlmostEqual(result["fundamentals"]["market_cap"], 900.0)
        self.assertEqual(result["source_base"]["fundamentals_source"]["source"], "cached_info_price_scaled")
        self.assertEqual((result["news"], result["source_base"]["news_status"]), ([], "not_fetched_outside_watchlist"))

    def test_populate_asks_as_before_and_stores(self):
        cache = Stage1InfoCache(self.root / "p", self.filing_db, {"ABC": 1}, mode="populate", today=TODAY)
        result, ticker = self.snapshot(cache)
        ticker.get_info.assert_called_once()
        self.assertEqual(len(result["news"]), 1)
        self.assertEqual(json.loads((self.root / "p" / "ABC.json").read_text())["reference_price"], 100.0)

    def test_reuse_skips_company_info_for_non_companies(self):
        result, ticker = self.snapshot(self.cache(), route={"company_fundamental_eligible": False})
        ticker.get_info.assert_not_called()
        self.assertEqual(result["source_base"]["fundamentals_source"], {"source": "skipped_not_a_company"})


class StaggeredRefreshTests(unittest.TestCase):
    def test_each_copy_is_renewed_once_per_cycle_and_about_a_seventh_per_night(self):
        cache = Stage1InfoCache(Path("."), Path("x.db"), {}, mode="reuse", today=date(2026, 10, 8))
        symbols = [f"S{n}" for n in range(7000)]
        stored = date(2026, 10, 7)
        tonight = sum(cache.scheduled_refresh(s, stored) for s in symbols)
        self.assertTrue(800 <= tonight <= 1200, tonight)  # ~1/7 of 7,000
        over_cycle = set()
        for offset in range(7):
            day = Stage1InfoCache(Path("."), Path("x.db"), {}, mode="reuse", today=date(2026, 10, 8) + timedelta(days=offset))
            over_cycle |= {s for s in symbols if day.scheduled_refresh(s, stored)}
        self.assertEqual(len(over_cycle), len(symbols))  # every symbol gets its day within the cycle

    def test_a_copy_written_today_is_never_refreshed_again_the_same_day(self):
        cache = Stage1InfoCache(Path("."), Path("x.db"), {}, mode="reuse", today=date(2026, 10, 8))
        self.assertFalse(any(cache.scheduled_refresh(f"S{n}", date(2026, 10, 8)) for n in range(100)))
        off = Stage1InfoCache(Path("."), Path("x.db"), {}, mode="reuse", today=date(2026, 10, 8), stagger_refresh=False)
        self.assertFalse(any(off.scheduled_refresh(f"S{n}", date(2026, 10, 1)) for n in range(100)))


if __name__ == "__main__":
    unittest.main()
