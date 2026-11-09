"""Headlines for held positions and candidates: an unanswered feed is unknown (never "no news"), the second source is
dropped after a few failures, the time limit leaves the rest not_read, and the shadow measurement only compares."""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import news_watch as nw

FEED = b"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<description>Latest Financial News for NVDA</description>
<item><title>Nvidia stock sinks 5% as chipmaker investigates CEO Jensen Huang's relationship with alleged Chinese spy</title>
<guid>g1</guid><link>https://example.test/a</link><pubDate>Sun, 08 Nov 2026 20:00:00 +0000</pubDate></item>
<item><title>Nvidia to present at investor day</title><guid>g2</guid><pubDate>Mon, 09 Nov 2026 15:00:00 +0000</pubDate></item>
</channel></rss>"""
UNKNOWN_SYMBOL = b"""<rss version="2.0"><channel><description>Latest Financial News for </description></channel></rss>"""
EMPTY = b"""<rss version="2.0"><channel><description>Latest Financial News for ZVRA</description></channel></rss>"""


def metrics(rows):
    known = [r for r in rows if r.get("outcome_pct") is not None]
    days = len({r["session_date"] for r in known})
    mean = round(sum(r["outcome_pct"] for r in known) / len(known), 3) if known else None
    return {"known": len(known), "days": days, "mean_pct": mean, "mean_excess_pct": None, "beat_spy_pct": None,
            "status": "judged" if len(known) >= 2 and days >= 1 else "insufficient_sample"}


class FeedTests(unittest.TestCase):
    def test_feed_items_unknown_symbol_and_empty(self):
        out = nw.parse_feed(FEED, "NVDA")
        self.assertEqual((out["status"], len(out["items"])), ("found", 2))
        self.assertEqual(out["items"][0]["published_at"], "2026-11-08T20:00:00+00:00")
        self.assertEqual(nw.parse_feed(UNKNOWN_SYMBOL, "ZZZZ")["status"], "not_recognised")
        self.assertEqual(nw.parse_feed(EMPTY, "ZVRA")["status"], "none")

    def test_yfinance_new_and_old_shapes(self):
        items = nw.parse_yfinance([
            {"id": "a", "content": {"title": "T1", "pubDate": "2026-11-08T20:00:00Z", "provider": {"displayName": "P"},
                                    "canonicalUrl": {"url": "https://example.test/1"}}},
            {"uuid": "b", "title": "T2", "publisher": "Q", "link": "https://example.test/2", "providerPublishTime": 86400},
            {"content": {"title": ""}}])
        self.assertEqual([(i["title"], i["publisher"], i["published_at"]) for i in items],
                         [("T1", "P", "2026-11-08T20:00:00+00:00"), ("T2", "Q", "1970-01-02T00:00:00+00:00")])


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.taxonomy = {"categories": {
            "regulatory_legal": {"keywords": ["investigation"], "base_materiality": 4},
            "governance_management": {"keywords": ["chief executive officer"], "base_materiality": 3},
            "dilution_capital_structure": {"keywords": ["atm"], "base_materiality": 5},
            "investor_events": {"keywords": ["investor day"], "base_materiality": 2}}}
        self.terms = nw._terms(self.taxonomy)
        self.weights = {k: v["base_materiality"] for k, v in self.taxonomy["categories"].items()}

    def test_the_ceo_probe_is_material_and_words_match_whole(self):
        label = nw.classify("Chipmaker investigates CEO's relationship with alleged spy", None, self.terms, self.weights)
        self.assertEqual((label["category_id"], label["material"]), ("regulatory_legal", True))
        self.assertEqual(nw.classify("Batman returns", None, self.terms, self.weights)["category_id"], "unknown")
        label = nw.classify("Company to host investor day", None, self.terms, self.weights)
        self.assertEqual((label["category_id"], label["material"]), ("investor_events", False))


class ReadTests(unittest.TestCase):
    def test_a_feed_that_does_not_answer_is_unknown_never_none(self):
        def first(symbol):
            raise OSError("down")
        out = nw.read_all(["A"], first, nw.SecondSource(None), 30)
        self.assertEqual(out["A"]["status"], "no_answer")

    def test_the_second_source_answers_and_is_dropped_after_failures(self):
        calls = []

        def first(symbol):
            raise OSError("down")

        def second(symbol):
            calls.append(symbol)
            if symbol == "OK":
                return {"status": "found", "items": [{"guid": "x", "title": "t"}]}
            raise OSError("down too")
        out = nw.read_all(["OK"], first, nw.SecondSource(second), 30)
        self.assertEqual((out["OK"]["status"], out["OK"]["source"]), ("found", "yfinance"))
        source = nw.SecondSource(second)
        nw.read_all([f"S{i}" for i in range(8)], first, source, 30, workers=1)
        self.assertEqual(len(calls) - 1, nw.SECOND_SOURCE_FAILURES_MAX)

    def test_out_of_time_is_not_read(self):
        ticks = iter([0.0] + [100.0] * 50)
        out = nw.read_all(["A", "B"], lambda s: {"status": "none", "items": []}, nw.SecondSource(None), 10,
                          clock=lambda: next(ticks))
        self.assertEqual({out["A"]["status"], out["B"]["status"]}, {"not_read"})


class NamesTests(unittest.TestCase):
    def test_a_headline_counts_only_when_it_names_the_company(self):
        self.assertEqual(nw.name_words("NVIDIA Corporation"), "nvidia")
        self.assertEqual(nw.name_words("Advanced Micro Devices, Inc."), "advanced micro")
        self.assertIsNone(nw.name_words(None))
        self.assertTrue(nw.names_company("Nvidia sinks on spy probe", "NVDA", "nvidia"))
        self.assertTrue(nw.names_company("Analyst lifts AMD to Outperform", "AMD", "advanced micro"))
        self.assertFalse(nw.names_company("Nvidia falls as board probes CEO", "AMD", "advanced micro"))
        self.assertFalse(nw.names_company("Stocks rise ON tech", "ON", None))      # a ticker that is a word


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        taxonomy = {"categories": {"regulatory_legal": {"keywords": [], "base_materiality": 4}}}
        nw.store(self.conn, {"NVDA": {**nw.parse_feed(FEED, "NVDA"), "source": "yahoo_rss"},
                             "AMD": {"status": "none", "items": [], "source": "yahoo_rss"},
                             "TSM": {"status": "no_answer", "items": [], "source": None}},
                 "preopen", "2026-11-09T14:00:00+00:00", taxonomy, companies={"NVDA": "NVIDIA Corporation"})
        # the same probe in AMD's feed does not name AMD: kept, not material for AMD
        nw.store(self.conn, {"AMD": {**nw.parse_feed(FEED.replace(b"NVDA<", b"AMD<"), "AMD"), "source": "yahoo_rss"}},
                 "preopen", "2026-11-09T14:00:01+00:00", taxonomy, companies={"AMD": "Advanced Micro Devices, Inc."})

    def test_headlines_are_kept_once_and_the_flag_reads_the_window(self):
        self.assertEqual(nw.store(self.conn, {"NVDA": {**nw.parse_feed(FEED, "NVDA"), "source": "yahoo_rss"}},
                                  "manual", "2026-11-09T15:00:00+00:00",
                                  {"categories": {"regulatory_legal": {"keywords": [], "base_materiality": 4}}}), 0)
        nvda = nw.news_flag(self.conn, "NVDA", date(2026, 11, 9))
        self.assertEqual((nvda["known"], nvda["flagged"], nvda["categories"]), (True, True, ["regulatory_legal"]))
        self.assertEqual(nw.news_flag(self.conn, "AMD", date(2026, 11, 9))["flagged"], False)
        self.assertEqual(self.conn.execute("SELECT COUNT(*), SUM(material) FROM news_headlines WHERE symbol='AMD'"
                                           ).fetchone(), (2, 0))
        self.assertFalse(nw.news_flag(self.conn, "TSM", date(2026, 11, 9))["known"])        # no answer: unknown
        self.assertFalse(nw.news_flag(self.conn, "NVDA", date(2026, 11, 12))["known"])      # no read that day

    def test_evaluate_compares_and_proposes_only_a_clear_gap(self):
        records = [{"kind": "trade", "session_date": "2026-11-09", "ticker": "NVDA", "outcome_pct": -3.0},
                   {"kind": "shadow_candidate", "session_date": "2026-11-09", "ticker": "NVDA", "outcome_pct": -1.0},
                   {"kind": "trade", "session_date": "2026-11-09", "ticker": "AMD", "outcome_pct": 1.0},
                   {"kind": "trade", "session_date": "2026-11-09", "ticker": "AMD", "outcome_pct": 0.0},
                   {"kind": "trade", "session_date": "2026-11-09", "ticker": "TSM", "outcome_pct": 9.0},
                   {"kind": "koulis_left_out", "session_date": "2026-11-09", "ticker": "NVDA", "outcome_pct": 9.0}]
        out = nw.evaluate(self.conn, records, metrics, 0.5)
        self.assertEqual((out["flagged"]["known"], out["clear"]["known"], out["unknown"]), (2, 2, 1))
        self.assertEqual((out["flagged_minus_clear_pct"], out["verdict"]), (-2.5, "news-flagged did worse"))
        self.assertEqual(nw.proposal(out)[0]["target_agent"], "preopen_check")
        self.assertFalse(out["active_decisions_changed"])
        self.assertEqual(nw.proposal({**out, "verdict": "insufficient_sample"}), [])


class RunTests(unittest.TestCase):
    def test_held_first_then_candidates_and_the_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "data").mkdir()
            (root / "output").mkdir()
            (root / "data" / "paper_positions.json").write_text(json.dumps({"NVDA": {}}), encoding="utf-8")
            (root / "output" / "yankos_strategy_latest.json").write_text(json.dumps({"results": [
                {"ticker": "AMD", "post_gate_verdict": "candidate"}, {"ticker": "MU", "post_gate_verdict": "watch"},
                {"ticker": "NVDA", "post_gate_verdict": "candidate", "company": "NVIDIA Corporation"}]}),
                encoding="utf-8")
            roles = nw.watched_symbols(root)
            self.assertEqual(list(roles), ["NVDA", "AMD"])
            self.assertEqual(roles["NVDA"], ["held", "candidate:yankos"])
            answers = {"NVDA": FEED, "AMD": EMPTY.replace(b"ZVRA", b"AMD")}
            summary = nw.run(root, "manual", first=lambda s: nw.parse_feed(answers[s], s), second=None,
                             now=datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc))
            self.assertEqual((summary["statuses"]["found"], summary["statuses"]["none"]), (1, 1))
            self.assertEqual([m["symbol"] for m in summary["material"]], ["NVDA"])
            text = (root / "output" / "news_watch_latest.md").read_text(encoding="utf-8")
            self.assertIn("held, candidate:yankos", text)
            self.assertIn("regulatory_legal", text)


if __name__ == "__main__":
    unittest.main()
