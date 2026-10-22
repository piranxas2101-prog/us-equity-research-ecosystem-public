"""ADR-001 step 3 (owner 2026-10-23): multi-day paper positions, leftovers closed, monitors also for Miltos entries.

Nothing here talks to Alpaca: every broker call is replaced by a fake.
"""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import pandas as pd

import alpaca_account_preflight
import alpaca_multi_day_monitor as mdm
import alpaca_paper_position_monitor as monitor
import koulis_risk_gate
import positions_registry as reg

ROOT = Path(__file__).resolve().parents[1]


def opening(ticker="ABC", playbook="support_bounce_swing", direction="long", **extra):
    return {"ticker": ticker, "playbook_name": playbook, "trade_direction": direction, "order_status": "filled",
            "order_response": {"id": f"o-{ticker}", "status": "filled", "filled_qty": "3", "filled_avg_price": "10"},
            **extra}


def entry(ticker="ABC", cls="month", opened="2026-10-01", **extra):
    entries: dict = {}
    reg.register(entries, opening(ticker), agent="yankos", holding_class=cls, today=date.fromisoformat(opened),
                 row={"atr_14_pct": 2.0, "giannis_score": 7.5, "risk_level": "Low"})
    return {**entries[ticker], **extra}


class RegistryTests(unittest.TestCase):
    def test_empty_map_keeps_every_position_a_day_position(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "scanner_config.json"
            config.write_text(json.dumps({"paper_session": {"holding_classes": {}}}), encoding="utf-8")
            self.assertEqual(reg.class_for("yankos", "support_bounce_swing", "long", reg.policy(config)), "day")
        live = reg.policy()
        self.assertEqual(live, {}, "the live map is the owner's decision; it starts empty")

    def test_map_by_agent_playbook_direction_or_playbook(self):
        mapping = {"yankos:support_bounce_swing:long": "quarter", "gap_continuation": "month", "x": "year"}
        self.assertEqual(reg.class_for("yankos", "support_bounce_swing", "long", mapping), "quarter")
        self.assertEqual(reg.class_for("yankos", "support_bounce_swing", "short", mapping), "day")
        self.assertEqual(reg.class_for("periklis", "gap_continuation", "short", mapping), "month")
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "c.json"
            config.write_text(json.dumps({"paper_session": {"holding_classes": mapping}}), encoding="utf-8")
            self.assertNotIn("x", reg.policy(config), "an unknown class is ignored, never guessed")

    def test_unknown_atr_is_not_registered(self):
        entries: dict = {}
        self.assertFalse(reg.register(entries, opening(), agent="yankos", holding_class="month",
                                      today=date(2026, 10, 23), row={"atr_14_pct": None}))
        self.assertFalse(reg.register(entries, opening(), agent="yankos", holding_class="day",
                                      today=date(2026, 10, 23), row={"atr_14_pct": 2.0}))
        self.assertEqual(entries, {})

    def test_register_once_with_entry_facts(self):
        e = entry()
        self.assertEqual((e["sessions"], e["entry_price"], e["qty"], e["atr_14_pct"], e["giannis_score"]),
                         (20, 10.0, "3", 2.0, 7.5))

    def test_sessions_held_counts_weekdays_and_today(self):
        self.assertEqual(reg.sessions_held("2026-10-23", date(2026, 10, 23)), 1)
        self.assertEqual(reg.sessions_held("2026-10-23", date(2026, 10, 26)), 2)  # Friday -> Monday
        self.assertEqual(reg.sessions_held("2026-10-19", date(2026, 10, 23)), 5)

    def test_update_and_close_write_the_closed_log(self):
        with tempfile.TemporaryDirectory() as folder:
            path, closed = Path(folder) / "p.json", Path(folder) / "closed.jsonl"
            reg.update(lambda entries: entries.update(ABC=entry()), path)
            self.assertIn("ABC", reg.load(path))
            reg.update(lambda entries: reg.close(entries, "ABC", "time_stop", path=closed), path)
            self.assertEqual(reg.load(path), {})
            self.assertEqual(json.loads(closed.read_text(encoding="utf-8"))["close_reason"], "time_stop")
            self.assertFalse(path.with_suffix(".lock").exists())


class PreflightTests(unittest.TestCase):
    SNAPSHOT = {"account": {}, "positions": [{"symbol": "INCY", "side": "long", "qty": "2"},
                                             {"symbol": "KEEP", "side": "long", "qty": "1"}],
                "open_orders": [{"id": "1", "symbol": "KEEP", "status": "new"},
                                {"id": "2", "symbol": "ROKU", "status": "new"}]}

    def test_flatten_keeps_registered_symbols(self):
        actions = alpaca_account_preflight.flatten_snapshot(self.SNAPSHOT, dry_run=True, keep={"KEEP"})
        self.assertEqual({(a["type"], a["symbol"]) for a in actions},
                         {("close_position", "INCY"), ("cancel_order", "ROKU")})

    def test_run_closes_leftovers_does_not_block_and_reports_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            registry = Path(folder) / "p.json"
            registry.write_text(json.dumps({"KEEP": {"symbol": "KEEP"}}), encoding="utf-8")
            args = SimpleNamespace(auto_flatten=True, dry_run=False, block_on_existing=True,
                                   keep_registered=str(registry), out_prefix=str(Path(folder) / "pre"))
            with mock.patch.object(alpaca_account_preflight, "load_dotenv"), \
                    mock.patch.object(alpaca_account_preflight, "fetch_snapshot", return_value=self.SNAPSHOT), \
                    mock.patch.object(alpaca_account_preflight, "close_position", return_value={"status": "accepted"}) as close, \
                    mock.patch.object(alpaca_account_preflight, "cancel_order", return_value={}):
                code = alpaca_account_preflight.run(args)
            payload = json.loads((Path(folder) / "pre_latest.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        close.assert_called_once_with("INCY")
        self.assertFalse(payload["block_trader_run"])
        self.assertEqual(payload["kept_registered"], ["KEEP"])
        self.assertEqual([p["symbol"] for p in payload["kept_positions"]], ["KEEP"])

    def test_failed_close_still_blocks(self):
        with tempfile.TemporaryDirectory() as folder:
            args = SimpleNamespace(auto_flatten=True, dry_run=False, block_on_existing=True, keep_registered=None,
                                   out_prefix=str(Path(folder) / "pre"))
            with mock.patch.object(alpaca_account_preflight, "load_dotenv"), \
                    mock.patch.object(alpaca_account_preflight, "fetch_snapshot", return_value=self.SNAPSHOT), \
                    mock.patch.object(alpaca_account_preflight, "close_position", side_effect=RuntimeError("x")), \
                    mock.patch.object(alpaca_account_preflight, "cancel_order", return_value={}):
                self.assertEqual(alpaca_account_preflight.run(args), 2)


class KoulisTests(unittest.TestCase):
    def build(self, kept):
        from tests.test_risk_gate import signal, strategy
        rows = [signal("yankos", t, "long", 8.0 + i / 10, playbook=f"p{i}") for i, t in enumerate("ABC")]
        preflight = {"block_trader_run": False, "kept_positions": kept,
                     "snapshot_before": {"positions": kept, "open_orders": []}}
        return koulis_risk_gate.build_gate([("yankos", strategy("yankos", rows))], preflight, run_id="run_test",
                                           min_score=7.0, max_total_orders=3, notional_per_trade=250.0,
                                           max_total_notional=750.0, max_short_orders=1)

    def test_no_held_positions_limits_as_before(self):
        self.assertEqual(self.build([])["approved_count"], 3)

    def test_held_positions_take_slots_and_notional(self):
        payload = self.build([{"symbol": "HELD", "side": "long", "market_value": "260"}])
        self.assertEqual(payload["approved_count"], 1)  # 1 slot left by count, 490 USD left by notional
        self.assertEqual(payload["held_multi_day_positions"], ["HELD"])
        self.assertEqual(payload["held_multi_day_notional"], 260.0)

    def test_unknown_held_value_blocks_new_orders(self):
        payload = self.build([{"symbol": "HELD", "side": "long", "market_value": None}])
        self.assertEqual((payload["approved_count"], payload["gate_reason"]), (0, "held_position_value_unknown"))
        self.assertIsNone(payload["held_multi_day_notional"])


class DayMonitorHandoffTests(unittest.TestCase):
    def run_monitor(self, mapping, row):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            report = folder / "yankos_paper_trades_latest.json"
            report.write_text(json.dumps({"agent_name": "yankos", "openings": [opening()]}), encoding="utf-8")
            (folder / "s.json").write_text(json.dumps({"results": [row]}), encoding="utf-8")
            registry = folder / "p.json"
            args = SimpleNamespace(open_report=str(report), strategy=str(folder / "s.json"), agent_name=None,
                                   eod_cutoff="15:55", poll_seconds=60, today_only_policy=True, exit_score_floor=4.75,
                                   close_on_watch=True, stop_loss_pct=0.03, take_profit_pct=0.06, submit=False,
                                   post_action_refresh_seconds=0, post_action_refresh_attempts=1)
            with mock.patch.object(monitor, "refreshed_order", return_value={"id": "o-ABC", "status": "filled",
                                                                             "filled_qty": "3"}), \
                    mock.patch.object(monitor, "fetch_position", return_value={"qty": "3", "unrealized_plpc": "-0.05"}), \
                    mock.patch.object(monitor, "eod_cutoff_reached", return_value=True), \
                    mock.patch.object(reg, "policy", return_value=mapping), \
                    mock.patch.object(reg, "REGISTRY", registry), \
                    mock.patch.object(reg.update, "__defaults__", (registry,)):
                payload = monitor.monitor_once(args)
            return payload, reg.load(registry)

    ROW = {"ticker": "ABC", "playbook_name": "support_bounce_swing", "atr_14_pct": 2.0, "giannis_score": 7.0,
           "signal_score": 9.0, "signal_verdict": "candidate"}

    def test_day_position_is_still_flattened(self):
        payload, registry = self.run_monitor({}, self.ROW)
        self.assertEqual([a["action_type"] for a in payload["actions"]], ["close_position"])
        self.assertEqual(registry, {})

    def test_multi_day_position_is_handed_over_not_closed(self):
        payload, registry = self.run_monitor({"support_bounce_swing": "quarter"}, self.ROW)
        self.assertEqual(payload["actions"], [])
        self.assertEqual(payload["remaining_active_count"], 0)
        self.assertEqual(registry["ABC"]["holding_class"], "quarter")

    def test_multi_day_with_unknown_atr_falls_back_to_day(self):
        payload, registry = self.run_monitor({"support_bounce_swing": "quarter"}, {**self.ROW, "atr_14_pct": None})
        self.assertEqual([a["action_type"] for a in payload["actions"]], ["close_position"])
        self.assertEqual(registry, {})


class MultiDayDecisionTests(unittest.TestCase):
    def decide(self, e, plpc, *, today, minutes=600, shock=None, board=None):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "yankos_strategy_latest.json").write_text(
                json.dumps({"run_id": "run_new", "results": board or []}), encoding="utf-8")
            return mdm.decide(e, {"unrealized_plpc": str(plpc)}, today=today, minutes=minutes, shock=shock,
                              strategy_dir=Path(folder))

    def test_stop_widens_with_time(self):
        e = entry(opened="2026-10-01")  # ATR 2% -> 2.2% on day 1, 1.1*2%*sqrt(17) ~ 9.1% on 10/23
        self.assertEqual(self.decide(e, -0.05, today=date(2026, 10, 1))[0], "scaled_stop")
        self.assertIsNone(self.decide(e, -0.05, today=date(2026, 10, 23))[0])
        self.assertEqual(self.decide(e, -0.10, today=date(2026, 10, 23))[0], "scaled_stop")

    def test_thesis_break_once_per_run_and_missing_stock_is_held(self):
        e = entry(cls="quarter", opened="2026-10-01")
        board = [{"ticker": "ABC", "playbook_name": "support_bounce_swing", "risk_level": "High",
                  "giannis_score": 7.5}]
        self.assertIsNone(self.decide(dict(e), 0.01, today=date(2026, 10, 23))[0])  # not on the board: unknown
        reason, _ = self.decide(e, 0.01, today=date(2026, 10, 23), board=board)
        self.assertEqual(reason, "thesis_risk_high")
        self.assertEqual(e["last_thesis_run"], "run_new")
        self.assertIsNone(self.decide(e, 0.01, today=date(2026, 10, 23), board=board)[0], "judged once per run")

    def test_no_thesis_judgement_on_the_opening_session(self):
        e = entry(cls="quarter", opened="2026-10-23")
        board = [{"ticker": "ABC", "playbook_name": "support_bounce_swing", "risk_level": "High"}]
        self.assertIsNone(self.decide(e, 0.0, today=date(2026, 10, 23), board=board)[0])

    def test_market_shock_against_the_position_near_the_close_only(self):
        e = entry(opened="2026-10-20")
        self.assertIsNone(self.decide(e, 0.0, today=date(2026, 10, 23), minutes=600, shock="down")[0])
        self.assertEqual(self.decide(e, 0.0, today=date(2026, 10, 23), minutes=mdm.SHOCK_FROM, shock="down")[0],
                         "market_shock")
        self.assertIsNone(self.decide(e, 0.0, today=date(2026, 10, 23), minutes=mdm.SHOCK_FROM, shock="up")[0])
        self.assertTrue(mdm.shock_against("volatile", "short"))

    def test_time_stop_on_the_last_session(self):
        e = entry(cls="week", opened="2026-10-19")
        self.assertIsNone(self.decide(e, 0.0, today=date(2026, 10, 22), minutes=mdm.TIME_STOP_FROM)[0])
        self.assertIsNone(self.decide(e, 0.0, today=date(2026, 10, 23), minutes=600)[0])
        self.assertEqual(self.decide(e, 0.0, today=date(2026, 10, 23), minutes=mdm.TIME_STOP_FROM)[0], "time_stop")

    def test_monitor_once_closes_and_records(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            registry, closed = folder / "p.json", folder / "c.jsonl"
            reg.save({"ABC": entry(cls="week", opened="2026-10-19"), "GONE": entry("GONE")}, registry)
            (folder / "yankos_strategy_latest.json").write_text("{}", encoding="utf-8")
            args = SimpleNamespace(registry=str(registry), closed_log=str(closed), strategy_dir=str(folder),
                                   submit=True)
            now = pd.Timestamp("2026-10-23 15:51", tz="America/New_York").to_pydatetime()
            with mock.patch.object(mdm, "now_eastern", return_value=now), \
                    mock.patch.object(mdm, "spy_daily", return_value=None), \
                    mock.patch.object(mdm, "fetch_position",
                                      side_effect=lambda s: None if s == "GONE" else {"qty": "3", "unrealized_plpc": "0.02"}), \
                    mock.patch.object(mdm, "close_position_qty", return_value={"id": "x", "status": "accepted"}) as send:
                payload = mdm.monitor_once(args, {})
            send.assert_called_once_with("ABC", "3")
            self.assertEqual(payload["actions"][0]["exit_rule"], "time_stop")
            self.assertEqual(reg.load(registry), {})
            reasons = {json.loads(line)["symbol"]: json.loads(line)["close_reason"]
                       for line in closed.read_text(encoding="utf-8").splitlines()}
            self.assertEqual(reasons, {"ABC": "time_stop", "GONE": "not_in_account"})

    def test_market_shocks_reused_on_spy_frame(self):
        index = pd.bdate_range("2025-01-01", periods=260)
        close = pd.Series(100.0, index=index)
        close.iloc[-1] = 90.0
        frame = pd.DataFrame({"High": close + 0.5, "Low": close - 0.5, "Close": close})
        self.assertEqual(mdm.thesis_exit.market_shocks(frame).get(index[-1].date()), "down")


class LauncherTests(unittest.TestCase):
    def test_monitors_start_when_only_miltos_enters(self):
        text = (ROOT / "start_trader_paper_trading_today.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("$miltosWillEnter = (-not $NoStartMiltos) -and ($approvedIntentCount -gt 0)", text)
        self.assertIn("if (($submittedTotal -gt 0) -or $miltosWillEnter) {", text)
        self.assertLess(text.index("$approvedIntentCount = [int]"), text.index("$miltosWillEnter ="))

    def test_leftovers_closed_registered_kept_by_default(self):
        text = (ROOT / "start_trader_paper_trading_today.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('"--auto-flatten", "--keep-registered", "data\\paper_positions.json"', text)
        self.assertIn("start_multi_day_monitor_today.ps1", text)

    def test_afternoon_looks_after_held_positions_without_orders(self):
        text = (ROOT / "start_trader_afternoon_session.ps1").read_text(encoding="utf-8-sig")
        branch = text[text.index('Write-Log "No paper orders today: $reason"'):]
        branch = branch[:branch.index("exit 0")]
        self.assertIn('"--keep-registered" "data\\paper_positions.json"', branch)
        self.assertIn("start_multi_day_monitor_today.ps1", branch)
        self.assertIn("$sessionWorkers", branch)
        self.assertIn("alpaca_multi_day_monitor", text.split("$sessionWorkers =", 1)[1].splitlines()[0])


if __name__ == "__main__":
    unittest.main()
