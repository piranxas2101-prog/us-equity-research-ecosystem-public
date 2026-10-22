import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from research_registry import full_universe
from research_registry import instrument_links as links
from research_registry.core import taxonomy


def listing(ticker, name, kind, venue="Q", aliases=None):
    return {"venue_code": venue, "ticker": ticker, "name": name, "instrument_type": kind,
            "directory_aliases": aliases or [ticker]}


SEC = {"fields": ["cik", "name", "ticker", "exchange"], "data": [
    [1111, "Arch", "ACGL", "Nasdaq"], [1111, "Arch", "ACGLN", "Nasdaq"],
    [2222, "First Citizens", "FCNCA", "Nasdaq"], [2222, "First Citizens", "FCNCB", "Nasdaq"],
    [2222, "First Citizens", "FCNCO", "Nasdaq"],
    [3333, "Brookfield Property", "BPYPM", "Nasdaq"],
    [4444, "Other Co", "XYZ", "Nasdaq"],
]}


class LinkRuleTests(unittest.TestCase):
    def link(self, rows):
        exact, canonical = links.sec_identities(SEC)
        return {x["ticker"]: x for x in links.link_instruments(rows, exact, canonical)}

    def test_ticker_roots_cover_nasdaq_suffix_and_nyse_separators(self):
        self.assertEqual(links.ticker_roots("ACGLN"), ["ACGL"])
        self.assertEqual(links.ticker_roots("AAC.W"), ["AAC"])
        self.assertEqual(links.ticker_roots("BML$G"), ["BML"])
        self.assertEqual(links.ticker_roots("ABC"), [])

    def test_issuer_key_ignores_legal_form_and_instrument_wording(self):
        self.assertEqual(links.issuer_key("Arch Capital Group Ltd. - Depositary Shares"),
                         links.issuer_key("Arch Capital Group Ltd - Common Stock"))
        self.assertEqual(links.issuer_key("Ares Acquisition Corporation III Units, each consisting of one share"),
                         "ares acquisition iii")
        # A company named "Preferred Bank" keeps its name when the instrument is after " - ".
        self.assertEqual(links.issuer_key("Preferred Bank - Common Stock"), "preferred bank")

    def test_same_sec_cik_links_instrument_to_listed_common_stock(self):
        out = self.link([listing("ACGL", "Arch Capital Group Ltd. - Common Stock", "equity_candidate"),
                         listing("ACGLN", "Arch Capital Group Ltd. - Depositary Shares", "preferred")])
        self.assertEqual((out["ACGLN"]["status"], out["ACGLN"]["parent_ticker"], out["ACGLN"]["method"]),
                         ("linked", "ACGL", "sec_cik"))
        self.assertEqual(out["ACGLN"]["parent_cik"], "1111")
        self.assertNotIn("ACGL", out)  # parents are not instruments

    def test_several_share_classes_keep_alternatives(self):
        out = self.link([listing("FCNCA", "First Citizens BancShares, Inc. - Class A Common Stock", "equity_candidate"),
                         listing("FCNCB", "First Citizens BancShares, Inc. - Class B Common Stock", "equity_candidate"),
                         listing("FCNCO", "First Citizens BancShares, Inc. - 5.625% Preferred", "preferred")])
        self.assertEqual(out["FCNCO"]["parent_ticker"], "FCNCA")
        self.assertEqual(out["FCNCO"]["evidence"]["alternatives"], ["FCNCB"])

    def test_ticker_root_picks_the_class_when_it_matches(self):
        out = self.link([listing("FCNCA", "First Citizens - Class A", "equity_candidate"),
                         listing("FCNCB", "First Citizens - Class B", "equity_candidate"),
                         listing("FCNCBW", "First Citizens - Warrant", "warrant",
                                 aliases=["FCNCBW"])])
        # FCNCBW has no SEC identity here, so the ticker root + issuer name decide.
        self.assertEqual((out["FCNCBW"]["parent_ticker"], out["FCNCBW"]["method"]), ("FCNCB", "ticker_and_issuer_name"))

    def test_ticker_and_issuer_name_link_without_sec_identity(self):
        out = self.link([listing("AAC", "Ares Acquisition Corporation III Class A Ordinary Shares", "spac", "N"),
                         listing("AAC.W", "Ares Acquisition Corporation III Redeemable warrants, each whole", "warrant", "N")])
        self.assertEqual((out["AAC.W"]["parent_ticker"], out["AAC.W"]["method"]), ("AAC", "ticker_and_issuer_name"))

    def test_unknown_parent_stays_unresolved_with_reason(self):
        out = self.link([
            listing("BPYPM", "Brookfield Property Partners L.P. - 6.25% Preferred", "preferred"),
            listing("ZZZW", "Nobody Corp - Warrant", "warrant"),
            listing("XYZ", "Other Co - Common Stock", "equity_candidate"),
            listing("XYZW", "Different Issuer Inc - Warrant", "warrant"),
        ])
        self.assertEqual(out["BPYPM"]["reason"], "no_listed_common_stock_with_same_cik")
        self.assertEqual(out["ZZZW"]["reason"], "no_sec_identity_and_no_matching_listed_parent")
        # Ticker root matches but the issuer name does not: no guess.
        self.assertEqual(out["XYZW"]["status"], "unresolved")
        for row in out.values():
            if row["status"] == "unresolved":
                self.assertIsNone(row["parent_ticker"])


class RegistryBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.addCleanup(self.db.close)
        full_universe.schema(self.db)
        data, _ = taxonomy()
        self.db.execute("INSERT INTO universe_snapshot VALUES('snap','2026-09-28T00:00:00Z','{}','{}')")
        for ticker, name, kind in [("ACGL", "Arch Capital Group Ltd. - Common Stock", "equity_candidate"),
                                   ("ACGLN", "Arch Capital Group Ltd. - Depositary Shares", "preferred"),
                                   ("ZZZW", "Nobody Corp - Warrant", "warrant")]:
            payload = {"ticker": ticker, "venue_code": "Q", "name": name, "directory_aliases": [ticker]}
            self.db.execute("INSERT INTO listed_symbol VALUES('snap','Q',?,?,?,?,?,NULL,'instrument_category_only',?)",
                            (ticker, name, kind, data["scheme"], data["version"], json.dumps(payload)))
        self.sec = Path(self.tmp.name) / "sec.json"
        self.sec.write_text(json.dumps(SEC), encoding="utf-8")

    def test_build_stores_links_and_is_repeatable(self):
        first = links.build(self.db, self.sec)
        second = links.build(self.db, self.sec)
        self.assertEqual((first["instruments"], first["linked"]), (2, 1))
        self.assertEqual(first, second)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM instrument_parent_link").fetchone()[0], 2)
        parent = links.links_for(self.db, "acgl")
        self.assertIsNone(parent["link"])
        self.assertEqual([(c["ticker"], c["instrument_type"]) for c in parent["children"]], [("ACGLN", "preferred")])
        self.assertEqual(parent["children"][0]["name"], "Arch Capital Group Ltd. - Depositary Shares")
        child = links.links_for(self.db, "ACGLN")
        self.assertEqual((child["link"]["parent_ticker"], child["link"]["method"]), ("ACGL", "sec_cik"))
        self.assertEqual(len(child["link"]["evidence"]["sec_tickers_sha256"]), 64)
        orphan = links.links_for(self.db, "ZZZW")["link"]
        self.assertEqual((orphan["status"], orphan["parent_ticker"]), ("unresolved", None))

    def test_control_center_shows_links_read_only(self):
        import control_center as cc

        links.build(self.db, self.sec)
        root = Path(self.tmp.name) / "root"
        (root / "data").mkdir(parents=True)
        target = sqlite3.connect(root / "data" / "research_registry.db")
        self.db.backup(target)
        target.close()
        views = cc.Views(root)
        self.assertEqual([c["ticker"] for c in views._instrument_links("ACGL")["children"]], ["ACGLN"])
        self.assertEqual(views._instrument_links("ACGLN")["link"]["parent_ticker"], "ACGL")
        self.assertIsNone(cc.Views(Path(self.tmp.name) / "missing")._instrument_links("ACGL"))

    def test_linked_row_without_parent_is_rejected_by_schema(self):
        links.schema(self.db)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO instrument_parent_link VALUES('snap','Q','X','warrant','linked',NULL,NULL,NULL,"
                            "'sec_cik',NULL,'{}','v','t')")


if __name__ == "__main__":
    unittest.main()
