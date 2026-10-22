# Exit lab findings - 2026-10-16 (ADR-001 step 1, first full run)

Run: `exit_lab.py`, 60,000 strategy-lab signals (up to 4,000 per strategy and side, 5 years of daily bars),
942,868 replays, 193 s. Report: `output/strategy_lab/exit_lab_latest.md` (+ `.json`).
Excess = result minus SPY over the same days (for a short: minus a short SPY). Research only; nothing changed in
the order path.

## What the numbers say

1. **Short side: holding to the end of the class (fixed) wins everywhere.** Every stop, signal exit or trailing
   stop is worse, significantly so from week upwards (e.g. gap_fade short quarter: fixed +8.50%, the other rules
   -6 to -8 points worse; overextension short quarter +7.41%, others -6 to -7.5 worse). Best class: quarter.
2. **Long side: no rule produces a positive edge.** Fixed is negative at month/quarter for most long strategies.
   Early exits are "better" there (gap_continuation long quarter: mr_signal +8.9 points over fixed) only because
   they cut exposure to a negative drift - the result is still negative (-0.75%).
3. The pattern is mechanical: stops and early exits shrink the holding time, so they help when the drift is
   negative and hurt when it is positive. This matches Kaminski & Lo (stops add value only with momentum in
   returns) and does not show timing skill in any rule.
4. Day class: differences are tiny (|0.0x-1.0| %); the ATR bracket is significantly worse in several cells.

## Caveats

- No random-entry baseline per rule yet: part of the short-side edge may be the general underperformance of the
  universe against SPY in 2021-2026, not the signal (the strategy lab's random baseline should be applied here).
- No borrow cost, no hard-to-borrow filter, no short-squeeze cap; entry at the signal close; daily bars only.
- The four original playbooks (support_bounce etc.) and the nine daily strategies the traders use are not in the
  lab signal file; this run covers the lab's strategies only.
- No per-year stability split yet.

## Second run: edge against a random entry, per-year stability, original playbooks

Run: 80,445 signals (lab + the four original playbooks from `strategy_lab.py --legacy`, 1,068 shortlist symbols,
every 5th session), 2,527,416 replays, 303 s. Each signal is paired with a random session of the same stock under
the same rule, class and side.

Real edge (significant, positive in more than half the years) **and** positive excess over SPY:

| strategy | side | class | rule | edge vs random | excess vs SPY | positive years |
|---|---|---|---|---|---|---|
| mean_reversion_overextension | short | quarter | fixed | +9.35 | +7.63 | 5/5 |
| gap_continuation | short | month | fixed | +2.93 | +2.22 | 5/5 |
| support_bounce_swing | long | quarter | fixed | +2.12 | +2.35 | 4/5 (2026 negative; 2022 carries most of it) |
| failed_breakout_reversal | short | quarter | fixed | +2.19 | +1.91 | 3/5 |
| gap_fade | short | week | fixed | +1.73 | +2.24 | 5/5 |

- The **best rule is always fixed**: no stop, trailing stop or signal exit gives a real edge anywhere. The holding
  period matters; the exit rule does not help.
- **Day class: no real edge** except overextension short (+1.2). support_bounce long (today's main playbook, held
  same-day under the ATR bracket) has no edge at day (-0.04) or week; its edge appears only at quarter.
- The four original playbooks' **short signals are one shared set** (3,609 of ~3,700 identical across the four:
  the traders' common short bias). Their edge vs random (+3.5 to +3.8) comes with a loss vs SPY (-1.6 to -1.9): they
  pick stocks that rose less than the stock usually did, which is not a profitable short.
- No stable edge: every other long, momentum_12_1 short, rsi2 short, compression short.
- Extra caveats for the playbooks: technical part only (neutral fundamentals), and today's shortlist used for the
  past (selection look-ahead). controlled_breakout long (213) and pre_run long (329) have small samples.

## Next steps (proposed, owner decides)

- Add a random-entry baseline per rule and class, and a per-year split.
- Run the same rules on the playbooks the traders actually use (shadow-trade ledger / 15-minute replay).
- No change to ADR Steps 2-3 from this run alone.
