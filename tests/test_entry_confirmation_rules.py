"""Miltos's confirmation on the consolidated tape with participation, momentum and a real-time check (owner
2026-10-24, the AMSF short of 10/23); no limit on new orders; one positions call per monitor cycle."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import alpaca_paper_position_monitor as monitor
import holding_horizon
import entry_confirmation_rules as rules
import koulis_risk_gate
import playbook_framework
import trader_trade_plan

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2026, 10, 23, 13, 30, tzinfo=timezone.utc)


def parse(stamp):
    return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")) if stamp else None


def bar(k, o, h, low, c, v, minutes=15):
    return {"t": (START + timedelta(minutes=minutes * k)).isoformat(), "o": o, "h": h, "l": low, "c": c, "v": v}


def falling_session(trigger_volume=3000, trigger_close=95.0):
    """24 bars drifting down with volume 1000, then a red break on ``trigger_volume``."""
    bars = [bar(k, 120 - k, 120.5 - k, 119 - k, 119.2 - k, 1000) for k in range(24)]
    bars.append(bar(24, 96.5, 96.6, trigger_close - 0.1, trigger_close, trigger_volume))
    return bars


def minutes_after(bars, closes, volume=100):
    end = parse(bars[-1]["t"]) + timedelta(minutes=15)
    early = [{"t": (START + timedelta(minutes=k)).isoformat(), "o": 100, "h": 100, "l": 100, "c": 100, "v": volume}
             for k in range(30)]
    return early + [{"t": (end + timedelta(minutes=k)).isoformat(), "o": c, "h": c, "l": c, "c": c, "v": volume}
                    for k, c in enumerate(closes)]


class ConfirmationTests(unittest.TestCase):
    def evaluate(self, direction, bars, minute_bars):
        return rules.evaluate(direction, bars, minute_bars, session_bars=bars, parse=parse)

    def test_clean_short_break_is_confirmed(self):
        bars = falling_session()
        result = self.evaluate("short", bars, minutes_after(bars, [94.9, 94.8]))
        self.assertTrue(result["confirmed"], result["reason"])
        self.assertEqual(result["checks"]["volume_x_average"], 3.0)
        self.assertLess(result["checks"]["rsi14_15m"], 50)

    def test_amsf_case_thin_break_waits(self):
        # the break on a fraction of the usual volume (413 IEX shares on 10/23) is not a break
        bars = falling_session(trigger_volume=400)
        result = self.evaluate("short", bars, minutes_after(bars, [94.9]))
        self.assertFalse(result["confirmed"])
        self.assertFalse(result["checks"]["participation"])

    def test_reclaim_or_green_surge_against_the_short_waits(self):
        bars = falling_session()
        reclaimed = self.evaluate("short", bars, minutes_after(bars, [95.2, 95.4]))
        self.assertFalse(reclaimed["checks"]["holding_now"])
        surge = self.evaluate("short", bars, minutes_after(bars, [94.5, 94.9], volume=100)[:-2]
                              + [{"t": (parse(bars[-1]["t"]) + timedelta(minutes=15 + k)).isoformat(), "o": c,
                                  "h": c, "l": c, "c": c, "v": 900} for k, c in enumerate([94.5, 94.95])])
        self.assertTrue(surge["checks"]["surge_against"])
        self.assertFalse(surge["confirmed"])

    def test_neutral_momentum_waits(self):
        flat = [bar(k, 100, 100.5, 99.5, 100 + (0.3 if k % 2 else -0.3), 1000) for k in range(24)]
        flat.append(bar(24, 100.2, 100.3, 99.0, 99.1, 3000))
        result = self.evaluate("short", flat, minutes_after(flat, [99.0]))
        self.assertTrue(result["checks"]["trigger"])
        self.assertIsNotNone(result["checks"]["rsi14_15m"])

    def test_unknown_is_never_a_pass(self):
        bars = falling_session()
        no_realtime = self.evaluate("short", bars, None)
        self.assertIsNone(no_realtime["checks"]["holding_now"])
        self.assertFalse(no_realtime["confirmed"])
        short_history = bars[-5:]
        thin = self.evaluate("short", short_history, minutes_after(short_history, [94.9]))
        self.assertIsNone(thin["checks"]["participation"])
        self.assertFalse(thin["confirmed"])

    def test_only_completed_bars_count(self):
        bars = falling_session()
        cut = parse(bars[-1]["t"]) + timedelta(minutes=10)  # the last bar is still forming
        self.assertEqual(len(rules.completed(bars, data_end=cut, minutes=15, parse=parse)), len(bars) - 1)

    def test_rsi_matches_wilder(self):
        self.assertEqual(rules.rsi([1.0] * 10), None)
        self.assertEqual(round(rules.rsi([float(x) for x in range(1, 20)]), 1), 100.0)


class NoLimitTests(unittest.TestCase):
    def test_owner_choice_no_limit_on_new_orders(self):
        self.assertEqual(koulis_risk_gate.new_orders_limit_mode(), "none")
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(koulis_risk_gate.new_orders_limit_mode(Path(folder) / "missing.json"), "limits")

    def test_no_limit_approves_every_eligible_intent(self):
        from tests.test_risk_gate import signal, strategy
        rows = [signal("yankos", f"T{i}", "short" if i % 2 else "long", 8.0, playbook="p") for i in range(40)]
        payload = koulis_risk_gate.build_gate(
            [("yankos", strategy("yankos", rows))],
            {"block_trader_run": False, "snapshot_before": {"positions": [], "open_orders": []}},
            run_id="run_test", min_score=7.0, max_total_orders=koulis_risk_gate.NO_LIMIT_ORDERS,
            notional_per_trade=250.0, max_total_notional=koulis_risk_gate.NO_LIMIT_NOTIONAL,
            max_short_orders=koulis_risk_gate.NO_LIMIT_ORDERS, max_per_strategy=0)
        self.assertEqual(payload["approved_count"], 40)
        json.loads(json.dumps(payload))  # standard JSON: no Infinity for PowerShell


class MonitorBatchTests(unittest.TestCase):
    def test_one_positions_call_and_errors_are_not_empty(self):
        with mock.patch.object(monitor, "alpaca_request", return_value=[{"symbol": "ABC", "qty": "1"}]) as call:
            self.assertEqual(set(monitor.fetch_positions()), {"ABC"})
            call.assert_called_once_with("GET", "/v2/positions")
        with mock.patch.object(monitor, "alpaca_request", return_value={"message": "error"}):
            with self.assertRaises(RuntimeError):
                monitor.fetch_positions()

    def test_filled_entry_is_not_fetched_again(self):
        filled = {"order_response": {"id": "x", "status": "filled", "filled_at": "2026-10-23T14:00:00Z"}}
        with mock.patch.object(monitor, "fetch_order_by_id") as fetch:
            self.assertEqual(monitor.refreshed_order(filled)["status"], "filled")
            fetch.assert_not_called()


class ShortGateTests(unittest.TestCase):
    TODAY = "2026-10-26"

    def data(self, **facts):
        import alpaca_short_gate_data as sg
        base = {"symbol": "ABC", "shortable": True, "easy_to_borrow": True}
        asset = {**base, **{k: v for k, v in facts.items() if k in ("shortable", "easy_to_borrow")}}
        row = {"ticker": "ABC", "short_percent_of_float": facts.get("short_float", 0.05),
               "shares_outstanding": facts.get("shares", 50_000_000)}
        return sg, sg.build([asset], [row], self.TODAY, info=lambda symbol: {})

    GATES = ["borrow_carry", "shortability", "squeeze_buyback_low_float_risk"]

    def test_all_gates_resolved_for_an_easy_liquid_stock(self):
        sg, data = self.data()
        resolved, remaining = sg.resolve(self.GATES, data, "ABC", self.TODAY)
        self.assertEqual((sorted(resolved), remaining), (sorted(self.GATES), []))

    def test_hard_to_borrow_crowded_or_small_float_stays_blocked(self):
        for facts, gate in (({"easy_to_borrow": False}, "borrow_carry"), ({"shortable": False}, "shortability"),
                            ({"short_float": 0.25}, "squeeze_buyback_low_float_risk"),
                            ({"shares": 5_000_000}, "squeeze_buyback_low_float_risk"),
                            ({"short_float": None}, "squeeze_buyback_low_float_risk")):
            sg, data = self.data(**facts)
            self.assertIn(gate, sg.resolve(self.GATES, data, "ABC", self.TODAY)[1], facts)

    def test_old_data_or_unknown_symbol_resolves_nothing(self):
        sg, data = self.data()
        self.assertEqual(sg.resolve(self.GATES, data, "ABC", "2026-10-27")[1], self.GATES)
        self.assertEqual(sg.resolve(self.GATES, data, "XYZ", self.TODAY)[1], self.GATES)
        self.assertEqual(sg.resolve(self.GATES, None, "ABC", self.TODAY)[1], self.GATES)

    def test_koulis_approves_and_thymios_accepts_a_resolved_lab_short(self):
        from datetime import date
        from thymios_order_intent import IntentValidationError, validate_intent_payload
        catalog = playbook_framework.load_catalog(ROOT / "playbook_catalog_v2.json")
        policy = json.loads((ROOT / "yankos_playbooks.json").read_text(encoding="utf-8"))["paper_sizing_policy"]
        row = {"agent_name": "yankos", "ticker": "ABC", "company": "Abc", "trade_direction": "short",
               "playbook_name": "gap_fade", "signal_score": 9.0, "signal_verdict": "candidate",
               "instrument_type": "common", "risk_level": "Low", "fresh_primary_data": True,
               "fundamental_packet_hash": "h", "fundamental_thesis_id": "t", "fundamental_thesis_hash": "th",
               "fundamental_gate_status": "pass", "execution_eligible": True, "atr_14_pct": 3.0, "rationale": ["r"]}
        row["paper_trade_plan"] = trader_trade_plan.build_trade_plan(
            "yankos", row, playbook_framework.catalog_by_id(catalog)["gap_fade"], policy, "run_test", {})
        self.assertTrue(row["paper_trade_plan"]["unresolved_short_gates_at_signal"])
        today = date.today().isoformat()
        sg, data = self.data()
        data["as_of"] = today

        def gate(short_data):
            return koulis_risk_gate.build_gate(
                [("yankos", {"run_id": "run_test", "agent_name": "yankos", "results": [row]})],
                {"block_trader_run": False, "snapshot_before": {"positions": [], "open_orders": []}},
                run_id="run_test", min_score=7.0, max_total_orders=5, notional_per_trade=250.0,
                max_total_notional=5000.0, max_short_orders=2, notional_source="agent_suggestion",
                short_gate_data=short_data, today=today)

        blocked = gate(None)
        self.assertEqual(blocked["approved_count"], 0)
        self.assertTrue(blocked["blocked_intents"][0]["block_reason"].startswith("unresolved_short_implementation_gates"))
        approved = gate(data)
        self.assertEqual(approved["approved_count"], 1)
        validate_intent_payload(approved)
        tampered = json.loads(json.dumps(approved))
        tampered["approved_intents"][0]["short_gate_resolution"]["resolved"].pop("borrow_carry")
        with self.assertRaisesRegex(IntentValidationError, "SHORT gates"):
            validate_intent_payload(tampered)

    def test_lab_strategies_are_in_the_catalog_with_a_horizon(self):
        catalog = playbook_framework.catalog_by_id(playbook_framework.load_catalog(ROOT / "playbook_catalog_v2.json"))
        for name in ("momentum_12_1", "high_52w_breakout", "rsi2_mean_reversion", "volatility_compression_breakout",
                     "gap_continuation", "gap_fade"):
            self.assertIn(name, catalog)
        policy = json.loads((ROOT / "yankos_playbooks.json").read_text(encoding="utf-8"))["paper_sizing_policy"]
        curve = {("gap_fade", "short"): [{"sessions": 5, "edge_pct": 1.9, "edge_ci": [1, 2], "years": "5/5", "n": 1}]}
        row = {"ticker": "GAP", "trade_direction": "short", "playbook_name": "gap_fade", "signal_score": 9.0,
               "signal_verdict": "candidate", "risk_level": "Low", "atr_14_pct": 3.0, "rationale": ["r"]}
        plan = trader_trade_plan.build_trade_plan("yankos", row, catalog["gap_fade"], policy, "run_test", curve)
        self.assertEqual((plan["holding_period_rule"], plan["holding_horizon"]["sessions"]), (holding_horizon.RULE, 5))


if __name__ == "__main__":
    unittest.main()
