# Sokratis Volatility Compression Breakout

- Name: `sokratis_volatility_compression_breakout`
- Owner agent: `sokratis`
- Status: `research_note`
- Style: intraday/swing setup discovery and timing filter

## Philosophy

This is Sokratis' second math-built proposal.

The strategy tries to find symbols that are no longer moving randomly, but are compressing into a tighter range before a possible directional expansion. The idea is simple: when volatility contracts, the next clean expansion can be useful only if price breaks the range with volume, direction, and risk/reward discipline.

This is not an active paper-trading rule yet. It is a research and training candidate for `yankos` and `periklis`.

## Mathematical Basis

- Rolling high/low range contraction over a recent window.
- ATR percentile or realized volatility percentile to detect compression.
- Bollinger Band width or normalized range width to compare current volatility with recent history.
- Donchian-style breakout level from the compression range.
- Relative volume expansion on the breakout candle.
- Candle close-location value to avoid weak breakouts.
- Reward/risk validation before any future promotion.
- Out-of-sample / forward paper validation before active use.

## Setup Rules

Long setup:

- Daily/shortlist setup is already approved by the analyst/trader pipeline.
- Recent range is compressed versus its own recent history.
- Price closes above the compression range high on a completed `15Min` candle.
- Breakout candle closes in the upper part of its range.
- Relative volume is above the recent baseline.
- Entry is not too far above the breakout level, so stop distance remains rational.
- Planned take profit is larger than planned stop loss.

Short setup:

- Daily/shortlist setup supports a short thesis and broker shortability is available.
- Recent range is compressed versus its own recent history.
- Price closes below the compression range low on a completed `15Min` candle.
- Breakdown candle closes in the lower part of its range.
- Relative volume confirms sellers.
- Entry is not too far below the breakdown level.
- Planned take profit is larger than planned stop loss.

## Disqualifiers

- Breakout happens before volatility actually compresses.
- Breakout candle closes near the middle of its range.
- Volume does not expand.
- Entry is already too extended from the range edge.
- Spread/liquidity makes stop placement unreliable.
- The setup only works after many parameter changes.
- Backtest sample count is too small for promotion.

## Risk / Reward Assumptions

- User-configured TP/SL always overrides defaults.
- Research assumption: reward target must be greater than stop distance.
- Initial stop should sit inside/behind the failed breakout level, not at an arbitrary percentage.
- Same-day paper testing should be used first.
- Longer holding periods require separate evidence.

## Outcome Tracking

- 1-day check: did the breakout hold the compression range edge?
- 3-day check: did expansion continue or immediately fail?
- 5-day check: did the setup improve timing versus buying before confirmation?
- 10-day check: did compression quality predict better follow-through?
- 20-day check: did the strategy improve average return, win rate, or drawdown versus baseline breakout entries?

What counts as success:

- Cleaner timing than raw breakout entries.
- Fewer fake breakouts.
- Positive forward paper evidence after enough samples.
- Traders can explain compression, trigger, invalidation, and risk/reward.

What counts as failure:

- Too many false breakouts.
- Volume confirmation does not add edge.
- ATR/range filters remove too many useful trades.
- Strategy is profitable only after parameter fishing.
- Traders cannot explain why the entry was not late.

## Trader Training Questions

- Is volatility really compressed, or is the symbol simply inactive?
- Where exactly is the compression range high/low?
- Did a completed `15Min` candle confirm the break?
- Is volume expanding enough to support the move?
- Where is invalidation?
- Is the reward larger than the stop?
- What does the backtest say versus a simple breakout baseline?

## Promotion Gate

This strategy can move from `research_note` to `training_only` only after:

- Sokratis defines the required data fields.
- `yankos` and `periklis` explain the strategy in their own words.
- A backtest/event-test plan compares it against simple breakout entries.
- `overfit_guard` confirms that parameter fishing is not being accepted.

It cannot open paper trades until explicitly promoted to `paper_candidate`.

## Source Basis

Direct mathematical/source framework:

- `strategy_library/math_strategy_taxonomy.md`
- `strategy_library/trader_math_skill_curriculum.md`
- `strategy_library/math_research_sources.md`
- `strategy_library/opening_range_breakout_breakdown.md`
- `strategy_library/breakout_retest_failed_breakout.md`

Internal synthesis notes:

- This is a Sokratis synthesis that combines volatility contraction, range breakout logic, volume confirmation, candle-quality checks, and risk/reward validation.
