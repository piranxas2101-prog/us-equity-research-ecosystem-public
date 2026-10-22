# Playbook Framework v2

## Purpose

Yankos and Periklis must not produce a single technical opinion from two near-identical rule sets. The v2 framework separates:

1. the shared market-perspective catalog,
2. each trader's specialization portfolio,
3. executable specialized evaluators,
4. Koulis/Thymios execution gates.

## Data flow

```text
Babis + Giannis symbol evidence
          |
          v
Shared catalog (17 LONG/SHORT/FLAT perspectives)
          |
          +--> Yankos specialization weights/statuses
          |
          +--> Periklis specialization weights/statuses
          |
          v
Advisory playbook_perspectives (never executable)
          |
          +--> alternative/counter views attached to active results
          |
          v
Specialized active evaluator -> Koulis -> Thymios -> paper executor
```

## Status contract

- `research`: the idea is defined but needs evidence and tests.
- `shadow`: it runs on current inputs and records matches without execution authority.
- `paper_candidate`: it may enter controlled paper probation after explicit promotion.
- `active`: a specialized evaluator may create a candidate, but Koulis and Thymios remain mandatory.
- `paused` / `retired`: excluded from routing.

Changing a JSON status alone does not create execution authority. A new active strategy also needs a specialized evaluator, current-session gates, tests, backtest/event study and paper probation.

## Trader differentiation

Yankos emphasizes tactical reversal, breakout/retest, VWAP, gaps, opening range and overextension fades.

Periklis emphasizes accumulation, compression, continuation, failed-breakdown reclaim, post-earnings drift, sector-relative strength and fundamental revision.

Both still review the entire catalog. Raw market match is common and reproducible; `agent_priority_score` applies the trader's specialization weight so their attention ordering differs without altering the observed facts.

## Output contract

The agent JSON keeps existing executable rows under `results`. New fields are additive:

- `playbook_framework`: catalog/portfolio/readiness counts.
- `playbook_perspectives`: one advisory evaluation per symbol and assigned playbook.
- `results[].alternative_perspectives`: the strongest supporting and conflicting views.
- `results[].playbook_breadth`: LONG/SHORT/FLAT perspective counts.

Downstream order selection continues to read only executable `results` and cannot submit a perspective row.

## Evaluation

Promotion must be measured by playbook id and version, side, sector, regime and horizon. The minimum metrics are sample size, expectancy in R, win rate with uncertainty, profit factor, MAE/MFE, drawdown, time-to-trigger, slippage, time stop, duplicate rate and missing/stale data rate.

Information-quality scoring remains separate from trade-performance scoring.
