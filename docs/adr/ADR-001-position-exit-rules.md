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
4. [x] Step 3 infrastructure built on the owner's request (2026-10-23), inactive until he gives a strategy a class:
       preflight manage-not-block, Koulis total exposure, multi-session monitor with the market-shock guard (see
       below). [ ] Owner decision: which strategies get which class (`paper_session.holding_classes`).

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

## Step 3: the agents decide how long each position stays open (owner 2026-10-23)

Built the day after the session of 2026-10-22, when all six approvals went through Miltos, nothing was sent
directly, no position monitor started and his four fills (INCY, ROKU, AMD, MIAX short) were left open overnight.
The same day the owner set the direction: "the agents decide which positions open and for how long - five hours,
a week, three days; since monitors watch them, opening and closing need no fixed time window". A first version
with a fixed class map (week / month / quarter set by the owner) was replaced the same day by this one.

* **Monitors also for Miltos entries.** `start_trader_paper_trading_today.ps1` starts the position monitors when
  orders were sent *or* Miltos will enter; a monitor re-reads the opening reports and picks up his fills.
* **Leftovers closed, not blocking.** By default the preflight closes, at the session's start, every position and
  open order that is not a registered position (`--auto-flatten --keep-registered data/paper_positions.json`);
  `-BlockOnLeftovers` restores the old block. A failed close still blocks the session. On days without new orders
  the afternoon session does the same at 09:35 New York.
* **The horizon is the agent's decision, in its frozen trade plan.** With `holding_period_rule = agent_horizon` in
  `yankos_playbooks.json` / `periklis_playbooks.json`, Yankos and Periklis write a `holding_horizon` into every
  plan (`holding_horizon.py`), from the exit lab's horizon curve (`exit_lab.py --horizon-curve`: the fixed hold on
  1, 2, 3, 5, 7, 10, 15, 20, 30, 40 and 60 sessions against a random entry of the same stock, FDR, per year). Only
  horizons with a real edge count (significant and positive in more than half of the years); among them the agent
  takes the most edge per square root of sessions (edge per unit of risk). No real edge at any horizon: the
  position stays same-day - open for the hours left in the session. The horizon is a ceiling: the monitors close
  earlier on the widening stop, a broken thesis or a market shock. Thymios accepts only `same_day_flat` or a valid
  agent horizon (whole sessions 1-60 with matching minutes); the plan's hash covers the horizon.
* **Time is counted in trading minutes from the fill** (`trading_clock.py`, NYSE calendar from `exchange_calendars`,
  added to the lock): one session = 390 minutes, nights, weekends, holidays and early closes skipped. A
  one-session position filled at 11:00 ends at 11:00 of the next session. The same-day flat moves to 5 minutes
  before an early close (13:00) - before, 15:55 came after the market had closed.
* **Entries:** a position with an agent horizon may open until 15:45 New York (`agent_horizon_entry_until_eastern`,
  never later than 15 minutes before an early close); a same-day position keeps the 15:00 cutoff.
* **Registry and monitor:** the day monitor hands a filled position with a valid horizon over to
  `data/paper_positions.json` (`positions_registry.py`; unknown ATR(14) or fill time: it stays same-day) and
  `alpaca_multi_day_monitor.py` watches it every session until its horizon ends: widening stop 1.1 x ATR x
  sqrt(sessions so far), thesis check once per new nightly run with the rules that fit the horizon (up to 5
  sessions technical, up to 20 both, beyond fundamental; a stock missing from the board is held), market shock in
  the last 15 minutes (SPY beyond 2 x ATR against the position, or calm -> volatile, with today's bar so far: the
  lab measured the close and left at the next open, so live exits come one overnight gap earlier), horizon end.
  A failed cycle is retried, never ends the watch. Closed positions go to `data/paper_positions_closed.jsonl`.
* **Koulis:** a held position always blocks its ticker for a new order. Owner's decision 2026-10-23: **no limit on
  held positions to begin with** (`paper_session.held_positions_limit = "none"`): the daily limits (6 new orders,
  agent-sized notional, 2 shorts) apply to new orders only, so every agent decision is taken and its win rate shows
  whether the agents open positions for a reason, not just to open them; the limits are discussed again on that
  evidence. `"count"` makes held positions take slots, notional and short limits (an unknown held value then stops
  new orders); an unreadable switch takes that stricter side.
* **No limit on new orders either** (owner 2026-10-24: "let them make as many trades as they want, just watched by
  the monitors with the rules we set"): `paper_session.new_orders_limit = "none"` - Koulis keeps one candidate per
  ticker, no opposite directions and no held ticker, and Thymios still validates every intent, but there is no
  count, notional, short or per-strategy cap; the traders place every approved intent. `"limits"` restores the
  launcher's daily limits. With hundreds of positions the Alpaca calls are paced (about 170 a minute, 429 retried),
  the monitors read all positions in one call per cycle and send every close at once at the flat.
* **Entry confirmation on sound data** (owner 2026-10-24, the AMSF short): Miltos confirms on completed SIP bars
  with volume, RSI(14) 15m and a real-time check (`entry_confirmation_rules.py`, docs/RULES_REVIEW.md).
* **Win rate on the right window:** a held trade is judged against SPY from its entry session's open to its exit
  session's close (`paper_learning.attach_held_spy`), not against the entry session alone.
* **Learning:** the ledger records a held trade as `held_on_horizon` and completes it on the session it closes in
  (`complete_held`: result, exit rule, exit session), so every held position still feeds the lessons.

Evidence on 2026-10-23's candidates: Yankos's support_bounce long (107 candidates) would be held 60 sessions,
Periklis's momentum_12_1 short 10, gap_fade short 5; momentum_12_1 long (99) has no stable edge at any horizon and
stays same-day. Counted in Koulis's six order slots, a few quarter-long positions would have left little room
for new ones; hence the owner's choice of no held-position limit while the win rate is measured.
Not yet: intraday evidence for horizons shorter than a session (the curve is built on daily bars).
