"""T8 intraday lab: returns per entry time and holding period, paired edge, FDR (no network)."""
from __future__ import annotations

from datetime import date
import unittest

import numpy as np
import pandas as pd

import intraday_lab as il


def day_bars(day: str, start_price: float, step: float) -> list[dict]:
    out, price = [], start_price
    for k in range(26):
        minutes = 9 * 60 + 30 + 15 * k
        stamp = f"{day} {minutes // 60:02d}:{minutes % 60:02d}"
        out.append({"t": stamp, "d": stamp, "o": price, "c": price + step})
        price += step
    return out


class ReturnTests(unittest.TestCase):
    def test_entry_at_the_bar_open_exit_at_the_last_bar_close(self):
        bars = day_bars("2026-10-26", 100.0, 1.0) + day_bars("2026-10-27", 126.0, 0.0)
        r = il.returns(bars, date(2026, 10, 26), long=True)
        self.assertAlmostEqual(r[("09:30", 15)], 1.0)            # 100 -> 101
        self.assertAlmostEqual(r[("09:30", 60)], 4.0)            # 4 bars
        self.assertAlmostEqual(r[("09:30", "close")], 26.0)      # to the day's last close
        self.assertAlmostEqual(r[("15:00", 390)], (126 / 122 - 1) * 100)  # into the next session
        self.assertNotIn(("15:00", 120), r)                      # would end after the close: not counted
        short = il.returns(bars, date(2026, 10, 26), long=False)
        self.assertAlmostEqual(short[("09:30", 15)], -1.0)

    def test_missing_entry_bar_is_skipped(self):
        bars = [b for b in day_bars("2026-10-26", 100.0, 1.0) if not b["d"].endswith("10:00")]
        self.assertNotIn(("10:00", 15), il.returns(bars, date(2026, 10, 26), long=True))


class SummaryTests(unittest.TestCase):
    def test_a_real_edge_passes_and_noise_does_not(self):
        rows = []
        gen = np.random.default_rng(1)
        for k in range(600):
            month = f"2026-{k % 12 + 1:02d}"
            noise = gen.normal(0, 1)
            rows.append(("good", "long", "10:00", "60", month, 1.0 + noise, noise, 1.0))
            rows.append(("plain", "long", "10:00", "60", month, noise, gen.normal(0, 1), noise - gen.normal(0, 1)))
        frame = pd.DataFrame(rows, columns=["strategy", "side", "entry", "hold", "month", "signal", "random", "edge"])
        verdicts = {e["strategy"]: e["verdict"] for e in il.summarize(frame)}
        self.assertEqual(verdicts, {"good": "edge", "plain": "no clear edge"})


if __name__ == "__main__":
    unittest.main()
