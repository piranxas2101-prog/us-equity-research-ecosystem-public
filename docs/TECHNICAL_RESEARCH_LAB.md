# Technical research lab

The technical research lab upgrades trader evaluation from forward-return scorecards to a point-in-time execution simulation. It remains research-only and cannot submit broker orders.

## Responsibilities

- Yankos and Periklis continue to own separate setups and playbooks.
- Sokratis may compare their results and teach approved methods; he does not place trades.
- Miltos, Koulis and Thymios remain the separate entry, portfolio-risk and order-intent controls.

## Validation flow

```text
trader signal
-> point-in-time candle validation
-> indicator causality audit
-> rolling train/validation/test windows
-> next-bar execution simulation
-> cost, MFE/MAE and regime scorecards
-> shadow/paper-candidate promotion gate
```

`technical_research_lab.py` implements the first local version of this flow. It calculates EMA, MACD, RSI, ATR and session VWAP incrementally, then verifies that each historical value matches the value produced with only the candles available at that moment.

## Execution assumptions

- Signals fill at the next tradable bar, never on the signal bar.
- Slippage and commissions are charged on every simulated fill.
- Filled quantity is capped by configured bar-volume participation.
- Stops, targets and time exits are explicit.
- If a stop and target are both touched inside one OHLC bar, the simulator uses the stop as the conservative outcome.
- Every simulated trade records MFE, MAE and market-regime labels.
- Scorecards include net expectancy, profit factor, drawdown, trade-level Sharpe/Sortino and a deterministic bootstrap confidence interval.

These rules make the output stricter than the existing `strategy_backtest.py`, which remains an honest signal-outcome scorecard rather than an execution backtest.

## Promotion gate

A playbook remains `shadow` unless all gates pass:

- causal-indicator audit passes;
- at least 50 out-of-sample trades;
- positive net expectancy after costs;
- profit factor of at least 1.2;
- maximum drawdown no worse than 20%;
- at least 20 forward paper trades.

Promotion still requires the user's explicit approval. The lab never edits a trader policy or activates paper execution by itself.

## Command

```powershell
.\run_py.cmd technical_research_lab.py `
  --input data/technical_lab_input.json `
  --output output/technical_research_lab_latest.json
```

The input contains chronologically sorted OHLCV bars with timezone-aware `timestamp` and `available_at` fields, optional trade simulations, walk-forward window sizes and paper-promotion evidence. Runtime inputs and outputs remain local and excluded from Git.

For the current Yankos and Periklis candidate/watch boards, launch the independent validation worker with:

```powershell
.\run_technical_validation_background.ps1
```

It downloads read-only `15Min` IEX bars through the configured Alpaca market-data credentials, checkpoints each symbol, performs no broker/account operation, and writes `output/technical_validation_latest.json` plus a readable Markdown report. The worker shows one local notification after reaching a terminal state; it does not require a monitoring loop.

## Design sources

The implementation is original project code informed by these upstream patterns:

- Microsoft Qlib rolling task generation and time-separated datasets (MIT).
- QuantConnect LEAN separation of strategy, execution and risk models (Apache-2.0).
- Freqtrade look-ahead and recursive-indicator audit concepts (GPLv3; concepts only, no copied code).
- Backtesting.py order and cost semantics (AGPLv3; concepts only, no copied code).
- VectorBT parameter-sweep and walk-forward research patterns (Commons Clause; concepts only, no dependency).

This project keeps its own small implementation so the public MIT repository does not absorb incompatible source code or require a second trading platform.
