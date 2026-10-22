"""Positions held on their agent's horizon (owner 2026-10-23); leftovers closed; monitors also for Miltos entries.

Nothing here talks to Alpaca: every broker call is replaced by a fake.
"""
from __future__ import annotations

import copy
from datetime import datetime
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
import holding_horizon
import koulis_risk_gate
import miltos_entry_stalker
import paper_trade_ledger
import playbook_framework
import positions_registry as reg
import trader_trade_plan
import trading_clock as clock
from thymios_order_intent import IntentValidationError, validate_intent_payload

ROOT = Path(__file__).resolve().parents[1]
NY = clock.EASTERN


def ny(text: str) -> datetime:
    return pd.Timestamp(text, tz=NY).to_pydatetime()


CURVE = {("support_bounce_swing", "long"): [{"sessions": 60, "edge_pct": 2.1, "edge_ci": [1, 3], "years": "5/5", "n": 1},
                                            {"sessions": 5, "edge_pct": 0.8, "edge_ci": [0.1, 1], "years": "4/5", "n": 1}],
         ("gap_fade", "short"): [{"sessions": 5, "edge_pct": 1.9, "edge_ci": [1, 3], "years": "5/5", "n": 1},
                                 {"sessions": 10, "edge_pct": 2.6, "edge_ci": [1, 3], "years": "5/5", "n": 1}]}


def horizon(sessions: int) -> dict:
    return {"rule": holding_horizon.RULE, "sessions": sessions, "minutes": sessions * 390, "basis": "test"}


def opening(ticker="ABC", sessions: int | None = 3, filled_at="2026-10-21T14:00:00Z", **extra):
    plan = ({"holding_period_rule": holding_horizon.RULE, "holding_horizon": horizon(sessions)} if sessions
            else {"holding_period_rule": holding_horizon.SAME_DAY})
    return {"ticker": ticker, "playbook_name": "support_bounce_swing", "trade_direction": "long",
            "order_status": "filled", "paper_trade_plan": plan,
            "order_response": {"id": f"o-{ticker}", "status": "filled", "filled_qty": "3", "filled_avg_price": "10",
                               "filled_at": filled_at}, **extra}


def entry(ticker="ABC", sessions=3, filled_at="2026-10-21T14:00:00Z", **extra):
    entries: dict = {}
    assert reg.register(entries, opening(ticker, sessions, filled_at), agent="yankos",
                        row={"atr_14_pct": 2.0, "giannis_score": 7.5, "risk_level": "Low"})
    return {**entries[ticker], **extra}


class TradingClockTests(unittest.TestCase):
    def test_holidays_and_early_closes(self):
        self.assertIsNone(clock.session(datetime(2026, 11, 26).date()))  # Thanksgiving
        self.assertEqual(clock.session(datetime(2026, 11, 27).date())[1].hour, 13)  # early close
        self.assertEqual(clock.minutes_to_close(ny("2026-11-27 12:50")), 10)

    def test_minutes_cross_nights_and_weekends(self):
        self.assertEqual(clock.trading_minutes_between(ny("2026-10-23 14:00"), ny("2026-10-26 10:00")), 150)
        self.assertEqual(clock.add_trading_minutes(ny("2026-10-23 11:00"), 390), pd.Timestamp("2026-10-26 11:00", tz=NY))
        # five hours from 14:00: two today, three on Monday
        self.assertEqual(clock.add_trading_minutes(ny("2026-10-23 14:00"), 300), pd.Timestamp("2026-10-26 12:30", tz=NY))
        # a fill before the open counts from the open
        self.assertEqual(clock.add_trading_minutes(ny("2026-10-23 08:00"), 30), pd.Timestamp("2026-10-23 10:00", tz=NY))


