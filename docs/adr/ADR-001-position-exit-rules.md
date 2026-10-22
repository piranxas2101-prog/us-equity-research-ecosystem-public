# ADR-001: Position exit rules per strategy family

**Status:** Proposed
**Date:** 2026-10-16
**Deciders:** the owner. Risk rules change only with his approval, a documented rationale and focused tests (CLAUDE.md).

## Context

The position monitors (`alpaca_paper_position_monitor.py`, decision rules in `alpaca_paper_exit_manager.py`) check
every open paper position every 2 minutes and close it on:

1. **Stop-loss / take-profit** from the position's trade plan (`trader_trade_plan.py`): stop = 1.1 x ATR(14)
   clamped to 1.5%-7.5%, take-profit = 1.75 x stop clamped to 2.5%-12%. Only the four original playbooks get a plan;
   the nine daily strategies fall back to the launcher's fixed 3% / 6%.
2. **Signal change**: the trader now rejects the name, flips direction, downgrades it (optional) or its score falls
   below a floor.
3. **High risk** without candidate conviction.
4. **Same-day flat** at 15:55 New York: every position is a day position.

Forces at play:

- **One exit for every strategy.** Mean-reversion strategies (rsi2, overextension, gap fade, failed
  breakout/breakdown, support bounce) earn when price snaps back; momentum/breakout strategies (momentum 12-1,
  52-week breakout, compression, controlled breakout) earn by holding the move. The literature points to different
  exits: a signal exit plus a time stop for mean reversion (Connors' RSI(2) exits on a close back across the
  5-day average and reports that stops hurt; that finding is second-hand and unverified), trailing stops for trends
  (chandelier exit, 3 x ATR below the highest high). Kaminski & Lo (MIT, J. Financial Markets 2014) find stop-loss
  rules add value when returns have momentum and lower expected return when they are close to random.
- **Holding period mismatch.** The five-year lab (`docs/STRATEGY_LAB_FINDINGS_2026-10-13.md`) gives the four
  original playbooks and momentum long their best edge at month/quarter horizons, while every position is closed the
  same day. The owner already asked for holding classes (day / week / month / quarter).
- **Evidence machinery exists.** The strategy lab (daily bars, 5 years, random-entry baseline, month-block
  bootstrap, FDR) and the paper-learning replay (`paper_simulation.py`, 15-minute bars, fidelity 0.37% vs real
  trades) can test exit variants before anything changes.
- **Multi-day positions touch the order path**: the account preflight blocks the session when positions exist,
  Koulis has no total-exposure view across days, the monitors stop at the close, and overnight gaps add risk.

## Decision

Proposed, in three gated steps:

1. **Measure first (research only).** Add exit policies to the strategy lab and to the shadow-trade replay and test,
   per strategy and side, the current exit against a family exit:
   - mean reversion: signal exit (close back across the 5-day average) + time stop (5 sessions) + wide disaster
     stop (2.5-3 x ATR);
   - momentum/breakout: chandelier trailing stop (3 x ATR from the highest high since entry), no fixed take-profit,
     time stop by holding class (20 or 60 sessions).
   Judged with the lab's statistics (edge vs random entry, block bootstrap, FDR, per-year stability).
2. **ATR-based limits for the nine daily strategies** (same policy as the original playbooks) - a risk-rule change,
   only with the owner's approval.
3. **Holding classes in execution** - only if step 1 supports longer holds and the owner approves: each strategy
   trades in its best class; the preflight manages instead of blocking existing positions; Koulis sizes on total
   exposure; monitors carry positions across sessions; overnight-gap guard in the pre-open check.

## Options Considered

### Option A: Keep one exit for all (status quo)
| Dimension | Assessment |
|-----------|------------|
| Complexity | Low |
| Cost | None now; edge left on the table (lab: original playbooks +0.4 day vs +3.1 quarter) |
| Scalability | Fine |
| Team familiarity | High |

**Pros:** nothing changes in the order path; simple to reason about.
**Cons:** exits contradict what each strategy family needs; same-day flat is the weakest class for most of them.

### Option B: Family-specific exits, still same-day
| Dimension | Assessment |
|-----------|------------|
| Complexity | Medium (exit policy per family in the monitor) |
| Cost | Small; order path unchanged in shape |
| Scalability | Fine |
| Team familiarity | Medium |

