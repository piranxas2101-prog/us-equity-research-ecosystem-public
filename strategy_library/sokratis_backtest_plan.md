# Sokratis Backtest / Event-Test Plan

- Version: 1
- Updated: 2026-05-01
- Owner: `sokratis`
- Status: planning / no paper-trading authority

## Decision

The first Sokratis strategy to move into a concrete backtest/event-test plan is:

- `sokratis_volatility_adjusted_vwap_reclaim`

The second strategy stays in plan/design mode until the first event-test pipeline is stable:

- `sokratis_volatility_compression_breakout`

Neither strategy is approved for paper trading from this document alone.

## Why VWAP Reclaim Goes First

`sokratis_volatility_adjusted_vwap_reclaim` directly targets the biggest trader issue observed so far: bad timing after a good thesis.

It answers the question:

- Did waiting for a completed `15Min` VWAP reclaim/rejection improve the entry versus acting on the setup immediately?

This is a better first test because it is a filter on existing trader-owned boards. It does not replace `yankos` or `periklis`; it only checks whether Sokratis' timing filter improves each trader's own entries.

## Trader Board Rule

Backtests must preserve separate trader ownership:

- `yankos` is tested only against `yankos` symbols and playbooks.
- `periklis` is tested only against `periklis` symbols and playbooks.
- Sokratis filters are shared knowledge, not shared execution logic.
- Results must be reported per trader before any combined read is shown.

## VWAP Reclaim Event-Test Design

### Input Universe

Use only symbols that already appear in trader-owned outputs:

- `output/yankos_strategy_latest.json`
- `output/periklis_strategy_latest.json`

Candidate and watch rows should be tested separately.

### Required Data

For each trader-owned symbol and session:

- `15Min` OHLCV candles.
- Intraday VWAP.
- ATR or realized range estimate.
- Relative volume / volume baseline.
- Candle body-to-range ratio.
- Close-location value.
- Direction from the trader report: long or short.
- Original trader playbook and signal score.

### Long Event

A long event is valid only when:

- the trader-owned setup is long,
- price trades below or near VWAP,
- a completed `15Min` candle closes back above VWAP,
- the candle closes in the upper part of its range,
- the close is not too extended above VWAP,
- volume is stable or improving.

### Short Event

A short event is valid only when:

- the trader-owned setup is short,
- the asset is broker-shortable,
- price trades above or near VWAP,
- a completed `15Min` candle closes back below VWAP,
- the candle closes in the lower part of its range,
- the close is not too extended below VWAP,
- volume supports the rejection.

### Baselines To Compare

Compare at least three versions:

- `setup_only`: enter when the trader-owned setup first appears.
- `existing_15min_confirmation`: use the current 15Min confirmation rule.
- `sokratis_vwap_reclaim`: require the VWAP reclaim/rejection filter.

### Metrics

Report separately for `yankos` and `periklis`:

- sample count,
- accepted / rejected / waiting events,
- win rate,
- average return,
- median return,
- average adverse excursion if available,
- average favorable excursion if available,
- false-entry reduction,
- missed-winner rate,
- improvement versus baseline,
- whether reward target is larger than stop distance.

### Horizons

Start with same-day and short forward windows:

- same-day close,
- 1 day,
- 3 days,
- 5 days.

Longer horizons can be added later only if the strategy is being evaluated as more than an intraday timing filter.

## Compression Breakout Plan

`sokratis_volatility_compression_breakout` is promising, but should be tested second.

Reason:

- It needs more parameter choices: compression window, ATR percentile, Bollinger width, range high/low, and volume threshold.
- These choices create higher overfit risk.
- It is better to first build the event-test pipeline with VWAP reclaim, then reuse the same reporting and overfit guard.

When tested, it should compare:

- raw breakout entries,
- breakout after compression,
- breakout after compression plus volume,
- breakout after compression plus volume plus reward/risk validation.

## Promotion Gates

No strategy can move beyond this plan unless:

- traders explain the rule in their own words,
- required data fields are available,
- event-test output exists,
- sample count is large enough,
- overfit guard reviews parameter choices,
- forward paper evidence is collected,
- and the user explicitly approves promotion.

## Next Implementation Task

Build a local event-test module for `sokratis_volatility_adjusted_vwap_reclaim` that reads trader-owned symbols, fetches or loads `15Min` candles, computes VWAP/ATR/candle-quality fields, and writes:

- `output/sokratis_vwap_event_test_latest.json`
- `output/sokratis_vwap_event_test_latest.md`