class HorizonDecisionTests(unittest.TestCase):
    def test_edge_per_sqrt_session_and_no_edge_stays_same_day(self):
        self.assertEqual(holding_horizon.decide("support_bounce_swing", "long", CURVE)["sessions"], 5)  # .36 vs .27
        self.assertEqual(holding_horizon.decide("gap_fade", "short", CURVE)["sessions"], 5)  # .85 vs .82
        none = holding_horizon.decide("rsi2_mean_reversion", "long", CURVE)
        self.assertEqual((none["rule"], none["sessions"], none["basis"]),
                         (holding_horizon.SAME_DAY, None, "no_stable_edge_at_any_horizon"))
        self.assertEqual(holding_horizon.decide("gap_fade", "short", {})["basis"], "no_horizon_curve")

    def test_curve_keeps_only_stable_edges(self):
        report = {"horizons": {"s5": 5, "s10": 10, "s20": 20}, "results": [
            {"strategy": "x", "side": "long", "class": "s5", "rule": "fixed", "edge_verdict": "edge",
             "edge_vs_random_pct": 1.0, "years": 5, "years_positive": 5},
            {"strategy": "x", "side": "long", "class": "s10", "rule": "fixed", "edge_verdict": "edge",
             "edge_vs_random_pct": 3.0, "years": 4, "years_positive": 2},  # half the years: not stable
            {"strategy": "x", "side": "long", "class": "s20", "rule": "fixed", "edge_verdict": "no clear edge",
             "edge_vs_random_pct": 9.0, "years": 5, "years_positive": 5}]}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "c.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            self.assertEqual([o["sessions"] for o in holding_horizon.load_curve(path)[("x", "long")]], [5])

    def test_valid_and_rules_class(self):
        self.assertTrue(holding_horizon.valid(horizon(7)))
        self.assertFalse(holding_horizon.valid({**horizon(7), "minutes": 100}))
        self.assertFalse(holding_horizon.valid(horizon(61)))
        self.assertEqual([holding_horizon.rules_class(n) for n in (1, 5, 10, 20, 60)],
                         ["week", "week", "month", "month", "quarter"])


class TradePlanAndThymiosTests(unittest.TestCase):
    def setUp(self):
        self.catalog = playbook_framework.load_catalog(ROOT / "playbook_catalog_v2.json")
        self.config = json.loads((ROOT / "yankos_playbooks.json").read_text(encoding="utf-8"))
        self.row = {"agent_name": "yankos", "ticker": "TEST", "company": "Test", "trade_direction": "long",
                    "playbook_name": "support_bounce_swing", "signal_score": 8.2, "signal_verdict": "candidate",
                    "confidence": "High", "instrument_type": "common", "risk_level": "Low",
                    "fresh_primary_data": True, "fundamental_packet_hash": "h", "fundamental_thesis_id": "t",
                    "fundamental_thesis_hash": "th", "fundamental_gate_status": "pass", "execution_eligible": True,
                    "atr_14_pct": 4.0, "rationale": ["r"]}

    def test_live_policy_lets_the_agent_decide(self):
        for name in ("yankos_playbooks.json", "periklis_playbooks.json"):
            config = json.loads((ROOT / name).read_text(encoding="utf-8"))
            self.assertEqual(config["paper_sizing_policy"]["holding_period_rule"], holding_horizon.RULE, name)

    def gate(self, curve):
        playbook = playbook_framework.catalog_by_id(self.catalog)["support_bounce_swing"]
        plan = trader_trade_plan.build_trade_plan("yankos", self.row, playbook, self.config["paper_sizing_policy"],
                                                  "run_test", curve)
        strategy = {"run_id": "run_test", "agent_name": "yankos", "results": [{**self.row, "paper_trade_plan": plan}]}
        return plan, koulis_risk_gate.build_gate(
            [("yankos", strategy)], {"block_trader_run": False, "snapshot_before": {"positions": [], "open_orders": []}},
            run_id="run_test", min_score=7.0, max_total_orders=5, notional_per_trade=250.0,
            max_total_notional=5000.0, max_short_orders=2, notional_source="agent_suggestion")

    def test_plan_carries_the_horizon_and_thymios_accepts_it(self):
        plan, payload = self.gate(CURVE)
        self.assertEqual((plan["holding_period_rule"], plan["holding_horizon"]["sessions"]), (holding_horizon.RULE, 5))
        validate_intent_payload(payload)

    def test_without_evidence_the_plan_is_same_day(self):
        plan, payload = self.gate({})
        self.assertEqual(plan["holding_period_rule"], holding_horizon.SAME_DAY)
        validate_intent_payload(payload)

    def test_thymios_refuses_a_broken_horizon(self):
        _, payload = self.gate(CURVE)
        bad = copy.deepcopy(payload)
        intent_plan = bad["approved_intents"][0]["trade_plan"]
        intent_plan["holding_horizon"]["minutes"] = 5
        basis = {k: v for k, v in intent_plan.items() if k not in {"trade_plan_sha256", "execution_authority"}}
        intent_plan["trade_plan_sha256"] = trader_trade_plan.digest(basis)
        with self.assertRaisesRegex(IntentValidationError, "horizon"):
            validate_intent_payload(bad)


