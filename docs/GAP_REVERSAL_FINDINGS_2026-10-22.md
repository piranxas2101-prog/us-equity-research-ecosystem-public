# Gap reversal lab findings - 2026-10-22

Question (owner): the ideas taken down after big opening gaps (ISRG, NFLX 2026-10-21; ANET, TSM, VRT 2026-10-15)
bounced during the session - an edge worth a strategy? `gap_reversal_lab.py`, 4,395 stocks, 85,335 gap events
(open beyond the previous close by >= 1 x ATR(14)), fade entered at the open, held 1 / 5 / 20 sessions, against a
random entry in the same stock, month-block bootstrap, FDR q <= 0.10, per-year stability. Report:
`output/strategy_lab/gap_reversal_lab_latest.md`.

1. **Buying a gap down does not pay** (no stable edge for any size on the day; 2-3 ATR gaps keep falling over a
   week and a month: -0.69 / -1.17 points against a random entry, 0-1 positive years of 6). The bounces seen in the
   paper ledger are not a five-year edge on the raw open-to-close; they came with Miltos's 15-minute confirmation,
   which daily bars cannot test, and from a handful of days.
2. **Fading a gap up (short) has a stable edge**: >= 3 ATR +0.56 on the day (5/6 years) and +2.09 over a month;
   1-2 ATR +0.47 over a week (6/6); 2-3 ATR +1.50 over a month. Consistent with the lab's gap_fade short. No borrow
   cost or squeeze cap in this test.
3. **Gaps on high-volume days continue** (descriptive only: the day's volume is not known at the open): a gap down
   on >= 2.5 x volume kept falling (-1.8 to -2.2 points against random, 0-1 of 6 years). This supports the pre-open
   check taking down longs after results with a gap against them.

Consequences (owner decides): no "buy the gap down" strategy; the recheck at the open keeps measuring the
gap-only cases (VRTX 2026-10-21: the pre-market price misled) in `preopen_recheck.py`; the gap-up fade is already
covered by gap_fade short.
