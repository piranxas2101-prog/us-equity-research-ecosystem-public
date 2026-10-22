"""Recheck at the open: today's opening price only; log mode changes no file; on mode restores the nightly verdict;
the launcher runs it after the open and before the orders."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

import preopen_open_recheck as por

TODAY = date(2026, 10, 21)
GAP = "Pre-market gap -2.85% against the long trade is larger than 1x the 14-day ATR (2.39%)."
OFFICER = "New SEC event after the last close (8-K item 5.02); the fundamental review has not read it."


def demoted(ticker, reasons, close=514.65, atr=2.39):
    return {"ticker": ticker, "playbook_name": "support_bounce_swing", "trade_direction": "long",
            "signal_verdict": "watch", "nightly_signal_verdict": "candidate",
            "preopen_check": {"decision": "demoted_to_watch", "demote_reasons": reasons, "last_close": close,
                              "atr_14_pct": atr, "gap_pct": -2.845}}


def snapshot(price, stamp="2026-10-21T13:30:00Z"):
    return {"dailyBar": {"o": price, "t": stamp}}


class PriceTests(unittest.TestCase):
    def test_only_todays_daily_bar_counts(self):
        self.assertEqual(por.opening_price(snapshot(511.7), TODAY), 511.7)
        self.assertIsNone(por.opening_price(snapshot(511.7, "2026-10-20T13:30:00Z"), TODAY))
        self.assertIsNone(por.opening_price({}, TODAY))


class RecheckTests(unittest.TestCase):
    def payload(self):
        return {"run_id": "r", "results": [demoted("VRTX", [GAP, OFFICER]), demoted("KARO", [GAP], close=100.0, atr=1.15),
                                            {"ticker": "AAA", "signal_verdict": "candidate",
                                             "preopen_check": {"decision": "kept"}}]}

    def test_vrtx_comes_back_and_a_gap_still_against_stays(self):
        opens = {"VRTX": 511.70, "KARO": 95.4}  # VRTX -0.57% (0.24 ATR); KARO -4.6% (4 ATR)
        new, decisions = por.recheck_payload(self.payload(), opens, restore=True, checked_at="t")
        by = {d["ticker"]: d["decision"] for d in decisions}
        self.assertEqual(by, {"VRTX": "restored", "KARO": "stays_watch"})
        verdicts = {r["ticker"]: r["signal_verdict"] for r in new["results"]}
        self.assertEqual(verdicts, {"VRTX": "candidate", "KARO": "watch", "AAA": "candidate"})

    def test_log_mode_keeps_the_verdicts(self):
        new, _ = por.recheck_payload(self.payload(), {"VRTX": 511.70}, restore=False, checked_at="t")
        self.assertEqual(new["results"][0]["signal_verdict"], "watch")
        self.assertEqual(new["results"][0]["open_recheck"]["decision"], "restored")

    def test_run_writes_files_only_in_on_mode_and_unknown_prices_stay(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            for agent in ("yankos", "periklis"):
                (out / f"{agent}_strategy_preopen_latest.json").write_text(json.dumps(self.payload()), encoding="utf-8")
            fetch = lambda tickers: {"VRTX": snapshot(511.70)}  # noqa: E731 - KARO's open not read
            report = por.run(out, out, fetch, TODAY, "log")
            self.assertEqual((report["demoted"], report["restored"], report["files_changed"]), (4, 2, False))
            stored = json.loads((out / "yankos_strategy_preopen_latest.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["results"][0]["signal_verdict"], "watch")
            report = por.run(out, out, fetch, TODAY, "on")
            stored = json.loads((out / "yankos_strategy_preopen_latest.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["results"][0]["signal_verdict"], "candidate")
            self.assertEqual(stored["results"][1]["signal_verdict"], "watch")  # KARO: no opening price


class SwitchTests(unittest.TestCase):
    def test_the_switch_and_the_launcher_step(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "c.json"
            path.write_text('{"preopen": {"recheck_at_open": "log"}}', encoding="utf-8")
            self.assertEqual(por.mode(path), "log")
            path.write_text('{"preopen": {"recheck_at_open": "maybe"}}', encoding="utf-8")
            self.assertEqual(por.mode(path), "off")
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(por.mode(root / "scanner_config.json"), "on")  # owner 2026-10-22: on from that day
        text = (root / "start_trader_afternoon_session.ps1").read_text(encoding="utf-8")
        # the days without orders also wait for 09:35 (held positions, 2026-10-23): the order step's own wait counts
        recheck = text.index('"preopen_open_recheck.py"')
        orders = text.index(r'-File ".\start_trader_paper_trading_today.ps1"')
        self.assertLess(text.index("Wait-Eastern 9 32"), recheck)
        self.assertLess(recheck, text.index("Wait-Eastern 9 35", recheck))
        self.assertLess(text.index("Wait-Eastern 9 35", recheck), orders)


if __name__ == "__main__":
    unittest.main()