class RegistryTests(unittest.TestCase):
    def test_register_with_expiry_from_the_fill(self):
        e = entry(sessions=3, filled_at="2026-10-21T14:00:00Z")  # 10:00 New York, Wednesday
        self.assertEqual(e["expires_at"], pd.Timestamp("2026-10-26 10:00", tz=NY).isoformat())
        self.assertEqual((e["holding_class"], e["opened_session"], e["horizon_minutes"]), ("week", "2026-10-21", 1170))

    def test_same_day_unknown_atr_or_fill_time_not_registered(self):
        entries: dict = {}
        row = {"atr_14_pct": 2.0}
        self.assertFalse(reg.register(entries, opening(sessions=None), agent="yankos", row=row))
        self.assertFalse(reg.register(entries, opening(), agent="yankos", row={"atr_14_pct": None}))
        self.assertFalse(reg.register(entries, opening(filled_at=None), agent="yankos", row=row))
        self.assertEqual(entries, {})

    def test_update_and_close_write_the_closed_log(self):
        with tempfile.TemporaryDirectory() as folder:
            path, closed = Path(folder) / "p.json", Path(folder) / "closed.jsonl"
            reg.update(lambda entries: entries.update(ABC=entry()), path)
            reg.update(lambda entries: reg.close(entries, "ABC", "horizon_end", path=closed), path)
            self.assertEqual(reg.load(path), {})
            self.assertEqual(json.loads(closed.read_text(encoding="utf-8"))["close_reason"], "horizon_end")
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

    def run_preflight(self, registry_text, close):
        with tempfile.TemporaryDirectory() as folder:
            registry = Path(folder) / "p.json"
            registry.write_text(registry_text, encoding="utf-8")
            args = SimpleNamespace(auto_flatten=True, dry_run=False, block_on_existing=True,
                                   keep_registered=str(registry), out_prefix=str(Path(folder) / "pre"))
            with mock.patch.object(alpaca_account_preflight, "load_dotenv"), \
                    mock.patch.object(alpaca_account_preflight, "fetch_snapshot", return_value=self.SNAPSHOT), \
                    mock.patch.object(alpaca_account_preflight, "close_position", **close) as sent, \
                    mock.patch.object(alpaca_account_preflight, "cancel_order", return_value={}):
                code = alpaca_account_preflight.run(args)
            return code, sent, json.loads((Path(folder) / "pre_latest.json").read_text(encoding="utf-8"))

    def test_leftovers_closed_kept_reported_not_blocking(self):
        code, sent, payload = self.run_preflight('{"KEEP": {"symbol": "KEEP"}}', {"return_value": {"status": "accepted"}})
        self.assertEqual(code, 0)
        sent.assert_called_once_with("INCY")
        self.assertEqual((payload["block_trader_run"], payload["kept_registered"]), (False, ["KEEP"]))

    def test_failed_close_still_blocks(self):
        code, _, _ = self.run_preflight("{}", {"side_effect": RuntimeError("x")})
        self.assertEqual(code, 2)


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
        self.assertEqual(payload["approved_count"], 1)
        self.assertEqual((payload["held_multi_day_positions"], payload["held_multi_day_notional"]), (["HELD"], 260.0))

    def test_unknown_held_value_blocks_new_orders(self):
        payload = self.build([{"symbol": "HELD", "side": "long", "market_value": None}])
        self.assertEqual((payload["approved_count"], payload["gate_reason"]), (0, "held_position_value_unknown"))


