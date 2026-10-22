# Sokratis Volatility-Adjusted VWAP Reclaim

- Name: `sokratis_volatility_adjusted_vwap_reclaim`
- Owner agent: `sokratis`
- Status: `research_note`
- Style: intraday execution filter / timing improvement

## Philosophy

This strategy is Sokratis' first math-built proposal.

It tries to avoid buying while price is still falling and avoid shorting while price is still squeezing. The core idea is that VWAP reclaim/rejection becomes more useful when it is adjusted for volatility, candle quality, and overextension.

The strategy is not an active trading rule yet. It is a training and backtest candidate for `yankos` and `periklis`.

## Mathematical Basis

- VWAP as intraday fair-value reference.
- ATR or realized intraday range as volatility normalization.
- Z-score / standardized distance from VWAP to avoid chasing.
- Candle body and close-location value for confirmation quality.
- Event testing against baseline post-signal returns.
- Overfit guard before promotion.

## Setup Rules

Long setup:

- Daily/shortlist setup is already approved by the existing analyst/trader pipeline.
- Price trades below or near VWAP, then closes a completed `15Min` candle back above VWAP.
- The reclaim candle closes in the upper part of its range.
- The close is not more than `1.5 ATR` or equivalent volatility band above VWAP.
- Relative volume is stable or improving.

Short setup:

- Daily/shortlist setup supports a short thesis and broker shortability is available.
- Price trades above or near VWAP, then closes a completed `15Min` candle back below VWAP.
- The rejection candle closes in the lower part of its range.
- The close is not more than `1.5 ATR` or equivalent volatility band below VWAP.
- Relative volume supports the rejection.

## Disqualifiers

- Price chops around VWAP without a decisive `15Min` close.
- Reclaim/rejection happens after the move is already extended.
- ATR is too high to define a rational stop.
- Candle has weak body quality or closes near the middle of its range.
- Setup lacks enough samples for backtest review.
- Strategy appears profitable only after many parameter variations.

## Risk / Reward Assumptions

- User-configured TP/SL always overrides defaults.
- Initial research assumption: reward target must be larger than stop distance.
- Stop should be tied to VWAP failure/reclaim failure or trigger-candle invalidation.
- Holding period starts as same-day paper-only.

## Outcome Tracking

- 1-day check: did price hold the reclaimed/rejected VWAP after entry?
- 3-day check: did the signal improve entry timing versus setup-only entries?
- 5-day check: did volatility adjustment avoid late/chase entries?
- 10-day check: did this filter reduce false breakouts?
- 20-day check: did the filter improve average return or drawdown compared with existing VWAP logic?

What counts as success:

- Better entry timing than raw setup-only entries.
- Fewer late entries.
- Positive forward paper evidence after enough samples.
- Trader explanations show they understand the invalidation.

What counts as failure:

- Too few samples.
- Repeated chop around VWAP.
- Worse results than existing `15Min` confirmation.
- Traders cannot explain the rule clearly.

## Trader Training Questions

- Is this a trend-continuation, reclaim, or mean-reversion idea?
- What exactly confirms the `15Min` candle?
- Is price too far from VWAP to justify entry?
- What invalidates the setup?
- What does the backtest say versus the baseline?

## Promotion Gate

This strategy can move from `research_note` to `training_only` only after:

- `yankos` and `periklis` explain the rule in their own words.
- Sokratis maps required data fields.
- Backtest/event-test plan is written.
- `overfit_guard` confirms that no parameter fishing is being accepted.

It cannot open paper trades until explicitly promoted to `paper_candidate`.

## Source Basis

Direct mathematical/source framework:

- `strategy_library/math_strategy_taxonomy.md`
- `strategy_library/trader_math_skill_curriculum.md`
- `strategy_library/math_research_sources.md`
- `strategy_library/vwap_reclaim_rejection.md`

Internal synthesis notes:

- This is a Sokratis synthesis that combines VWAP reclaim/rejection, volatility normalization, candle-feature confirmation, and overfit control.
