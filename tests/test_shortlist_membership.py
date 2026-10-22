import tempfile
import unittest
from pathlib import Path
from unittest import mock

import shortlist_membership as sm
import us_equity_monitor as monitor


class MembershipModuleTests(unittest.TestCase):
    def test_members_get_the_lower_exit_threshold_and_newcomers_the_entry_threshold(self):
        self.assertEqual(sm.threshold_for(False, 70.0, 55.0), 55.0 + 15.0)
        self.assertEqual(sm.threshold_for(True, 70.0, 55.0), 55.0)
        self.assertEqual(sm.threshold_for(True, 70.0, None), 70.0)      # no exit threshold configured
        self.assertEqual(sm.threshold_for(True, 70.0, 90.0), 70.0)      # an exit above the entry never tightens
        self.assertIsNone(sm.threshold_for(True, None, 55.0))           # no filter at all

    def test_update_records_additions_removals_and_keeps_unscanned_members(self):
        state = {"schema": sm.SCHEMA, "updated_at": None, "members": {}, "events": []}
        first = sm.update(state, ["AAA", "BBB", "CCC"], {}, "t1")
        self.assertEqual([e["event"] for e in first], ["added"] * 3)
        second = sm.update(state, ["AAA", "DDD"], {"BBB": "below_exit_composite"}, "t2")
        self.assertEqual({(e["ticker"], e["event"], e.get("reason")) for e in second},
                         {("DDD", "added", None), ("BBB", "removed", "below_exit_composite")})
        self.assertEqual(state["members"]["CCC"]["status"], "active")   # not scanned tonight: stays a member
        self.assertEqual(state["members"]["CCC"]["not_scanned_at"], "t2")
        self.assertEqual(state["members"]["AAA"]["since"], "t1")
        third = sm.update(state, ["BBB"], {}, "t3")                      # a removed name can come back
        self.assertEqual(third, [{"at": "t3", "ticker": "BBB", "event": "added"}])
        self.assertEqual(state["members"]["BBB"]["since"], "t3")

    def test_unreadable_or_foreign_state_means_everyone_is_new(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "m.json"
            self.assertEqual(sm.load(path)["members"], {})
            path.write_text("not json")
            self.assertEqual(sm.load(path)["members"], {})
            path.write_text('{"schema": "other", "members": {"X": {}}}')
            self.assertEqual(sm.load(path)["members"], {})
            state = sm.load(path)
            sm.update(state, ["AAA"], {}, "t1")
            sm.save(path, state)
            self.assertEqual(sm.active_tickers(sm.load(path)), {"AAA"})


RULES = [{"metric": "roe", "operator": ">=", "value": 0.12, "weight": 6.0},
         {"metric": "profit_margin", "operator": ">=", "value": 0.1, "weight": 4.0}]


def snapshot_for(symbol, *, roe_ok, margin_ok):
    return {"symbol": symbol, "price_metrics": {"close": 10.0}, "news": [], "sec_recent_filings": [],
            "source_base": {}, "fundamentals": {"roe": 0.2 if roe_ok else 0.0,
                                                 "profit_margin": 0.2 if margin_ok else 0.0}}


class ScanMembershipTests(unittest.TestCase):
    def scan(self, snapshots, membership_active, exit_score=50.0):
        with mock.patch.object(monitor, "fetch_snapshot", side_effect=lambda symbol, *a, **k: snapshots[symbol]), \
             mock.patch.object(monitor, "fetch_fred_macro_context", return_value={}):
            return monitor.scan_market_core(
                symbols=list(snapshots), lookback_days=10, min_history_days=1, max_price=None,
                min_composite_score=70.0, min_fundamental_coverage=None, ma_rules=[], fundamental_rules=RULES,
                ma_weight=0.0, fundamental_weight=1.0, data_sources_cfg={},
                membership_active=membership_active, exit_composite_score=exit_score)

    def test_a_member_survives_a_score_that_would_keep_a_newcomer_out(self):
        snaps = {"NEW": snapshot_for("NEW", roe_ok=True, margin_ok=False),    # 60: below entry 70
                 "OLD": snapshot_for("OLD", roe_ok=True, margin_ok=False),    # 60: above exit 50
                 "GONE": snapshot_for("GONE", roe_ok=False, margin_ok=True),  # 40: below exit
                 "TOP": snapshot_for("TOP", roe_ok=True, margin_ok=True)}     # 100
        payload = self.scan(snaps, {"OLD", "GONE"})
        self.assertEqual(sorted(r["symbol"] for r in payload["results"]), ["OLD", "TOP"])
        self.assertEqual(payload["excluded_tickers"], {"NEW": "below_min_composite", "GONE": "below_exit_composite"})

    def test_sec_values_decide_the_rules_and_yfinance_is_only_a_fallback(self):
        class FakeSec:
            def values(self, symbol):
                return {"roe": 0.30, "profit_margin": None} if symbol == "SEC" else None

        snaps = {"SEC": snapshot_for("SEC", roe_ok=False, margin_ok=True),   # yfinance ROE fails, SEC ROE passes
                 "YF": snapshot_for("YF", roe_ok=False, margin_ok=True)}     # no SEC values: yfinance only
        with mock.patch.object(monitor, "fetch_snapshot", side_effect=lambda symbol, *a, **k: snaps[symbol]),              mock.patch.object(monitor, "fetch_fred_macro_context", return_value={}):
            payload = monitor.scan_market_core(
                symbols=list(snaps), lookback_days=10, min_history_days=1, max_price=None,
                min_composite_score=70.0, min_fundamental_coverage=None, ma_rules=[], fundamental_rules=RULES,
                ma_weight=0.0, fundamental_weight=1.0, data_sources_cfg={}, sec_ratios=FakeSec())
        [row] = payload["results"]
        self.assertEqual((row["symbol"], row["composite_score"], row["fundamental_score_basis"]), ("SEC", 100.0, "sec_first"))
        sources = {d["metric"]: d["value_source"] for d in row["fundamental_rule_details"]}
        self.assertEqual(sources, {"roe": "sec_edgar", "profit_margin": "yfinance_secondary"})
        self.assertEqual(payload["excluded_tickers"], {"YF": "below_min_composite"})

    def test_without_membership_the_entry_threshold_applies_to_everyone(self):
        snaps = {"OLD": snapshot_for("OLD", roe_ok=True, margin_ok=False)}
        self.assertEqual(self.scan(snaps, set())["results"], [])
        self.assertEqual(self.scan(snaps, {"OLD"}, exit_score=None)["results"], [])  # no exit threshold: same as before


if __name__ == "__main__":
    unittest.main()


class ParallelFetchTests(unittest.TestCase):
    """Parallel stage-1 workers must give exactly the output of the sequential loop."""

    def run_scan(self, workers, symbols, snapshots, delays=None):
        import time

        def fake(symbol, *args, **kwargs):
            time.sleep((delays or {}).get(symbol, 0.0))
            value = snapshots[symbol]
            if isinstance(value, Exception):
                raise value
            return value

        with mock.patch.object(monitor, "fetch_snapshot", side_effect=fake), \
             mock.patch.object(monitor, "fetch_fred_macro_context", return_value={}):
            return monitor.scan_market_core(
                symbols=symbols, lookback_days=10, min_history_days=1, max_price=None, min_composite_score=70.0,
                min_fundamental_coverage=None, ma_rules=[], fundamental_rules=RULES, ma_weight=0.0,
                fundamental_weight=1.0, data_sources_cfg={}, workers=workers)

    def snapshots(self):
        names = [f"S{i:02d}" for i in range(12)]
        snaps = {n: snapshot_for(n, roe_ok=i % 3 != 0, margin_ok=i % 2 == 0) for i, n in enumerate(names)}
        snaps["S05"] = ValueError("provider failed")
        return names, snaps

    def test_results_errors_and_order_match_the_sequential_run_even_with_scrambled_latency(self):
        names, snaps = self.snapshots()
        delays = {n: 0.03 * (len(names) - i) % 0.07 for i, n in enumerate(names)}  # later symbols finish first
        sequential = self.run_scan(1, names, snaps)
        parallel = self.run_scan(4, names, snaps, delays)
        strip = lambda p: {k: v for k, v in p.items() if k not in ("run_timestamp", "runtime_summary")}  # noqa: E731
        self.assertEqual(strip(parallel), strip(sequential))
        self.assertEqual([e["symbol"] for e in parallel["errors"]], ["S05"])

    def test_the_sec_gate_is_off_with_one_worker_and_restored_after_a_parallel_run(self):
        names, snaps = self.snapshots()
        self.run_scan(3, names, snaps)
        self.assertEqual(monitor.SEC_MIN_INTERVAL_SECONDS, 0.0)

    def test_the_sec_gate_spaces_requests_when_enabled(self):
        import time
        monitor.SEC_MIN_INTERVAL_SECONDS = 0.05
        monitor._sec_next_slot = 0.0
        try:
            started = time.monotonic()
            for _ in range(4):
                monitor._sec_throttle()
            # three gaps of 0.05 s; a timer can come up a hair short, so allow some slack
            self.assertGreaterEqual(time.monotonic() - started, 0.12)
        finally:
            monitor.SEC_MIN_INTERVAL_SECONDS = 0.0
