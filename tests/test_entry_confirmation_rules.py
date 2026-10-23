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
import entry_confirmation_rules as rules
import koulis_risk_gate

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


if __name__ == "__main__":
    unittest.main()
