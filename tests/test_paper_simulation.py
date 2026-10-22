"""'Had we opened this position': entry as Miltos confirms it, exits as the monitors manage them, on 15-min bars."""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

import paper_learning as pl
import paper_simulation as sim
import paper_trade_ledger as ledger

SESSION = date(2026, 10, 15)
OPEN_UTC = datetime(2026, 10, 15, 13, 30, tzinfo=timezone.utc)  # 09:30 New York


def bars(*rows: tuple[float, float, float, float]) -> list[dict]:
    return [{"t": (OPEN_UTC + timedelta(minutes=15 * i)).isoformat().replace("+00:00", "Z"),
             "o": o, "h": h, "l": low, "c": c} for i, (o, h, low, c) in enumerate(rows)]


class SimulationTests(unittest.TestCase):
    def test_a_long_enters_on_confirmation_and_takes_profit(self):
        day = bars((100, 101, 99, 100), (100, 102, 99.5, 101.5), (101.5, 108, 101, 107.5), (107.5, 108, 107, 107))
        result = sim.simulate(day, SESSION, "long")
        self.assertTrue(result["entered"])                  # bar 2 closed above bar 1's high with a green body
        self.assertEqual((result["entry_price"], result["exit_reason"]), (101.5, "take_profit"))
        self.assertEqual(result["result_pct"], 6.0)

    def test_a_bar_touching_stop_and_take_profit_counts_as_the_stop(self):
        day = bars((100, 101, 99, 100), (100, 102, 99.5, 101.5), (101.5, 110, 95, 104))
        self.assertEqual(sim.simulate(day, SESSION, "long")["exit_reason"], "stop_loss")

    def test_without_confirmation_the_position_is_not_opened_but_the_forced_entry_is_measured(self):
        day = bars((100, 101, 99, 99.5), (99.5, 100, 98, 98.5), (98.5, 99, 97, 97.5))
        result = sim.simulate(day, SESSION, "long")
        self.assertFalse(result["entered"])
        self.assertNotIn("result_pct", result)
        self.assertAlmostEqual(result["forced_result_pct"], round((97.5 / 99.5 - 1) * 100, 3))

    def test_a_short_mirrors_the_long_and_too_few_bars_are_unknown(self):
        day = bars((100, 101, 99, 100), (100, 100.2, 98, 98.5), (98.5, 99, 97, 97.0))
        result = sim.simulate(day, SESSION, "short")
        self.assertEqual((result["entered"], result["exit_reason"]), (True, "end_of_day"))
        self.assertAlmostEqual(result["result_pct"], round(-(97.0 / 98.5 - 1) * 100, 3))
        self.assertIsNone(sim.simulate(day[:1], SESSION, "long"))

    def test_no_entry_after_15_00_new_york(self):
        late = [{"t": (OPEN_UTC + timedelta(minutes=330 + 15 * i)).isoformat(), "o": 100, "h": 101, "l": 99, "c": c}
                for i, c in enumerate((100, 102, 103))]  # bars from 15:00
        self.assertFalse(sim.simulate(late, SESSION, "long")["entered"])


class LearningWithSimulationTests(unittest.TestCase):
    def test_outcomes_use_the_simulated_trade_and_fidelity_compares_with_real_trades(self):
        record = lambda kind, ticker, result=None: {  # noqa: E731
            "record_id": f"{kind}{ticker}", "session_date": "2026-10-15", "kind": kind, "agent": "yankos",
            "ticker": ticker, "playbook": "p", "direction": "long", "result_pct": result}
        rows = [record("trade", "AAA", 5.5), record("shadow_candidate", "BBB"), record("shadow_candidate", "CCC"),
                record("approved_not_entered", "DDD")]
        confirm = bars((100, 101, 99, 100), (100, 102, 99.5, 101.5), (101.5, 108, 101, 107.5))
        flat = bars((100, 101, 99, 99.5), (99.5, 100, 98, 98.5), (98.5, 99, 97, 97.5))
        spy = bars((500, 501, 499, 500), (500, 501, 499, 500), (500, 506, 499, 505))
        table = {"AAA": confirm, "BBB": confirm, "CCC": flat, "DDD": flat, "SPY": spy}
        with tempfile.TemporaryDirectory() as folder, closing(ledger.connect(Path(folder) / "l.db")) as conn:
            self.assertEqual(pl.fill_simulations(conn, rows, lambda symbols, day: table), 4)
            pl.attach_counterfactuals(conn, rows)
        by = {r["ticker"]: r for r in rows}
        self.assertEqual((by["AAA"]["outcome_source"], by["AAA"]["outcome_pct"]), ("real", 5.5))
        self.assertEqual((by["BBB"]["outcome_source"], by["BBB"]["outcome_pct"]), ("simulated", 6.0))
        self.assertEqual((by["CCC"]["outcome_source"], by["CCC"]["outcome_pct"]), ("not_entered", None))
        self.assertEqual(by["DDD"]["outcome_source"], "simulated_forced_entry")
        self.assertEqual(by["BBB"]["excess_pct"], 5.0)                     # SPY +1% that session
        self.assertEqual(pl.simulation_fidelity([by["AAA"]]), {"pairs": 1, "mean_abs_gap_pct": 0.5, "same_sign_pct": 100.0})

    def test_each_position_keeps_its_own_trade_plan_limits(self):
        # PGY on 2026-10-15: its plan's take-profit was +9.45%, so a +6% move is no exit (the monitors held it)
        rows = [{"record_id": "pgy", "session_date": "2026-10-15", "kind": "shadow_candidate", "agent": "yankos",
                 "ticker": "PGY", "playbook": "p", "direction": "long", "stop_loss_pct": 0.054, "take_profit_pct": 0.0945}]
        day = bars((100, 101, 99, 100), (100, 102, 99.5, 101.5), (101.5, 108, 101, 107.5), (107.5, 108, 107, 107.6))
        spy = bars((500, 501, 499, 500), (500, 501, 499, 500), (500, 501, 499, 500), (500, 501, 499, 500))
        with tempfile.TemporaryDirectory() as folder, closing(ledger.connect(Path(folder) / "l.db")) as conn:
            pl.fill_simulations(conn, rows, lambda symbols, d: {"PGY": day, "SPY": spy})
            pl.attach_counterfactuals(conn, rows)
        self.assertEqual(rows[0]["simulation"]["exit_reason"], "end_of_day")
        self.assertEqual(rows[0]["simulation"]["take"], 0.0945)


if __name__ == "__main__":
    unittest.main()