class DayMonitorHandoffTests(unittest.TestCase):
    ROW = {"ticker": "ABC", "playbook_name": "support_bounce_swing", "atr_14_pct": 2.0, "giannis_score": 7.0,
           "signal_score": 9.0, "signal_verdict": "candidate"}

    def run_monitor(self, item, row):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            report = folder / "yankos_paper_trades_latest.json"
            report.write_text(json.dumps({"agent_name": "yankos", "openings": [item]}), encoding="utf-8")
            (folder / "s.json").write_text(json.dumps({"results": [row]}), encoding="utf-8")
            registry = folder / "p.json"
            args = SimpleNamespace(open_report=str(report), strategy=str(folder / "s.json"), agent_name=None,
                                   eod_cutoff="15:55", poll_seconds=60, today_only_policy=True, exit_score_floor=4.75,
                                   close_on_watch=True, stop_loss_pct=0.03, take_profit_pct=0.06, submit=False,
                                   post_action_refresh_seconds=0, post_action_refresh_attempts=1)
            with mock.patch.object(monitor, "refreshed_order", return_value=item["order_response"]), \
                    mock.patch.object(monitor, "fetch_position", return_value={"qty": "3", "unrealized_plpc": "-0.05"}), \
                    mock.patch.object(monitor, "eod_cutoff_reached", return_value=True), \
                    mock.patch.object(reg.update, "__defaults__", (registry,)):
                payload = monitor.monitor_once(args)
            return payload, reg.load(registry)

    def test_same_day_position_is_still_flattened(self):
        payload, registry = self.run_monitor(opening(sessions=None), self.ROW)
        self.assertEqual([a["action_type"] for a in payload["actions"]], ["close_position"])
        self.assertEqual(registry, {})

    def test_agent_horizon_position_is_handed_over_not_closed(self):
        payload, registry = self.run_monitor(opening(sessions=10), self.ROW)
        self.assertEqual((payload["actions"], payload["remaining_active_count"]), ([], 0))
        self.assertEqual(registry["ABC"]["horizon_sessions"], 10)

    def test_unknown_atr_falls_back_to_same_day(self):
        payload, registry = self.run_monitor(opening(sessions=10), {**self.ROW, "atr_14_pct": None})
        self.assertEqual([a["action_type"] for a in payload["actions"]], ["close_position"])
        self.assertEqual(registry, {})

    def test_early_close_moves_the_same_day_flat(self):
        with mock.patch.object(monitor, "now_eastern", return_value=ny("2026-11-27 12:56")):
            self.assertTrue(monitor.eod_cutoff_reached(15 * 60 + 55))
        with mock.patch.object(monitor, "now_eastern", return_value=ny("2026-11-27 12:00")):
            self.assertFalse(monitor.eod_cutoff_reached(15 * 60 + 55))


