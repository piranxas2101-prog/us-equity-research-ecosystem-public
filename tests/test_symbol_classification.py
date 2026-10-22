from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import symbol_classification
import us_equity_monitor


class SymbolClassificationTests(unittest.TestCase):
    """Provider-label behaviour, isolated from the developer's local Babis registry."""

    def setUp(self) -> None:
        patcher = mock.patch.dict("os.environ", {"ECOSYSTEM_CLASSIFICATION_SOURCE": "provider"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_provider_labels_are_normalized_without_scoring(self) -> None:
        row = symbol_classification.classify_symbol(
            {"ticker": "ABC", "sector": "Consumer Cyclical", "industry": "Specialty Retail"}
        )
        self.assertEqual(row["sector_id"], "consumer_discretionary")
        self.assertEqual(row["industry_id"], "consumer_discretionary.specialty_retail")
        self.assertEqual(row["classification_status"], "classified")
        self.assertNotIn("score", row)

    def test_missing_provider_classification_stays_unknown(self) -> None:
        row = symbol_classification.classify_symbol({"ticker": "XYZ"})
        self.assertEqual(row["sector_id"], "unknown")
        self.assertFalse(row["index_analysis_eligible"])

    def test_asset_type_is_separate_from_sector_classification(self) -> None:
        row = symbol_classification.classify_security_asset(
            "ADACW", "American Drive Acquisition Corp"
        )
        self.assertEqual(row["asset_type"], "warrant")
        self.assertFalse(row["common_equity_eligible"])

    def test_spac_common_and_unit_are_excluded_from_common_equity(self) -> None:
        common = symbol_classification.classify_security_asset(
            "ADAC", "American Drive Acquisition Corp"
        )
        unit = symbol_classification.classify_security_asset(
            "ADACU", "American Drive Acquisition Corp"
        )
        self.assertEqual(common["asset_type"], "spac_common")
        self.assertEqual(unit["asset_type"], "unit")
        self.assertFalse(common["common_equity_eligible"])
        self.assertFalse(unit["common_equity_eligible"])

    def test_spac_name_with_series_between_acquisition_and_company_is_excluded(self) -> None:
        row = symbol_classification.classify_security_asset(
            "CHEC", "Chenghe Acquisition III Co."
        )
        self.assertEqual(row["asset_type"], "spac_common")
        self.assertFalse(row["common_equity_eligible"])

    def test_dash_ri_symbol_is_classified_as_a_right(self) -> None:
        row = symbol_classification.classify_security_asset("CELG-RI", "Bristol Myers Squibb Co")
        self.assertEqual(row["asset_type"], "right")
        self.assertFalse(row["common_equity_eligible"])

    def test_warrant_tranche_suffix_is_excluded_without_security_name_hint(self) -> None:
        row = symbol_classification.classify_security_asset("NE-WTA", "Noble Corp plc")
        self.assertEqual(row["asset_type"], "warrant")
        self.assertFalse(row["common_equity_eligible"])

    def test_ambiguous_r_suffix_requires_validation_without_auto_exclusion(self) -> None:
        row = symbol_classification.classify_security_asset("ALMR", "Alamar Biosciences, Inc.")
        self.assertEqual(row["asset_type"], "ambiguous_right_suffix")
        self.assertTrue(row["common_equity_eligible"])
        self.assertTrue(row["requires_asset_master_validation"])

    def test_plain_company_tickers_ending_w_or_u_are_not_auto_excluded(self) -> None:
        for ticker in ("PANW", "SNOW", "MU"):
            with self.subTest(ticker=ticker):
                row = symbol_classification.classify_security_asset(ticker, "Example Operating Company Inc")
                self.assertTrue(row["common_equity_eligible"])
                self.assertTrue(row["requires_asset_master_validation"])

    def test_common_company_names_do_not_match_embedded_security_words(self) -> None:
        for ticker, name in (("UNH", "UnitedHealth Group Inc"),
                             ("HWM", "Howmet Aerospace Inc"),
                             ("CW", "Curtiss Wright Corp"),
                             ("PFBC", "Preferred Bank")):
            with self.subTest(ticker=ticker):
                self.assertFalse(us_equity_monitor.security_name_excluded(name, {}))
                self.assertTrue(symbol_classification.classify_security_asset(ticker, name)["common_equity_eligible"])

    def test_primary_config_has_full_company_universe_without_price_cap(self) -> None:
        config = json.loads((Path(__file__).resolve().parents[1] / "scanner_config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["universe"]["max_symbols"], 0)
        self.assertEqual(config["universe"]["mode"], "registry_all_listed")
        self.assertIsNone(config["scan"]["max_price"])
        self.assertTrue(config["pipeline"]["full_universe"])

    def test_sec_universe_excludes_special_assets_but_keeps_ambiguous_r_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "sec.json"
            cache.write_text(
                json.dumps(
                    {
                        "fields": ["ticker", "name", "exchange"],
                        "data": [
                            ["ABC", "Example Common Inc.", "Nasdaq"],
                            ["ADACW", "American Drive Acquisition Corp", "Nasdaq"],
                            ["BC-PC", "Brunswick Corp", "NYSE"],
                            ["ALMR", "Alamar Biosciences, Inc.", "Nasdaq"],
                            ["UNH", "UnitedHealth Group Inc", "NYSE"],
                            ["HWM", "Howmet Aerospace Inc", "NYSE"],
                            ["PFBC", "Preferred Bank", "Nasdaq"],
                            ["PANW", "Palo Alto Networks Inc", "Nasdaq"],
                        ],
                    }
                ),
                encoding="utf-8",
            )
            symbols = us_equity_monitor.load_us_universe_from_sec(
                {
                    "cache_file": str(cache),
                    "cache_max_age_hours": 24,
                    "max_symbols": 0,
                    "include_exchanges": ["Nasdaq", "NYSE"],
                }
            )

        self.assertEqual(symbols, ["ABC", "ALMR", "HWM", "PANW", "PFBC", "UNH"])

    def test_explicit_full_universe_overrides_daily_cap(self) -> None:
        parser = us_equity_monitor.make_parser()
        args = parser.parse_args(["pipeline", "--full-universe"])
        self.assertTrue(us_equity_monitor.pipeline_uses_full_universe(args, {"full_universe": False}))
        capped_args = parser.parse_args(["pipeline", "--respect-universe-cap"])
        self.assertFalse(us_equity_monitor.pipeline_uses_full_universe(capped_args, {"full_universe": True}))

    def test_local_asset_master_and_dated_halt_exclusions_are_audited(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache, registry, overrides, receipt = (
                root / "sec.json", root / "registry.db", root / "overrides.json", root / "receipt.json"
            )
            cache.write_text(json.dumps({
                "fields": ["ticker", "name", "exchange"],
                "data": [
                    ["ABC", "Example Inc", "Nasdaq"],
                    ["FBYDP", "Falcon's Beyond Global Inc", "Nasdaq"],
                    ["NE-WTA", "Noble Corp plc", "NYSE"],
                    ["SVA", "Sinovac Biotech Ltd", "Nasdaq"],
                ],
            }), encoding="utf-8")
            db = sqlite3.connect(registry)
            try:
                db.execute("CREATE TABLE universe_snapshot(snapshot_id TEXT,retrieved_at TEXT)")
                db.execute("CREATE TABLE effective_listed_symbol(snapshot_id TEXT,ticker TEXT,venue_code TEXT,instrument_type TEXT)")
                db.execute("INSERT INTO universe_snapshot VALUES('dated','2026-09-20')")
                db.execute("INSERT INTO effective_listed_symbol VALUES('dated','FBYDP','Q','preferred')")
                db.commit()
            finally:
                db.close()
            overrides.write_text(json.dumps({
                "schema": "ecosystem.universe_eligibility_overrides.v1",
                "exclusions": [{"ticker": "SVA", "reason_code": "trading_halt",
                                "reviewed_at": "2026-01-01", "valid_until": "2099-01-01",
                                "source_url": "https://www.sec.gov/example"}],
            }), encoding="utf-8")
            symbols = us_equity_monitor.load_us_universe_from_sec({
                "cache_file": str(cache), "cache_max_age_hours": 24,
                "registry_db_path": str(registry),
                "eligibility_overrides_path": str(overrides),
                "eligibility_receipt_path": str(receipt),
                "max_symbols": 0,
            })
            self.assertEqual(symbols, ["ABC"])
            audited = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual({row["ticker"] for row in audited["special_exclusions"]}, {"FBYDP", "SVA"})
            self.assertEqual(audited["eligible_symbols"], ["ABC"])

    def test_registry_all_listed_routes_every_instrument(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry, receipt = root / "registry.db", root / "eligibility.json"
            db = sqlite3.connect(registry)
            try:
                db.execute("CREATE TABLE universe_snapshot(snapshot_id TEXT,retrieved_at TEXT)")
                db.execute(
                    "CREATE TABLE effective_listed_symbol("
                    "snapshot_id TEXT,ticker TEXT,venue_code TEXT,name TEXT,"
                    "instrument_type TEXT,node_id TEXT,payload_json TEXT)"
                )
                db.execute("INSERT INTO universe_snapshot VALUES('dated','2026-09-21')")
                db.executemany(
                    "INSERT INTO effective_listed_symbol VALUES(?,?,?,?,?,?,?)",
                    [
                        ("dated", "ABC", "Q", "Example Common", "equity_candidate", "technology", "{}"),
                        ("dated", "FUND", "P", "Example ETF", "etf", None, "{}"),
                        ("dated", "ABCW", "Q", "Example Warrant", "warrant", None, "{}"),
                        ("dated", "BRK.A", "N", "Berkshire Class A", "equity_candidate", "financials", "{}"),
                    ],
                )
                db.commit()
            finally:
                db.close()
            receipt.write_text(json.dumps({
                "local_snapshot_id": "dated", "eligible_count": 2,
                "eligible_symbols": ["ABC", "BRK-A"]
            }), encoding="utf-8")
            cfg = {"registry_db_path": str(registry), "eligibility_receipt_path": str(receipt),
                   "max_symbols": 0}
            routes, snapshot_id = us_equity_monitor.load_all_listed_routes(cfg)
            symbols = us_equity_monitor.load_all_listed_universe(cfg)

        self.assertEqual(snapshot_id, "dated")
        self.assertEqual(symbols, ["ABC", "ABCW", "BRK.A", "FUND"])
        self.assertEqual(routes["ABC"]["research_route"], "company_fundamental_research")
        self.assertEqual(routes["FUND"]["research_route"], "fund_or_etp_observation")
        self.assertEqual(routes["ABCW"]["research_route"], "listed_derivative_observation")
        self.assertTrue(routes["BRK.A"]["company_fundamental_eligible"])
        self.assertEqual(routes["BRK.A"]["sec_eligibility_ticker"], "BRK-A")

    def test_issuer_linked_instrument_is_deferred_only_when_switched_on(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry, receipt = root / "registry.db", root / "eligibility.json"
            db = sqlite3.connect(registry)
            try:
                db.execute("CREATE TABLE universe_snapshot(snapshot_id TEXT,retrieved_at TEXT)")
                db.execute("CREATE TABLE effective_listed_symbol(snapshot_id TEXT,ticker TEXT,venue_code TEXT,name TEXT,"
                           "instrument_type TEXT,node_id TEXT,payload_json TEXT)")
                db.execute("CREATE TABLE instrument_parent_link(snapshot_id TEXT,ticker TEXT,status TEXT,"
                           "parent_ticker TEXT,method TEXT)")
                db.execute("INSERT INTO universe_snapshot VALUES('dated','2026-09-21')")
                db.executemany("INSERT INTO effective_listed_symbol VALUES(?,?,?,?,?,?,?)", [
                    ("dated", "ABC", "Q", "Example Common", "equity_candidate", "technology", "{}"),
                    ("dated", "ABCW", "Q", "Example Warrant", "warrant", None, "{}"),
                    ("dated", "ZZZW", "Q", "Orphan Warrant", "warrant", None, "{}"),
                ])
                db.executemany("INSERT INTO instrument_parent_link VALUES(?,?,?,?,?)", [
                    ("dated", "ABCW", "linked", "ABC", "sec_cik"),
                    ("dated", "ZZZW", "unresolved", None, None),
                    ("older", "ZZZW", "linked", "ABC", "sec_cik"),  # another snapshot never applies
                ])
                db.commit()
            finally:
                db.close()
            receipt.write_text(json.dumps({"local_snapshot_id": "dated", "eligible_symbols": ["ABC"]}), encoding="utf-8")
            cfg = {"registry_db_path": str(registry), "eligibility_receipt_path": str(receipt), "max_symbols": 0}
            off, _ = us_equity_monitor.load_all_listed_routes(cfg)
            on, _ = us_equity_monitor.load_all_listed_routes({**cfg, "defer_issuer_linked_instruments": True})

        self.assertEqual((off["ABCW"]["issuer_ticker"], off["ABCW"]["defer_market_scan"]), ("ABC", False))
        self.assertEqual((on["ABCW"]["issuer_ticker"], on["ABCW"]["defer_market_scan"]), ("ABC", True))
        self.assertEqual((on["ZZZW"]["issuer_ticker"], on["ZZZW"]["defer_market_scan"]), (None, False))
        self.assertFalse(on["ABC"]["defer_market_scan"])

    def test_etf_and_spac_routes_move_to_separate_lanes_but_companies_never_do(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry, receipt = root / "registry.db", root / "eligibility.json"
            db = sqlite3.connect(registry)
            try:
                db.execute("CREATE TABLE universe_snapshot(snapshot_id TEXT,retrieved_at TEXT)")
                db.execute("CREATE TABLE effective_listed_symbol(snapshot_id TEXT,ticker TEXT,venue_code TEXT,name TEXT,"
                           "instrument_type TEXT,node_id TEXT,payload_json TEXT)")
                db.execute("INSERT INTO universe_snapshot VALUES('dated','2026-09-21')")
                db.executemany("INSERT INTO effective_listed_symbol VALUES(?,?,?,?,?,?,?)", [
                    ("dated", "ABC", "Q", "Example Common", "equity_candidate", "technology", "{}"),
                    ("dated", "FUND", "P", "Example ETF", "etf", None, "{}"),
                    ("dated", "SHEL", "Q", "Shell Acquisition Corp", "spac", None, "{}"),
                    # Merged SPAC: still typed "spac" by the provider, but an SEC-eligible company.
                    ("dated", "DONE", "Q", "Done Holdings", "spac", None, "{}"),
                ])
                db.commit()
            finally:
                db.close()
            receipt.write_text(json.dumps({"local_snapshot_id": "dated", "eligible_symbols": ["ABC", "DONE"]}),
                               encoding="utf-8")
            cfg = {"registry_db_path": str(registry), "eligibility_receipt_path": str(receipt), "max_symbols": 0,
                   "separate_lane_routes": {"fund_or_etp_observation": "etf_lane",
                                            "acquisition_vehicle_observation": "spac_lane"}}
            routes, _ = us_equity_monitor.load_all_listed_routes(cfg)

        self.assertEqual((routes["FUND"]["separate_lane"], routes["FUND"]["defer_market_scan"]), ("etf_lane", True))
        self.assertEqual((routes["SHEL"]["separate_lane"], routes["SHEL"]["defer_market_scan"]), ("spac_lane", True))
        self.assertEqual(routes["DONE"]["research_route"], "company_fundamental_research")
        self.assertFalse(routes["DONE"]["defer_market_scan"])
        self.assertFalse(routes["ABC"]["defer_market_scan"])
        observation = us_equity_monitor.deferred_instrument_observation("FUND", routes["FUND"])
        self.assertEqual((observation["workflow_status"], observation["close"]), ("deferred_to_etf_lane", None))

    def test_stage1_checkpoint_signature_changes_when_a_symbol_is_deferred(self) -> None:
        base = {"instrument_type": "etf", "research_route": "fund_or_etp_observation",
                "company_fundamental_eligible": False}

        def signature(route):
            return us_equity_monitor.stage1_signature(
                symbols=["FUND"], lookback_days=30, min_history_days=1, max_price=None,
                min_composite_score=None, min_fundamental_coverage=None, snapshot_profile="light",
                data_sources_cfg={}, scoring_mode="fundamental_only", symbol_routes={"FUND": route})

        plain = signature(base)
        self.assertEqual(plain, signature({**base, "defer_market_scan": False, "separate_lane": None}))
        self.assertNotEqual(plain, signature({**base, "defer_market_scan": True, "separate_lane": "etf_lane"}))

    def test_deferred_instrument_is_recorded_without_a_market_fetch(self) -> None:
        fetched: list[str] = []

        def snapshot(symbol, *_args, listing_context=None, **_kwargs):
            fetched.append(symbol)
            return {
                "symbol": symbol, "company_name": symbol, "sector": None, "industry": None,
                "currency": "USD", "listing_context": listing_context or {},
                "price_metrics": {"close": 10.0}, "fundamentals": {}, "news": [], "sec_recent_filings": [],
                "openbb_research": {}, "source_base": {"market_data_provider": "test", "timing_seconds": {}},
            }

        routes = {
            "ABCW": {"ticker": "ABCW", "instrument_type": "warrant", "research_route": "listed_derivative_observation",
                     "company_fundamental_eligible": False, "issuer_ticker": "ABC", "issuer_link_method": "sec_cik",
                     "defer_market_scan": True},
            "FUND": {"instrument_type": "etf", "research_route": "fund_or_etp_observation",
                     "company_fundamental_eligible": False},
        }
        with mock.patch.object(us_equity_monitor, "fetch_snapshot", side_effect=snapshot):
            result = us_equity_monitor.scan_market_core(
                symbols=["ABCW", "FUND"], lookback_days=30, min_history_days=1,
                max_price=None, min_composite_score=None, min_fundamental_coverage=None,
                ma_rules=[], fundamental_rules=[], ma_weight=0.0, fundamental_weight=1.0,
                data_sources_cfg={}, symbol_routes=routes,
            )

        self.assertEqual(fetched, ["FUND"])
        deferred = next(o for o in result["instrument_observations"] if o["ticker"] == "ABCW")
        self.assertEqual((deferred["workflow_status"], deferred["issuer_ticker"]), ("deferred_to_issuer_card", "ABC"))
        self.assertIsNone(deferred["close"])  # unknown, not zero
        self.assertEqual(result["runtime_summary"]["processed_symbols"], 2)
        self.assertEqual(result["universe_routing"]["instrument_observations_deferred_to_issuer"], 1)
        self.assertEqual(result["universe_routing"]["instrument_observations_deferred_to_lane"], {})

    def test_non_company_instrument_is_observed_but_not_fundamental_candidate(self) -> None:
        def snapshot(symbol, *_args, listing_context=None, **_kwargs):
            return {
                "symbol": symbol, "company_name": symbol, "sector": None, "industry": None,
                "currency": "USD", "listing_context": listing_context or {},
                "price_metrics": {"close": 10.0, "close_above_sma_50": 1.0},
                "fundamentals": {"roe": 0.2}, "news": [], "sec_recent_filings": [],
                "openbb_research": {}, "source_base": {"market_data_provider": "test", "timing_seconds": {}},
            }

        routes = {
            "ABC": {"instrument_type": "equity_candidate", "research_route": "company_fundamental_research",
                    "company_fundamental_eligible": True},
            "FUND": {"instrument_type": "etf", "research_route": "fund_or_etp_observation",
                     "company_fundamental_eligible": False},
        }
        with mock.patch.object(us_equity_monitor, "fetch_snapshot", side_effect=snapshot):
            result = us_equity_monitor.scan_market_core(
                symbols=["ABC", "FUND"], lookback_days=30, min_history_days=1,
                max_price=None, min_composite_score=None, min_fundamental_coverage=50.0,
                ma_rules=[{"metric": "close_above_sma_50", "operator": ">=", "value": 1, "weight": 1}],
                fundamental_rules=[{"metric": "roe", "operator": ">=", "value": 0.1, "weight": 1}],
                ma_weight=0.0, fundamental_weight=1.0, data_sources_cfg={}, symbol_routes=routes,
            )

        self.assertEqual([row["symbol"] for row in result["results"]], ["ABC"])
        self.assertEqual([row["ticker"] for row in result["instrument_observations"]], ["FUND"])
        self.assertEqual(result["universe_routing"]["assigned_symbols"], 2)


class RegistryClassificationTests(unittest.TestCase):
    """The Babis registry is the classification authority for analysis."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "registry.db"
        conn = sqlite3.connect(self.db)
        conn.executescript("""
            CREATE TABLE universe_snapshot(snapshot_id TEXT, retrieved_at TEXT, provenance_json TEXT, summary_json TEXT);
            CREATE TABLE listed_symbol(snapshot_id TEXT, venue_code TEXT, ticker TEXT, name TEXT, instrument_type TEXT,
                                       scheme TEXT, version TEXT, node_id TEXT, status TEXT, payload_json TEXT);
            CREATE TABLE listing_classification_override(snapshot_id TEXT, venue_code TEXT, ticker TEXT, instrument_type TEXT,
                                                         node_id TEXT, status TEXT, batch_id TEXT, updated_at TEXT);
            CREATE VIEW effective_listed_symbol AS
              SELECT l.snapshot_id, l.venue_code, l.ticker, l.name,
                     COALESCE(o.instrument_type, l.instrument_type) AS instrument_type, l.scheme, l.version,
                     CASE WHEN o.ticker IS NULL THEN l.node_id ELSE o.node_id END AS node_id,
                     CASE WHEN o.ticker IS NULL THEN l.status ELSE o.status END AS status, l.payload_json
              FROM listed_symbol l LEFT JOIN listing_classification_override o
                ON o.snapshot_id=l.snapshot_id AND o.venue_code=l.venue_code AND o.ticker=l.ticker;
            INSERT INTO universe_snapshot VALUES('old', '2026-09-01', '{}', '{}'), ('new', '2026-09-24', '{}', '{}');
            INSERT INTO listed_symbol VALUES
              ('old','Q','EDU','x','equity_candidate','s','2.1.0','technology.software','provisional','{}'),
              ('new','Q','EDU','x','equity_candidate','s','2.1.0','real_estate.ownership','provisional','{}'),
              ('new','Q','SHALLOW','x','equity_candidate','s','2.1.0','healthcare','provisional','{}'),
              ('new','Q','TMO','x','equity_candidate','s','2.1.0','industrials.capital_goods.machinery.heavy','provisional','{}'),
              ('new','Q','DUAL','x','equity_candidate','s','2.1.0','energy.hydrocarbons.upstream','provisional','{}'),
              ('new','N','DUAL','x','equity_candidate','s','2.1.0','materials.chemicals.commodity','provisional','{}'),
              ('new','Q','ETFX','x','etf','s','2.1.0','technology.software.applications','instrument_category_only','{}');
            INSERT INTO listing_classification_override VALUES
              ('new','Q','EDU','equity_candidate','consumer_discretionary.services_housing.education.education_services',
               'approved_provisional','batch-1','2026-09-25');
        """)
        conn.commit()
        conn.close()
        for patcher in (mock.patch.object(symbol_classification, "REGISTRY_DB", self.db),
                        mock.patch.dict("os.environ", {"ECOSYSTEM_CLASSIFICATION_SOURCE": "registry"})):
            patcher.start()
            self.addCleanup(patcher.stop)
        symbol_classification._load_registry.cache_clear()

    def tearDown(self) -> None:
        symbol_classification._load_registry.cache_clear()
        self.tmp.cleanup()

    def test_approved_correction_overrides_the_provider_label(self) -> None:
        row = symbol_classification.classify_symbol({"ticker": "EDU", "sector": "Real Estate", "industry": "REIT - Office"})
        self.assertEqual(row["sector_id"], "consumer_discretionary")
        self.assertEqual(row["industry_id"], "consumer_discretionary.services_housing.education")
        self.assertEqual(row["classification_source"], "babis_registry")
        self.assertEqual(row["registry_status"], "approved_provisional")
        self.assertEqual(row["classification_confidence"], "high")
        self.assertEqual(row["raw_sector"], "Real Estate")  # provider label kept as provenance

    def test_sector_only_registry_node_is_not_index_eligible(self) -> None:
        row = symbol_classification.classify_symbol({"ticker": "SHALLOW", "sector": "Healthcare", "industry": "Biotechnology"})
        self.assertEqual(row["sector_id"], "healthcare")
        self.assertEqual(row["classification_status"], "sector_only")
        self.assertFalse(row["index_analysis_eligible"])

    def test_ambiguous_or_missing_ticker_falls_back_to_provider(self) -> None:
        dual = symbol_classification.classify_symbol({"ticker": "DUAL", "sector": "Energy", "industry": "Oil & Gas E&P"})
        self.assertEqual(dual["classification_source"], "provider_metadata")
        self.assertEqual(dual["sector_id"], "energy")
        etf = symbol_classification.classify_symbol({"ticker": "ETFX", "sector": "Technology", "industry": "Software"})
        self.assertEqual(etf["classification_source"], "provider_metadata")  # instruments get no issuer sector

    def test_uncorroborated_registry_node_does_not_override_the_provider_sector(self) -> None:
        row = symbol_classification.classify_symbol({"ticker": "TMO", "sector": "Healthcare",
                                                     "industry": "Medical - Diagnostics & Research"})
        self.assertEqual(row["sector_id"], "healthcare")
        self.assertEqual(row["classification_source"], "provider_metadata")
        self.assertTrue(row["registry_sector_disagreement"])  # visible, not silently resolved
        self.assertEqual(row["registry_node_id"], "industrials.capital_goods.machinery.heavy")

    def test_provider_switch_restores_old_behaviour(self) -> None:
        with mock.patch.dict("os.environ", {"ECOSYSTEM_CLASSIFICATION_SOURCE": "provider"}):
            row = symbol_classification.classify_symbol({"ticker": "EDU", "sector": "Real Estate", "industry": "REIT - Office"})
        self.assertEqual(row["sector_id"], "real_estate")
        self.assertEqual(row["classification_source"], "provider_metadata")


if __name__ == "__main__":
    unittest.main()