**Pros:** matches exit to strategy logic without overnight risk; testable on 15-minute replays now.
**Cons:** keeps the holding-period mismatch; signal exits on 5-day averages mean little intraday.

### Option C: Family exits + holding classes (multi-day), gated by evidence
| Dimension | Assessment |
|-----------|------------|
| Complexity | High (preflight, Koulis exposure, monitors across sessions, gap guard) |
| Cost | Most engineering; overnight risk |
| Scalability | Fine; more open positions to watch |
| Team familiarity | Low - new behaviour |

**Pros:** lets each strategy trade where the lab says it has its edge; aligns with the owner's holding-class goal.
**Cons:** the largest change to the order path; needs weeks of shadow evidence first.

## Trade-off Analysis

The binding constraint is evidence, not code: changing exits on literature alone would replace one untested rule
with another (the Connors stop finding is unverified; the chandelier multiplier is a vendor default). Step 1 costs
no risk and uses machinery that already exists, so it goes first under every option. Option B is a cheap
intermediate but leaves the larger mismatch (holding period) untouched; Option C addresses it but is only worth its
complexity if step 1 shows the longer classes hold up out of sample and in shadow trades. Recommendation: start Step 1
now; decide between B and C on its results; Step 2 can be decided independently (it only extends the existing ATR
policy).

## Consequences

- Easier: judging exits with numbers per strategy; explaining why a position closed (exit reason per family).
- Harder: more exit logic to test and monitor; with Option C, positions and risk span sessions.
- Revisit: stop multipliers and time stops after 4-6 weeks of shadow trades; the gap guard once overnight holds exist.

## Action Items

1. [x] Exit policies tested in `exit_lab.py` (daily bars, every holding class, edge vs a random entry, per year);
       findings in `docs/EXIT_LAB_FINDINGS_2026-10-16.md`: the holding period carries the edge, fixed is the best
       rule everywhere, the day class has almost none. Intraday 15-minute exit variants were not added.
2. [ ] Owner decision on Step 2 (ATR limits for the nine daily strategies).
3. [ ] After 4-6 weeks of evidence (shadow multi-day holds in the learning ledger, `holding_shadow.py`, from
       2026-10-16): owner decision between Option B and Option C.
4. [ ] If C: preflight manage-not-block, Koulis total exposure, multi-session monitors, overnight-gap guard - each
       with tests and the invariants guard.

Sources: Kaminski & Lo, "When do stop-loss rules stop losses?" (MIT DSpace); RSI(2) exit descriptions (LuxAlgo,
FMZ/steemit, Alpha Algo Trading Research - secondary); chandelier exit definition and the StockCharts SystemTrader
test (Arthur Hill, 2016).

## Evidence for Step 3 being collected (2026-10-22)

The owner's notes are followed in shadow, without any order: the holding class that suits each kind of company and
environment is learned from the shadow holds (`holding_shadow.category_findings`: every category tag - Giannis
tier, Piotroski, results trend, market state, sector, volatility - against the rest, FDR, repeated weeks; findings
become lesson candidates for the traders); positions are judged on their own clock (`thesis_exit.py`); and the
watch priority is measured on every record (`returned_after_demotion`: after_filing / after_gap / no). Multi-day
execution (preflight that manages instead of blocking, Koulis total exposure, monitors across sessions,
overnight-gap guard) is built only when this evidence supports it and the owner decides.

## Every position watched, whatever its declared class (owner's decision 2026-10-22)

Each position keeps the holding class it was opened for (day / week / month / quarter) and is judged on that clock,
but all of them are watched for an abrupt change of the market's mood: a session in which SPY closes beyond 2 x its
ATR(14) against the position, or the market turns from calm to volatile, sends it out at the next open
(`thesis_exit.market_shocks`, rules `thesis_shock` in the shadow holds and `fixed_shock` in the exit lab). Five
years of the exit lab (80,445 signals, 26 shock sessions): no effect on day and week holds, +0.04 points on month
holds and **+0.39 points per trade on quarter holds** (6 strategies better, 2 worse, 15 no clear difference). In
execution (Step 3) the same guard would run intraday in the monitors for every open position.
