import gzip
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from companyfacts_cache import CompanyFactsCache, previous_business_day
from provider_rate_limiter import SharedProviderRateLimiter
from research_information_flow import ResearchPacketRegistry
from research_registry import sec_filing_index as sfi
from research_workflow_orchestrator import collect_sec_financials

RAW = {"cik": 1, "entityName": "ABC Corp", "facts": {}}
TODAY = date(2026, 9, 29)  # Tuesday


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.filing_db = self.root / "registry.db"
        db = sqlite3.connect(self.filing_db)
        sfi.schema(db)
        with db:
            for n in range(22, 29):  # calendar covers 2026-09-22 .. 2026-09-28
                day = f"2026-09-{n}"
                db.execute("INSERT INTO sec_filing_index_day VALUES(?,?,?,?,?,?)",
                           (day, "ok" if date.fromisoformat(day).weekday() < 5 else "no_index", "u", None, 0, "t"))
        db.close()

    def cache(self, mode="reuse", today=TODAY, fetched=date(2026, 9, 24), max_age_days=30):
        writer = CompanyFactsCache(self.root / "store", self.filing_db, mode="populate", today=fetched)
        writer.store(1, RAW)
        return CompanyFactsCache(self.root / "store", self.filing_db, mode=mode, today=today, max_age_days=max_age_days)

    def add_filing(self, form, day):
        db = sqlite3.connect(self.filing_db)
        with db:
            db.execute("INSERT INTO sec_filing_event VALUES(?,?,?,?,?,?,?)",
                       (f"acc-{form}-{day}", "1", form, day, "financial", "ABC Corp", day))
        db.close()

    def test_reuses_a_recent_copy_when_nothing_was_filed(self):
        raw, info = self.cache().decide(1)
        self.assertEqual(raw, RAW)
        self.assertEqual((info["source"], info["fetched_date_et"], info["filings_checked_through"]),
                         ("cached_companyfacts", "2026-09-24", "2026-09-28"))

    def test_only_reuse_mode_reuses(self):
        for mode in ("off", "populate"):
            self.assertIsNone(self.cache(mode=mode).decide(1)[0])
        self.assertIsNone(CompanyFactsCache(self.root / "x", mode="off").store(1, RAW))

    def test_a_financial_report_forces_a_download_but_an_8k_does_not(self):
        self.add_filing("8-K", "2026-09-25")
        self.assertIsNotNone(self.cache().decide(1)[0])
        self.add_filing("10-Q", "2026-09-26")
        raw, info = self.cache().decide(1)
        self.assertIsNone(raw)
        self.assertEqual((info["reason"], info["filings"][0]["form"]), ("new_financial_filing", "10-Q"))

    def test_filing_on_the_fetch_day_or_the_day_before_forces_a_download(self):
        self.add_filing("10-K", "2026-09-23")  # day before the fetch: may not be in the copy yet
        self.assertEqual(self.cache().decide(1)[1]["reason"], "new_financial_filing")

    def test_calendar_gap_old_copy_and_tampering_force_a_download(self):
        self.assertEqual(self.cache(today=date(2026, 10, 1)).decide(1)[1]["reason"], "filing_calendar_gap")
        self.assertEqual(self.cache(fetched=date(2026, 8, 20)).decide(1)[1]["reason"], "cached_copy_older_than_max_age")
        cache = self.cache()
        data = self.root / "store" / "CIK0000000001.json.gz"
        data.write_bytes(gzip.compress(json.dumps({"tampered": True}).encode()))
        self.assertEqual(cache.decide(1)[1]["reason"], "cached_copy_hash_mismatch")
        self.assertEqual(cache.decide(2)[1]["reason"], "no_cached_copy")

    def test_missing_calendar_is_unknown_not_empty(self):
        cache = CompanyFactsCache(self.root / "store", self.root / "nothing.db", mode="reuse", today=TODAY)
        CompanyFactsCache(self.root / "store", mode="populate", today=date(2026, 9, 24)).store(1, RAW)
        self.assertEqual(cache.decide(1)[1]["reason"], "filing_calendar_gap")

    def test_previous_business_day_skips_weekends(self):
        self.assertEqual(previous_business_day(date(2026, 9, 28)), date(2026, 9, 25))

    def test_collection_uses_the_store_without_network_or_rate_limit(self):
        cache = self.cache()
        registry = ResearchPacketRegistry(self.root / "flow.sqlite3")
        limiter = SharedProviderRateLimiter()
        with patch.dict(os.environ, {"SEC_USER_AGENT": ""}, clear=False), \
                patch("research_workflow_orchestrator.fetch_companyfacts") as fetch, \
                patch.object(limiter, "permit") as permit:
            result = collect_sec_financials(symbol="ABC", cik=1, as_of="2026-09-29", run_id="run-1",
                                            registry=registry, limiter=limiter, companyfacts_cache=cache)
        fetch.assert_not_called()
        permit.assert_not_called()
        self.assertEqual((result["status"], result["api_calls"], result["companyfacts_source"]),
                         ("completed", 0, "cached_companyfacts"))

    def test_populate_keeps_a_young_copy_so_it_can_age_but_reuse_always_replaces(self):
        store = self.root / "keep_store"
        first = CompanyFactsCache(store, self.filing_db, mode="populate", today=date(2026, 9, 24))
        first.store(1, RAW)
        meta = store / "CIK0000000001.meta.json"
        fetched = lambda: json.loads(meta.read_text())["fetched_date_et"]  # noqa: E731
        later = {**RAW, "entityName": "changed"}
        CompanyFactsCache(store, self.filing_db, mode="populate", today=date(2026, 9, 29)).store(1, later)
        self.assertEqual(fetched(), "2026-09-24")  # 5 days old: kept, so it keeps aging
        CompanyFactsCache(store, self.filing_db, mode="populate", today=date(2026, 10, 5)).store(1, later)
        self.assertEqual(fetched(), "2026-10-05")  # 11 days old: refreshed
        CompanyFactsCache(store, self.filing_db, mode="reuse", today=date(2026, 10, 6)).store(1, RAW)
        self.assertEqual(fetched(), "2026-10-06")  # a fresh download in reuse mode always replaces

    def test_shadow_only_compares_aged_copies_oldest_first(self):
        from unittest.mock import patch

        import companyfacts_cache as cf

        store = self.root / "shadow_store"
        for cik, fetched in ((1, date(2026, 9, 29)), (2, date(2026, 9, 24)), (3, date(2026, 9, 20))):
            CompanyFactsCache(store, self.filing_db, mode="populate", today=fetched).store(cik, RAW)
        cache = CompanyFactsCache(store, self.filing_db, mode="reuse", today=TODAY)
        asked = []

        def fetch(cik, _agent):
            asked.append(cik)
            return RAW

        with patch("sec_xbrl_pilot.fetch_companyfacts", side_effect=fetch), \
                patch("sec_xbrl_pilot.extract_companyfacts", side_effect=lambda raw, as_of: {"same": True}), \
                patch.object(cf.CompanyFactsCache, "decide", side_effect=lambda cik: (RAW, {"fetched_date_et": "x"})):
            result = cf.shadow(cache, "agent", limit=5, as_of="2026-09-29", pause_seconds=0)
        self.assertEqual(asked, [3, 2])  # CIK 1 was fetched today: too recent to prove anything
        self.assertEqual((result["too_recent"], len(result["identical"])), (1, 2))

    def test_material_view_ignores_frame_and_name_whitespace_but_not_values(self):
        import companyfacts_cache as cf

        base = {"schema": "s", "entity_name": "Covista Inc.", "payload_sha256": "a", "as_of": "2026-10-03",
                "fields": {"net_income": {"value": 5, "unit": "USD", "period_end": "2026-06-30", "filed": "2026-08-06",
                                          "frame": "CY2026"}}}
        reframed = json.loads(json.dumps(base))
        reframed["fields"]["net_income"]["frame"] = None
        reframed["entity_name"], reframed["payload_sha256"] = "Covista Inc.", "b"
        self.assertEqual(cf.material_view(base), cf.material_view(reframed))
        for key, value in (("value", 6), ("period_end", "2026-09-30"), ("filed", "2026-10-01"), ("unit", "EUR")):
            changed = json.loads(json.dumps(base))
            changed["fields"]["net_income"][key] = value
            self.assertNotEqual(cf.material_view(base), cf.material_view(changed), key)
        missing = json.loads(json.dumps(base))
        del missing["fields"]["net_income"]
        self.assertNotEqual(cf.material_view(base), cf.material_view(missing))

    def test_shadow_counts_a_frame_only_difference_as_identical_but_reports_it(self):
        from unittest.mock import patch

        import companyfacts_cache as cf

        store = self.root / "mat_store"
        CompanyFactsCache(store, self.filing_db, mode="populate", today=date(2026, 9, 20)).store(1, RAW)
        cache = CompanyFactsCache(store, self.filing_db, mode="reuse", today=TODAY)
        old = {"schema": "s", "entity_name": "A B", "payload_sha256": "x", "fields": {"revenue": {"value": 1, "frame": "CY2026"}}}
        new = {"schema": "s", "entity_name": "A B", "payload_sha256": "y", "fields": {"revenue": {"value": 1, "frame": None}}}
        with patch("sec_xbrl_pilot.fetch_companyfacts", return_value=RAW),                 patch("sec_xbrl_pilot.extract_companyfacts", side_effect=[old, new]),                 patch.object(cf.CompanyFactsCache, "decide", side_effect=lambda cik: (RAW, {"fetched_date_et": "x"})):
            result = cf.shadow(cache, "agent", limit=5, as_of="2026-10-03", pause_seconds=0)
        self.assertEqual((len(result["identical"]), len(result["different"])), (1, 0))
        self.assertEqual(len(result["non_material_differences"]), 1)

    def test_a_404_is_remembered_for_two_weeks_then_asked_again(self):
        store = self.root / "absent_store"
        cache = lambda day, mode="populate": CompanyFactsCache(store, self.filing_db, mode=mode, today=day)  # noqa: E731
        self.assertIsNone(cache(date(2026, 10, 1)).known_absent(7))
        marker = cache(date(2026, 10, 1)).note_absent(7)
        self.assertEqual(marker["recheck_after"], "2026-10-15")
        self.assertEqual(cache(date(2026, 10, 14)).known_absent(7)["http_status"], 404)
        self.assertIsNone(cache(date(2026, 10, 15)).known_absent(7))   # time to ask again
        self.assertIsNone(cache(date(2026, 9, 20)).known_absent(7))    # a future-dated marker is not trusted
        self.assertIsNone(cache(date(2026, 10, 2), mode="off").known_absent(7))
        self.assertIsNone(CompanyFactsCache(store, self.filing_db, mode="off", today=date(2026, 10, 1)).note_absent(8))
        cache(date(2026, 10, 3)).store(7, RAW)                         # a real download clears the marker
        self.assertIsNone(cache(date(2026, 10, 3)).known_absent(7))

    def _collect(self, cache, *, error=None, day=TODAY):
        from unittest.mock import patch

        registry = ResearchPacketRegistry(self.root / "flow404.sqlite3")
        limiter = SharedProviderRateLimiter(db_path=self.root / "rate404.sqlite3")
        with patch.dict(os.environ, {"SEC_USER_AGENT": "test agent test@example.com"}, clear=False),                 patch("research_workflow_orchestrator.fetch_companyfacts", side_effect=error) as fetch,                 patch.object(limiter, "permit", wraps=limiter.permit) as permit:
            result = collect_sec_financials(symbol="TRST", cik=7, as_of=day.isoformat(), run_id=f"run-{day}",
                                            registry=registry, limiter=limiter, companyfacts_cache=cache)
        return result, fetch.call_count, permit.call_count

    def test_a_company_the_sec_has_no_companyfacts_for_is_asked_once_not_every_night(self):
        store = self.root / "absent_flow"
        first, fetches, _ = self._collect(CompanyFactsCache(store, self.filing_db, mode="populate", today=TODAY),
                                          error=RuntimeError("SEC Companyfacts HTTP 404"))
        if first["reason_code"] == "rate_limited":
            self.skipTest("rate policy refused the SEC call in this environment")
        self.assertEqual((first["status"], first["reason_code"], first["permanent"], first["api_calls"], fetches),
                         ("blocked", "source_unavailable", True, 1, 1))
        # next night: no request, no rate-limit slot, same label
        again, fetches, permits = self._collect(CompanyFactsCache(store, self.filing_db, mode="populate", today=date(2026, 9, 30)),
                                                error=RuntimeError("must not be called"), day=date(2026, 9, 30))
        self.assertEqual((again["reason_code"], again["permanent"], again["api_calls"], fetches, permits),
                         ("source_unavailable", True, 0, 0, 0))
        # two weeks later it is asked again
        later, fetches, _ = self._collect(CompanyFactsCache(store, self.filing_db, mode="populate", today=date(2026, 10, 13)),
                                          error=RuntimeError("SEC Companyfacts HTTP 404"), day=date(2026, 10, 13))
        self.assertEqual((later["api_calls"], fetches), (1, 1))

    def test_other_http_errors_are_not_treated_as_permanent(self):
        store = self.root / "transient_flow"
        result, fetches, _ = self._collect(CompanyFactsCache(store, self.filing_db, mode="populate", today=TODAY),
                                           error=RuntimeError("SEC Companyfacts HTTP 500"))
        if result["reason_code"] == "rate_limited":
            self.skipTest("rate policy refused the SEC call in this environment")
        self.assertEqual((result["reason_code"], result.get("permanent"), result["api_calls"]), ("source_unavailable", None, 1))
        self.assertFalse(list(store.glob("*.absent.json")))
        _, fetches, _ = self._collect(CompanyFactsCache(store, self.filing_db, mode="populate", today=TODAY),
                                      error=RuntimeError("SEC Companyfacts HTTP 500"))
        self.assertEqual(fetches, 1)  # asked again at once

    def test_fresh_download_is_stored_in_populate_mode(self):
        cache = CompanyFactsCache(self.root / "fresh", self.filing_db, mode="populate", today=TODAY)
        registry = ResearchPacketRegistry(self.root / "flow.sqlite3")
        limiter = SharedProviderRateLimiter(db_path=self.root / "rate.sqlite3")
        with patch.dict(os.environ, {"SEC_USER_AGENT": "test agent test@example.com"}, clear=False), \
                patch("research_workflow_orchestrator.fetch_companyfacts", return_value=RAW):
            result = collect_sec_financials(symbol="ABC", cik=1, as_of="2026-09-29", run_id="run-1",
                                            registry=registry, limiter=limiter, companyfacts_cache=cache)
        if result["status"] == "blocked":  # the shared rate ledger may refuse; nothing is stored then
            self.skipTest("rate policy refused the SEC call in this environment")
        self.assertEqual(result["companyfacts_source"], "fresh_download")
        self.assertTrue((self.root / "fresh" / "CIK0000000001.meta.json").is_file())


if __name__ == "__main__":
    unittest.main()
