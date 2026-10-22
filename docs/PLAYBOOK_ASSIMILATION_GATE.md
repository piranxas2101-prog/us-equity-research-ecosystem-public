# Trader Playbook Assimilation Gate

This gate sits between theoretical ecosystem understanding and free paper experimentation:

`theory contract -> playbook assimilation exam -> free paper experiment -> lifecycle/P&L review`

It does not score profit and it never authorizes broker execution. It tests whether Yankos and Periklis can apply the shared catalog while preserving their different portfolio status and weights.

## What is tested

- full catalog coverage and source hashes;
- selection of LONG, SHORT or FLAT perspectives;
- an explicit opposing perspective rather than one-sided agreement;
- semantic completeness and freshness of required data;
- completed entry confirmation;
- invalidators, exit logic and holding horizon;
- global SHORT gates: shortability, borrow/carry, squeeze or buyback risk and cover discipline;
- defensive no-trade behavior;
- strict separation of `research`/`shadow` knowledge from active specialization;
- lifecycle response to a frozen invalidator or time stop.

The exam uses synthetic symbols and no live market data. P&L, win rate and future facts are excluded.

## Pass policy

An agent passes only when:

- total score is at least 85/100;
- every configured domain floor is met;
- no critical failure exists;
- every active-specialization case passes;
- the portfolio still covers 100% of the catalog.

Any change to the catalog, exam or portfolio changes its SHA-256 identity and makes the previous assessment distinguishable from the current code state.

## Command

```powershell
.\run_py.cmd trader_playbook_assimilation.py --output-dir output/playbook_assimilation
```

Use `--agent yankos` or `--agent periklis` for one report. A passing exam means observable contract application on deterministic cases; it does not prove cognition, profitability or live readiness.
