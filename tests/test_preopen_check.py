"""Pre-open check: a candidate only goes down to watch, for a gap against it or a new SEC event; unknown stays unknown."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest

import pandas as pd

import preopen_check as pc

NOW = datetime(2026, 10, 13, 13, 0, tzinfo=timezone.utc)  # 09:00 New York
LAST = date(2026, 10, 12)


def row(ticker, direction="long", verdict="candidate", atr=2.0):
    return {"ticker": ticker, "trade_direction": direction, "signal_verdict": verdict, "atr_14_pct": atr,
            "playbook_name": "support_bounce_swing", "signal_score": 9.0, "execution_eligible": True}


def store(folder: Path, closes: dict[str, float], session: date = LAST) -> None:
    for ticker, close in closes.items():
        frame = pd.DataFrame({"Open": [close], "High": [close], "Low": [close], "Close": [close], "Volume": [1e6]},
                             index=pd.DatetimeIndex([pd.Timestamp(session)], name="Date"))
        frame.to_csv(folder / f"{ticker}.csv.gz", compression="gzip")


class JudgeTests(unittest.TestCase):
    def test_a_gap_against_the_trade_beyond_one_atr_demotes_and_a_small_one_does_not(self):
        reference = (100.0, LAST)
        self.assertEqual(pc.judge(row("A"), {"price": 97.5}, reference, [])["decision"], "demoted_to_watch")
        self.assertEqual(pc.judge(row("A"), {"price": 98.5}, reference, [])["decision"], "kept")
        self.assertEqual(pc.judge(row("A"), {"price": 104.0}, reference, [])["decision"], "kept")  # gap in favour
        self.assertEqual(pc.judge(row("A", "short"), {"price": 102.5}, reference, [])["decision"], "demoted_to_watch")

    def test_unknown_price_or_atr_keeps_the_nightly_decision_and_says_so(self):
        view = pc.judge(row("A"), None, (100.0, LAST), [])
        self.assertEqual((view["decision"], view["gap_pct"]), ("kept", None))
        self.assertIn("could not be judged", view["notes"][0])
        no_atr = pc.judge(row("A", atr=None), {"price": 90.0}, (100.0, LAST), [])
        self.assertEqual(no_atr["decision"], "kept")
        unread = pc.judge(row("A"), {"price": 100.0}, (100.0, LAST), None)
        self.assertIn("could not be read", unread["notes"][0])

    def test_a_new_content_filing_demotes(self):
        events = [{"sec_form": "8-K", "sec_item": "2.02"}]
        view = pc.judge(row("A"), {"price": 100.0}, (100.0, LAST), events)
        self.assertEqual(view["decision"], "demoted_to_watch")
        self.assertIn("8-K item 2.02", view["demote_reasons"][0])


class FilingTests(unittest.TestCase):
    def test_only_content_items_after_the_close_count(self):
        close = pc.close_moment(LAST)
        filings = [
            {"form": "8-K", "items": "2.02,9.01", "accepted": "2026-10-12T21:05:00.000Z", "filing_date": "2026-10-12"},
            {"form": "4", "items": "", "accepted": "2026-10-12T22:00:00.000Z", "filing_date": "2026-10-12"},
            {"form": "8-K", "items": "5.02", "accepted": "2026-10-12T15:00:00.000Z", "filing_date": "2026-10-12"},
        ]
        events = pc.content_events_since(filings, close)
        self.assertEqual([e["sec_item"] for e in events], ["2.02"])  # 9.01 is supporting, Form 4 no item, 5.02 before close

    def test_submissions_are_read_into_filings(self):
        submissions = {"cik": "320193", "filings": {"recent": {
            "form": ["8-K"], "filingDate": ["2026-10-12"], "acceptanceDateTime": ["2026-10-12T21:05:00.000Z"],
            "items": ["2.02"], "accessionNumber": ["0000320193-26-000001"], "primaryDocument": ["a.htm"]}}}
        filing = pc.recent_filings(submissions)[0]
        self.assertEqual((filing["form"], filing["items"]), ("8-K", "2.02"))
        self.assertTrue(filing["url"].endswith("/000032019326000001/a.htm"))


class QuoteSourceTests(unittest.TestCase):
    TODAY = date(2026, 10, 13)

    def test_only_a_trade_from_todays_pre_market_counts(self):
        today = self.TODAY
        pre = pc.premarket_trade({"latestTrade": {"p": 101.5, "t": "2026-10-13T13:12:01.123456789Z"}}, today)
        self.assertEqual((pre["price"], pre["source"]), (101.5, "alpaca_iex"))    # 09:12 New York
        self.assertIsNone(pc.premarket_trade({"latestTrade": {"p": 100.0, "t": "2026-10-12T19:59:00Z"}}, today))
        self.assertIsNone(pc.premarket_trade({"latestTrade": {"p": 100.0, "t": "2026-10-13T14:00:00Z"}}, today))
        self.assertIsNone(pc.premarket_trade({}, today))

    def test_a_bad_symbol_is_isolated_and_the_rest_of_the_batch_answered(self):
        calls = []

        def fetch(symbols):
            calls.append(list(symbols))
            if "BAD" in symbols:
                raise pc.BadSymbol("400")
            return {s: {"latestTrade": {"p": 10.0, "t": "2026-10-13T13:00:00Z"}} for s in symbols}

        out = pc.alpaca_snapshot_quotes(["AAA", "BAD", "CCC", "DDD"], fetch, today=self.TODAY)
        self.assertEqual(sorted(out), ["AAA", "CCC", "DDD"])
        self.assertEqual(calls[0], ["AAA", "BAD", "CCC", "DDD"])                 # one request for the batch first

    def test_later_sources_only_get_what_is_missing(self):
        seen = {}

        def first(tickers, out):
            seen["first"] = list(tickers)
            out["AAA"] = {"price": 1.0, "source": "a"}

        def second(tickers, out):
            seen["second"] = list(tickers)
            out["BBB"] = {"price": 2.0, "source": "b"}

        quotes = pc.layered_quotes([("a", first), ("b", second)])
        self.assertEqual(sorted(quotes(["AAA", "BBB", "CCC"])), ["AAA", "BBB"])
        self.assertEqual(seen, {"first": ["AAA", "BBB", "CCC"], "second": ["BBB", "CCC"]})


class RunTests(unittest.TestCase):
    def test_last_runs_unknowns_are_looked_at_first_and_new_ones_are_recorded(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            store(folder, {"AAA": 100.0, "BBB": 50.0, "CCC": 20.0})
            order = {}

            def quotes(tickers):
                order["quotes"] = list(tickers)
                return {"AAA": {"price": 100.0, "source": "alpaca_iex"}}

            def filings(cik):
                order.setdefault("filings", []).append(cik)
                return []

            result = pc.run({"yankos": {"run_id": "r1", "results": [row("AAA"), row("BBB"), row("CCC")]}},
                            quotes=quotes, filings=filings, ciks={"AAA": 1, "BBB": 2, "CCC": 3}, price_dir=folder,
                            now=NOW, priority={"quotes": ["CCC"], "filings": ["BBB"]})
            self.assertEqual(order["quotes"][0], "CCC")
            self.assertEqual(order["filings"][0], 2)
            self.assertEqual(result["report"]["pending_unknowns"], {"quotes": ["BBB", "CCC"], "filings": []})
            self.assertEqual(result["report"]["timings"]["quote_sources"], {"alpaca_iex": 1})

    def test_only_candidates_change_and_the_report_counts_them(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            store(folder, {"AAA": 100.0, "BBB": 50.0, "CCC": 20.0})
            payloads = {"yankos": {"run_id": "r1", "results": [row("AAA"), row("BBB"), row("CCC", verdict="watch")]},
                        "periklis": {"run_id": "r1", "results": [row("BBB", "short")]}}
            quotes = lambda tickers: {"AAA": {"price": 95.0}, "BBB": {"price": 50.1}}  # noqa: E731
            filings = lambda cik: [{"form": "8-K", "items": "1.01", "accepted": "2026-10-12T23:00:00Z"}] if cik == 2 else []  # noqa: E731
            result = pc.run(payloads, quotes=quotes, filings=filings, ciks={"AAA": 1, "BBB": 2}, price_dir=folder, now=NOW)
            yankos = {r["ticker"]: r for r in result["outputs"]["yankos"]["results"]}
            self.assertEqual(yankos["AAA"]["signal_verdict"], "watch")       # -5% gap, ATR 2%
            self.assertEqual(yankos["AAA"]["nightly_signal_verdict"], "candidate")
            self.assertEqual(yankos["BBB"]["signal_verdict"], "watch")       # new 8-K item 1.01
            self.assertNotIn("preopen_check", yankos["CCC"])                 # a watch row is left alone
            self.assertEqual(result["outputs"]["yankos"]["run_id"], "r1")    # same run: Koulis still matches it
            self.assertEqual(result["report"]["counts"]["demoted_to_watch"], 3)
            self.assertEqual(payloads["yankos"]["results"][0]["signal_verdict"], "candidate")  # input untouched

    def test_a_hung_quote_source_and_slow_filings_stay_unknown_within_the_budget(self):
        import time

        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            store(folder, {"AAA": 100.0, "BBB": 50.0})

            def hung(_tickers):
                time.sleep(30)
                return {"AAA": {"price": 90.0}}

            started = time.monotonic()
            result = pc.run({"yankos": {"run_id": "r1", "results": [row("AAA"), row("BBB")]}}, quotes=hung,
                            filings=lambda cik: [], ciks={"AAA": 1, "BBB": 2}, price_dir=folder, now=NOW,
                            quotes_budget=0.5, filings_budget=0.0)
            self.assertLess(time.monotonic() - started, 10)                 # did not wait for the hung source
            self.assertIn("TimeoutError", result["report"]["quote_error"])
            self.assertEqual(result["report"]["timings"]["filings_not_read_over_budget"], 2)
            checks = [r["preopen_check"] for r in result["outputs"]["yankos"]["results"]]
            self.assertEqual([c["decision"] for c in checks], ["kept", "kept"])  # unknown keeps the nightly call
            self.assertTrue(all(c["new_sec_events"] is None for c in checks))
        pc.ABANDONED_WORK = False

    def test_a_stale_close_is_no_reference_and_failed_quotes_are_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            store(folder, {"AAA": 100.0}, session=date(2026, 9, 1))

            def broken(_tickers):
                raise TimeoutError("no answer")
            result = pc.run({"yankos": {"run_id": "r1", "results": [row("AAA")]}}, quotes=broken, filings=None,
                            ciks={}, price_dir=folder, now=NOW)
            check = result["outputs"]["yankos"]["results"][0]["preopen_check"]
            self.assertEqual((check["decision"], check["last_close"]), ("kept", None))
            self.assertIn("TimeoutError", result["report"]["quote_error"])
            self.assertIn("Έλεγχος πριν το άνοιγμα", pc.render_md(result["report"]))


if __name__ == "__main__":
    unittest.main()