class MultiDayDecisionTests(unittest.TestCase):
    def decide(self, e, plpc, now, shock=None, board=None):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "yankos_strategy_latest.json").write_text(
                json.dumps({"run_id": "run_new", "results": board or []}), encoding="utf-8")
            return mdm.decide(e, {"unrealized_plpc": str(plpc)}, now=now, shock=shock, strategy_dir=Path(folder))

    def test_stop_widens_with_time(self):
        e = entry(sessions=60, filled_at="2026-10-01T14:00:00Z")  # ATR 2%: 2.2% at first, ~9% after 17 sessions
        self.assertEqual(self.decide(e, -0.05, ny("2026-10-01 15:00"))[0], "scaled_stop")
        self.assertIsNone(self.decide(e, -0.05, ny("2026-10-23 11:00"))[0])
        self.assertEqual(self.decide(e, -0.10, ny("2026-10-23 11:00"))[0], "scaled_stop")

    def test_thesis_once_per_run_missing_stock_held_not_on_opening_day(self):
        e = entry(sessions=60, filled_at="2026-10-01T14:00:00Z")
        board = [{"ticker": "ABC", "playbook_name": "support_bounce_swing", "risk_level": "High", "giannis_score": 7.5}]
        self.assertIsNone(self.decide(dict(e), 0.01, ny("2026-10-23 10:00"))[0])
        self.assertEqual(self.decide(e, 0.01, ny("2026-10-23 10:00"), board=board)[0], "thesis_risk_high")
        self.assertIsNone(self.decide(e, 0.01, ny("2026-10-23 10:05"), board=board)[0], "judged once per run")
        fresh = entry(sessions=60, filled_at="2026-10-23T14:00:00Z")
        self.assertIsNone(self.decide(fresh, 0.0, ny("2026-10-23 11:00"), board=board)[0])

    def test_market_shock_against_the_position_near_the_close_only(self):
        e = entry(sessions=20, filled_at="2026-10-20T14:00:00Z")
        self.assertIsNone(self.decide(e, 0.0, ny("2026-10-23 12:00"), shock="down")[0])
        self.assertEqual(self.decide(e, 0.0, ny("2026-10-23 15:46"), shock="down")[0], "market_shock")
        self.assertIsNone(self.decide(e, 0.0, ny("2026-10-23 15:46"), shock="up")[0])
        self.assertTrue(mdm.shock_against("volatile", "short"))

    def test_horizon_end_at_its_minute_or_before_the_close(self):
        e = entry(sessions=1, filled_at="2026-10-22T15:00:00Z")  # 11:00 Thursday -> 11:00 Friday
        self.assertIsNone(self.decide(e, 0.0, ny("2026-10-23 10:59"))[0])
        self.assertEqual(self.decide(e, 0.0, ny("2026-10-23 11:00"))[0], "horizon_end")
        late = entry(sessions=1, filled_at="2026-10-22T13:31:00Z")  # 09:31 -> 09:31 the next day
        self.assertIsNone(self.decide(late, 0.0, ny("2026-10-22 15:56"))[0], "it does not end today")
        at_open = entry(sessions=1, filled_at="2026-10-22T13:00:00Z")  # filled pre-market: ends at today's close
        self.assertEqual(self.decide(at_open, 0.0, ny("2026-10-22 15:56"))[0], "horizon_end")

    def test_monitor_once_closes_and_records(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            registry, closed = folder / "p.json", folder / "c.jsonl"
            reg.save({"ABC": entry(sessions=1, filled_at="2026-10-22T14:00:00Z"), "GONE": entry("GONE")}, registry)
            (folder / "yankos_strategy_latest.json").write_text("{}", encoding="utf-8")
            args = SimpleNamespace(registry=str(registry), closed_log=str(closed), strategy_dir=str(folder),
                                   submit=True)
            with mock.patch.object(mdm, "now_eastern", return_value=ny("2026-10-23 10:30")), \
                    mock.patch.object(mdm, "fetch_position",
                                      side_effect=lambda s: None if s == "GONE" else {"qty": "-3", "unrealized_plpc": "0.02"}), \
                    mock.patch.object(mdm, "close_position_qty", return_value={"id": "x", "status": "accepted"}) as send:
                payload = mdm.monitor_once(args, {})
            send.assert_called_once_with("ABC", "3")
            self.assertEqual(payload["actions"][0]["exit_rule"], "horizon_end")
            self.assertEqual(reg.load(registry), {})
            reasons = {json.loads(x)["symbol"]: json.loads(x)["close_reason"]
                       for x in closed.read_text(encoding="utf-8").splitlines()}
            self.assertEqual(reasons, {"ABC": "horizon_end", "GONE": "not_in_account"})

    def test_market_shocks_reused_on_spy_frame(self):
        index = pd.bdate_range("2025-01-01", periods=260)
        close = pd.Series(100.0, index=index)
        close.iloc[-1] = 90.0
        frame = pd.DataFrame({"High": close + 0.5, "Low": close - 0.5, "Close": close})
        self.assertEqual(mdm.thesis_exit.market_shocks(frame).get(index[-1].date()), "down")


class MiltosWindowTests(unittest.TestCase):
    CONFIG = {"entry_allowed_from_eastern": "09:30", "entry_allowed_until_eastern": "15:00",
              "agent_horizon_entry_until_eastern": "15:45"}

    def test_horizon_positions_may_enter_until_15_45_and_before_an_early_close(self):
        window = miltos_entry_stalker.horizon_entry_window(self.CONFIG, ny("2026-10-23 15:30"))
        self.assertTrue(miltos_entry_stalker.entry_window_open(window, ny("2026-10-23 15:30"))[0])
        self.assertFalse(miltos_entry_stalker.entry_window_open(self.CONFIG, ny("2026-10-23 15:30"))[0])
        early = miltos_entry_stalker.horizon_entry_window(self.CONFIG, ny("2026-11-27 11:00"))
        self.assertEqual(early["entry_allowed_until_eastern"], "12:45")

    def test_live_config_has_the_horizon_window(self):
        config = json.loads((ROOT / "trading_window_config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["agent_horizon_entry_until_eastern"], "15:45")


class LedgerTests(unittest.TestCase):
    def test_held_trade_completed_when_it_closes(self):
        record = {"record_id": paper_trade_ledger.record_id(datetime(2026, 10, 21).date(), "trade", "yankos", "ABC",
                                                            "support_bounce_swing", "long"),
                  "session_date": "2026-10-21", "kind": "trade", "exit_reason": "held_on_horizon", "complete": False}
        with tempfile.TemporaryDirectory() as folder:
            closed = Path(folder) / "c.jsonl"
            closed.write_text(json.dumps({**entry(), "close_reason": "horizon_end", "exit_plpc": 0.034,
                                          "closed_at": "2026-10-26T14:00:00+00:00"}) + "\n", encoding="utf-8")
            conn = paper_trade_ledger.connect(Path(folder) / "l.db")
            try:
                paper_trade_ledger.store(conn, [record])
                self.assertEqual(paper_trade_ledger.complete_held(conn, closed), 1)
                stored = paper_trade_ledger.load(conn)[0]
            finally:
                conn.close()
        self.assertEqual((stored["result_pct"], stored["exit_reason"], stored["exit_session"], stored["complete"]),
                         (3.4, "horizon_end", "2026-10-26", True))


class LauncherTests(unittest.TestCase):
    def test_monitors_start_when_only_miltos_enters(self):
        text = (ROOT / "start_trader_paper_trading_today.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("$miltosWillEnter = (-not $NoStartMiltos) -and ($approvedIntentCount -gt 0)", text)
        self.assertIn("if (($submittedTotal -gt 0) -or $miltosWillEnter) {", text)
        self.assertIn('start_multi_day_monitor_today.ps1" -PythonExe $PythonExe -UntilClose', text)

    def test_leftovers_closed_registered_kept_by_default(self):
        text = (ROOT / "start_trader_paper_trading_today.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('"--auto-flatten", "--keep-registered", "data\\paper_positions.json"', text)

    def test_afternoon_looks_after_held_positions_without_orders(self):
        text = (ROOT / "start_trader_afternoon_session.ps1").read_text(encoding="utf-8-sig")
        branch = text[text.index('Write-Log "No paper orders today: $reason"'):]
        branch = branch[:branch.index("exit 0")]
        self.assertIn('"--keep-registered" "data\\paper_positions.json"', branch)
        self.assertIn("start_multi_day_monitor_today.ps1", branch)
        self.assertIn("alpaca_multi_day_monitor", text.split("$sessionWorkers =", 1)[1].splitlines()[0])


if __name__ == "__main__":
    unittest.main()
