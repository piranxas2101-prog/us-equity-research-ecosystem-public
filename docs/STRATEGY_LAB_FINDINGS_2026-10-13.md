# Strategy lab findings (2026-10-13) — notes for the owner's decisions

Owner's instruction: keep these as notes; decide changes in the evening after seeing how the pre-open run works.
Nothing here is applied. Source: `output/strategy_lab/strategy_lab_latest.json` (9 daily strategies, 5,312 symbols,
1.9M signals, 2021-2026), `strategy_lab_legacy_latest.json` (4 original playbooks, current shortlist, every 5th
session) and the statistical review in `output/strategy_lab/analysis/` (heatmaps + `lab_statistics.json`).

Method of the review: edge = share of signals beating SPY minus the share of random entries (same liquid stocks,
direction, horizon, year) beating SPY; 95% interval from a month-block bootstrap (signals of one day are not
independent); "stable" = positive in at least 2 of 3 years with 100+ signals. 60 tests: about 3 may pass by chance,
so borderline results (interval starting at ~0) are weak.

## Real and stable edge

| Strategy · side | Holding class | Edge (points) |
|---|---|---|
| gap_fade · short | every class, every year | +3.3 day, +5.7 week, +5.2 month, +6.6 quarter |
| mean_reversion_overextension · short | day, month, quarter (all 5 years) | +1.9, +4.1, +5.5 |
| momentum_12_1 · long | quarter (lost only in 2022) | +2.7 |
| rsi2_mean_reversion · long / short | day | +1.6 / +2.0 |
| momentum_12_1 · long / short | day | +1.1 / +1.1 (short is borderline) |
| gap_continuation · short | week | +1.8 (borderline) |
| failed_breakdown_reclaim · long | month | +2.3 (borderline) |

## Really negative

- gap_continuation · long and gap_fade · long: -3.6 to -6.5 in every class, almost every year.
- volatility_compression_breakout · long, day class: -1.8.

## Uncertain (cannot be told from chance yet)

Everything else, including mean_reversion_overextension · long (looked bad in the first table, not proven),
high_52w_breakout, failed_breakout_reversal, volatility_compression_breakout short.

## Four original playbooks (technical part only)

All four long sides fit the quarter best: support_bounce +3.1 (day +0.4), controlled_breakout +2.3, early_accumulation
+1.8, pre_run_breakout +1.1. Positions are currently closed the same day, i.e. in their weakest class. Their short
side comes from one shared weak-trend rule per trader and shows no edge.

## Market states

2021-2026 had 866 sessions up_calm, 3 up_volatile, 118 down_calm, 70 down_volatile: nothing reliable yet for the
difficult states.

## Candidate decisions (owner, after the pre-open week)

1. Holding classes: day = rsi2 (both), momentum; week = gap_fade short; month/quarter = overextension short,
   gap_fade short, momentum long and the four original playbooks' long sides.
2. The three "dangerous longs": the data support switching off only gap_continuation long and gap_fade long;
   overextension long is uncertain. Owner chose B (keep all, judge from paper trades) on 2026-10-12.
3. Same-day flat contradicts every original playbook's best class; revisit with the holding-class rule.

Caveats: survivorship (only today's listed companies), no fundamental gate in the lab, mostly a rising market.
