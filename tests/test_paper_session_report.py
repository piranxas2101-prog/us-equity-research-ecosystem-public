"""After-close session report: only today's files count, closed positions are summed, unknown SPY stays unknown."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

import paper_session_report as psr

TODAY = date(2026, 10, 13)


def write(folder: Path, name: str, payload: dict) -> None:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class SessionReportTests(unittest.TestCase):
    def test_todays_files_are_summed_and_old_ones_reported_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            write(out, "koulis_order_intents_latest.json", {"generated_at": "2026-10-13T13:35:00+00:00",
                  "approved_count": 2, "approved_notional_usd": 500, "approved_intents": [
                      {"agent": "yankos", "ticker": "AAA", "trade_direction": "long"}]})
            write(out, "yankos_position_monitor_latest.json", {"run_timestamp": "2026-10-13T22:55:00+03:00", "actions": [
                {"ticker": "AAA", "trade_direction": "long", "action_type": "close_position", "position_plpc": 0.02},
                {"ticker": "BBB", "trade_direction": "short", "action_type": "close_position", "position_plpc": -0.01}]})
            write(out, "miltos_entry_stalker_latest.json", {"run_timestamp": "2026-09-02T18:51:45+03:00"})
            report = psr.build(out, TODAY, spy_move=0.5)
            self.assertEqual(report["koulis"]["approved_count"], 2)
            self.assertIsNone(report["miltos"])                     # an old file is missing, not zero activity
            self.assertEqual(report["day_result"]["average_result_pct"], 0.5)
            excess = {c["ticker"]: c["approx_excess_vs_spy_pct"] for c in report["closed_positions"]}
            self.assertEqual(excess, {"AAA": 1.5, "BBB": -0.5})     # a short gains when SPY falls, so SPY is added
            text = psr.render(report)
            self.assertIn("Δεν υπάρχει σημερινός έλεγχος", text)
            self.assertIn("Miltos (όλη η μέρα): δεν υπάρχει σημερινή εγγραφή", text)

    def test_a_position_closed_mid_session_comes_from_the_journal(self):
        import alpaca_paper_position_monitor as monitor

        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            journal_dir = out / "paper_session"
            # mid-session cycle: ASND closed by a stop; the last cycle (in *_latest) only has the end-of-day closes
            monitor.journal_actions({"agent_name": "yankos", "run_timestamp": "2026-10-13T19:10:00+03:00", "actions": [
                {"ticker": "ASND", "trade_direction": "long", "action_type": "close_position", "position_plpc": -0.03,
                 "action_status": "filled"}]}, journal_dir)
            monitor.journal_actions({"agent_name": "yankos", "run_timestamp": "2026-10-13T19:11:00+03:00",
                                     "actions": []}, journal_dir)
            last = {"run_timestamp": "2026-10-13T22:55:00+03:00", "agent_name": "yankos", "actions": [
                {"ticker": "PGY", "trade_direction": "long", "action_type": "close_position", "position_plpc": 0.066,
                 "action_status": "filled"}]}
            monitor.journal_actions(last, journal_dir)
            write(out, "yankos_position_monitor_latest.json", last)
            write(out, "miltos_entry_stalker_latest.json", {"run_timestamp": "2026-10-13T22:02:00+03:00",
                                                             "entry_window_finished": True, "skipped_count": 2})
            report = psr.build(out, TODAY, spy_move=0.0)
            results = {c["ticker"]: c["result_pct"] for c in report["closed_positions"]}
            self.assertEqual(results, {"ASND": -3.0, "PGY": 6.6})          # PGY once, though journal + latest
            self.assertEqual(report["miltos"]["status"], "entry window finished")

    def test_unknown_spy_keeps_excess_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            write(out, "periklis_position_monitor_latest.json", {"run_timestamp": "2026-10-13T22:55:00+03:00", "actions": [
                {"ticker": "CCC", "trade_direction": "long", "action_type": "close_position", "position_plpc": 0.01}]})
            report = psr.build(out, TODAY, spy_move=None)
            self.assertIsNone(report["closed_positions"][0]["approx_excess_vs_spy_pct"])
            self.assertIn("άγνωστο", psr.render(report))


class WholeDayTests(unittest.TestCase):
    """T7 (2026-10-26): the whole day, not the last cycle."""

    def test_entries_by_who_and_when_strategies_held_and_waiting_reasons(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            out = root / "output"
            write(out, "yankos_paper_trades_latest.json", {"run_timestamp": "2026-10-13T16:40:00+03:00", "openings": [
                {"ticker": "AAA", "playbook_name": "support_bounce_swing", "trade_direction": "long",
                 "order_status": "filled", "miltos_activated_at": "2026-10-13T18:02:00+03:00",
                 "order_response": {"filled_at": "2026-10-13T15:02:00Z"},
                 "paper_trade_plan": {"holding_period_rule": "agent_horizon", "holding_horizon": {"sessions": 60}}},
                {"ticker": "BBB", "playbook_name": "momentum_12_1", "trade_direction": "long", "order_status": "filled",
                 "order_response": {"filled_at": "2026-10-13T13:40:00Z"}, "paper_trade_plan": {}},
                {"ticker": "CCC", "playbook_name": "momentum_12_1", "trade_direction": "long",
                 "order_status": "waiting_confirmation",
                 "confirmation_checks": {"trigger": True, "participation": False, "momentum": True, "holding_now": None}}]})
            write(out, "yankos_position_monitor_latest.json", {"run_timestamp": "2026-10-13T22:55:00+03:00", "actions": [
                {"ticker": "BBB", "trade_direction": "long", "action_type": "close_position", "position_plpc": -0.01}]})
            write(out, "multi_day_monitor_latest.json", {"run_timestamp": "2026-10-13T22:58:00+03:00", "holds": [
                {"ticker": "AAA", "horizon_sessions": 60, "sessions_held": 1, "position_plpc": 0.004, "stop_pct": 0.022}]})
            write(root / "data", "paper_positions.json", {"AAA": {"agent": "yankos", "playbook": "support_bounce_swing",
                                                                 "direction": "long", "expires_at": "2027-01-11T11:02:00-05:00"}})
            report = psr.build(out, TODAY, spy_move=0.2)
        e = report["entries"]
        self.assertEqual((e["total"], e["by_miltos"], e["by_trader_at_start"], e["held_on_horizon"]), (2, 1, 1, 1))
        self.assertEqual(e["by_hour_ny"], {"09": 1, "11": 1})
        self.assertEqual(report["waiting_reasons"], {"participation": 1})
        self.assertEqual(report["closed_positions"][0]["playbook"], "momentum_12_1")
        self.assertEqual(report["per_strategy"][0]["closed"], 1)
        held = report["held_positions"][0]
        self.assertEqual((held["agent"], held["result_pct"], held["stop_pct"], held["expires"]),
                         ("yankos", 0.4, 2.2, "2027-01-11"))
        text = psr.render(report)
        self.assertIn("Θέσεις που κρατάμε", text)
        self.assertIn("χαμηλός όγκος 1", text)


if __name__ == "__main__":
    unittest.main()
