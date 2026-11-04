"""The state of the market (owner 2026-11-04): read, explain, learn what works in it, and Koulis uses it."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np
import pandas as pd

import market_state as ms

DAYS = pd.bdate_range("2025-01-01", periods=320)


def closes(stock_drift: float = 0.001, iwm_drift: float = 0.002) -> pd.DataFrame:
    rng = np.random.default_rng(1)
    data = {"SPY": 100 * np.cumprod(1 + 0.001 + rng.normal(0, 0.003, len(DAYS))),
            "IWM": 100 * np.cumprod(1 + iwm_drift + rng.normal(0, 0.003, len(DAYS))),
            "QQQ": 100 * np.cumprod(1 + 0.001 + rng.normal(0, 0.003, len(DAYS)))}
    for k in range(30):
        data[f"S{k}"] = 50 * np.cumprod(1 + stock_drift + rng.normal(0, 0.004, len(DAYS)))
    return pd.DataFrame(data, index=DAYS)


class ReadTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(ms, "MIN_COMPANIES", 20)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_calm_rising_broad_market_with_small_companies_leading(self):
        state = ms.snapshot(ms.read_history(closes()))
        self.assertEqual((state["trend"], state["mood"], state["participation"], state["appetite"]),
                         ("up", "calm", "broad", "on"))
        self.assertEqual(state["session"], DAYS[-1].date().isoformat())

    def test_a_falling_crowd_under_a_rising_index_is_narrow_and_too_few_companies_is_unknown(self):
        history = ms.read_history(closes(stock_drift=-0.002, iwm_drift=-0.001))
        state = ms.snapshot(history)
        self.assertEqual((state["trend"], state["participation"], state["appetite"]), ("up", "narrow", "off"))
        with mock.patch.object(ms, "MIN_COMPANIES", 100):
            self.assertIsNone(ms.snapshot(ms.read_history(closes()))["participation"])   # unknown, not a guess
        self.assertIsNone(ms.snapshot(ms.read_history(pd.DataFrame({"X": [1.0]})))["trend"])

    def test_the_paragraph_is_greek_says_unknown_and_checks_yesterdays_reading(self):
        state = {"session": "2026-11-02", "trend": "down", "mood": "calm", "participation": None, "appetite": "neutral",
                 "rewards": "both", "figures": {"spy_vs_sma200_pct": 3.7, "up_1d_pct": 26.0}}
        text = ms.paragraph(state, yesterday={"rewards": "longs"}, agents={"long": 271, "short": 98})
        self.assertIn("Η αγορά (SPY) πέφτει", text)
        self.assertIn("Η συμμετοχή είναι άγνωστη", text)
        self.assertIn("λάθος ανάγνωση", text)
        self.assertIn("271 long και 98 short", text)


class EvidenceTests(unittest.TestCase):
    def history(self) -> pd.DataFrame:
        days = pd.bdate_range("2021-01-01", periods=900)
        frame = pd.DataFrame(index=days)
        frame["spy_close"] = 100.0
        up = (np.arange(len(days)) // 60) % 2 == 0
        frame["trend"] = np.where(up, "up", "down")
        for dim in ("mood", "participation", "appetite", "rewards"):
            frame[dim] = None
        return frame

    def test_a_strategy_that_loses_in_one_state_is_against_there_and_supported_in_the_other(self):
        history = self.history()
        rows = []
        for day, trend in history["trend"].items():
            for _ in range(3):
                rows.append({"strategy": "momentum_12_1", "side": "short", "date": day,
                             "x1": 0.0, "x5": 0.0, "x20": (-2.0 if trend == "up" else 2.0), "x60": 0.0})
        evidence = ms.build_evidence(pd.DataFrame(rows), history)
        cells = {(c["value"], c["horizon"]): c for c in evidence["cells"] if c["dimension"] == "trend"}
        self.assertEqual(cells[("up", 20)]["verdict"], "against")
        self.assertEqual(cells[("down", 20)]["verdict"], "supported")
        self.assertEqual(cells[("up", 5)]["verdict"], "unclear")                       # nothing there

        up = {"session": "2026-11-03", "trend": "up"}
        self.assertEqual(ms.fit(up, evidence, "momentum_12_1", "short", 15, date(2026, 11, 4))["fit"], "against")
        self.assertEqual(ms.fit({**up, "trend": "down"}, evidence, "momentum_12_1", "short", 20)["fit"], "supported")
        self.assertEqual(ms.fit(up, evidence, "momentum_12_1", "short", None)["fit"], "neutral")   # same-day: x1
        self.assertEqual(ms.fit(up, evidence, "gap_fade", "short", 20)["fit"], "neutral")
        self.assertEqual(ms.fit(None, evidence, "momentum_12_1", "short", 20)["fit"], "unknown")
        self.assertEqual(ms.fit(up, evidence, "momentum_12_1", "short", 20, date(2026, 11, 20))["fit"], "unknown")


class ContinuityTests(unittest.TestCase):
    def test_changes_since_yesterday_how_long_each_side_held_and_what_koulis_does_differently(self):
        history = pd.DataFrame({"trend": ["up", "up", "up", "down"], "mood": ["calm"] * 4,
                                "participation": [None, "narrow", "narrow", "narrow"], "appetite": ["off"] * 4,
                                "rewards": ["longs", "shorts", "shorts", "both"]}, index=DAYS[:4])
        moves = ms.changes(history)
        self.assertEqual((moves["trend"]["changed"], moves["trend"]["yesterday"], moves["trend"]["sessions"]),
                         (True, "up", 1))
        self.assertEqual((moves["participation"]["changed"], moves["participation"]["sessions"]), (False, 3))
        koulis = ms.fit_changes({"against": ["a short"], "supported": ["b long"]}, {"against": ["c long"]})
        self.assertEqual(koulis, {"now_against": ["a short"], "no_longer_against": ["c long"],
                                  "now_supported": ["b long"]})
        text = ms.change_sentence(moves, koulis)
        self.assertIn("τάση: από ανεβαίνει σε πέφτει", text)
        self.assertIn("Από σήμερα ο Koulis κρατά έξω: a short", text)

    def test_every_session_read_with_the_night_before_and_the_filter_judged_on_what_happened(self):
        history = pd.DataFrame({"spy_close": [1.0] * 3, "trend": ["up", "down", "down"], "mood": [None] * 3,
                                "participation": [None] * 3, "appetite": [None] * 3, "rewards": [None] * 3},
                               index=DAYS[:3])
        evidence = {"cells": [{"strategy": "gap_fade", "side": "long", "horizon": 1, "dimension": "trend",
                               "value": "up", "mean_return_pct": -1.0, "dates": 99, "verdict": "against"}]}
        day = DAYS[1].date().isoformat()
        records = [{"record_id": "t", "kind": "trade", "session_date": day, "playbook": "gap_fade", "direction": "long",
                    "holding_class": "day", "result_pct": -2.0},
                   {"record_id": "s", "kind": "shadow_candidate", "session_date": day, "playbook": "rsi2",
                    "direction": "short", "holding_class": "day"},
                   {"record_id": "u", "kind": "shadow_candidate", "session_date": day, "playbook": "rsi2",
                    "direction": "long", "holding_class": "day"}]                 # no move known: left out
        result = ms.review(history, evidence, records, {"s": 1.5})
        row = result["sessions"][0]
        self.assertEqual((row["trend"], row["longs_mean_pct"], row["shorts_mean_pct"]), ("up", -2.0, -1.5))
        self.assertEqual((result["by_fit"]["against"]["n"], result["by_fit"]["neutral"]["n"]), (1, 1))
        self.assertIn("Αν το φίλτρο υπήρχε από την αρχή", ms.review_markdown(result, ms.timeline(history)))


class KoulisTests(unittest.TestCase):
    def test_koulis_keeps_out_what_the_market_stands_against_and_puts_supported_first(self):
        import koulis_risk_gate
        import playbook_framework
        import trader_trade_plan

        root = Path(koulis_risk_gate.__file__).resolve().parent
        catalog = playbook_framework.catalog_by_id(playbook_framework.load_catalog(root / "playbook_catalog_v2.json"))
        policy = json.loads((root / "yankos_playbooks.json").read_text(encoding="utf-8"))["paper_sizing_policy"]

        def row(ticker, playbook, score):
            r = {"agent_name": "yankos", "ticker": ticker, "company": ticker, "trade_direction": "long",
                 "playbook_name": playbook, "signal_score": score, "signal_verdict": "candidate",
                 "instrument_type": "common", "risk_level": "Low", "fresh_primary_data": True,
                 "fundamental_packet_hash": "h", "fundamental_thesis_id": "t", "fundamental_thesis_hash": "th",
                 "fundamental_gate_status": "pass", "execution_eligible": True, "atr_14_pct": 3.0, "rationale": ["r"]}
            r["paper_trade_plan"] = trader_trade_plan.build_trade_plan("yankos", r, catalog[playbook], policy,
                                                                       "run_test", {})
            return r

        evidence = {"cells": [
            {"strategy": "gap_fade", "side": "long", "horizon": h, "dimension": "trend", "value": "up",
             "mean_return_pct": -1.0, "dates": 300, "verdict": "against"} for h in ms.HORIZONS] + [
            {"strategy": "support_bounce_swing", "side": "long", "horizon": h, "dimension": "trend", "value": "up",
             "mean_return_pct": 1.0, "dates": 300, "verdict": "supported"} for h in ms.HORIZONS]}
        today = pd.Timestamp.now("UTC").date().isoformat()
        payload = koulis_risk_gate.build_gate(
            [("yankos", {"run_id": "run_test", "agent_name": "yankos",
                         "results": [row("AAA", "gap_fade", 9.9), row("BBB", "support_bounce_swing", 7.1),
                                     row("CCC", "rsi2_mean_reversion", 9.5)]})],
            {"block_trader_run": False, "snapshot_before": {"positions": [], "open_orders": []}},
            run_id="run_test", min_score=7.0, max_total_orders=5, notional_per_trade=250.0,
            max_total_notional=5000.0, max_short_orders=2, notional_source="agent_suggestion",
            market={"state": {"session": today, "trend": "up"}, "evidence": evidence}, today=today)
        blocked = {b["ticker"]: b["block_reason"] for b in payload["blocked_intents"]}
        self.assertEqual(blocked.get("AAA"), "market_state_against")
        self.assertEqual([i["ticker"] for i in payload["approved_intents"]][:1], ["BBB"])   # supported first
        self.assertEqual(payload["market_state"]["trend"], "up")


class PriceStoreTests(unittest.TestCase):
    def test_a_session_without_its_close_is_not_stored(self):
        import price_history_store as phs

        index = pd.to_datetime(["2026-10-30", "2026-11-02"])
        columns = pd.MultiIndex.from_product([["SPY"], ["Open", "High", "Low", "Close", "Adj Close", "Volume"]])
        raw = pd.DataFrame([[1, 2, 0.5, 1.5, 1.5, 10], [1, 2, 0.5, None, None, 11]], index=index, columns=columns)
        fake = types.SimpleNamespace(download=lambda *a, **k: raw)
        with mock.patch.dict(sys.modules, {"yfinance": fake}):
            frames = phs._download(["SPY"], date(2026, 10, 1))
        self.assertEqual(list(frames["SPY"].index.strftime("%Y-%m-%d")), ["2026-10-30"])


class MainTests(unittest.TestCase):
    def test_nightly_run_writes_the_reading_and_reuses_unchanged_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            prices = folder / "prices"
            prices.mkdir()
            for name, series in closes().items():
                series.rename("Close").to_frame().rename_axis("Date").to_csv(prices / f"{name}.csv.gz",
                                                                             compression="gzip")
            with mock.patch.multiple(ms, ROOT=folder, PRICES=prices, LAB=folder / "lab", OUT=folder / "o.json",
                                     EVIDENCE=folder / "e.json", HISTORY=folder / "h.jsonl", REVIEW=folder / "r",
                                     MIN_COMPANIES=20):
                self.assertEqual(ms.main([]), 0)
                first = json.loads((folder / "e.json").read_text(encoding="utf-8"))["generated_at"]
                ms.main([])
                again = json.loads((folder / "e.json").read_text(encoding="utf-8"))["generated_at"]
            reading = json.loads((folder / "o.json").read_text(encoding="utf-8"))
        self.assertEqual(first, again)                                                    # the lab did not change
        self.assertEqual(reading["trend"], "up")
        self.assertIn("Η αγορά (SPY) ανεβαίνει", reading["paragraph"])
        self.assertIn("Καμία αλλαγή από χθες", reading["paragraph"])


if __name__ == "__main__":
    unittest.main()
