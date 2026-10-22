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
            self.assertIn("Miltos: δεν υπάρχει σημερινή εγγραφή", text)

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


if __name__ == "__main__":
    unittest.main()
