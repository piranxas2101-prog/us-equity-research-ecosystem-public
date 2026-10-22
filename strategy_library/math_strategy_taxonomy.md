# Math-to-Strategy Taxonomy

- Version: 1
- Updated: 2026-04-30
- Purpose: Categorize the mathematics the trader agents must learn by strategy family, so future skills are built from reusable principles instead of copied internet strategies.

## Core Rule

The traders do not learn "a setup" first. They learn the mathematical behavior behind the setup.

Each strategy family must map to:

- market behavior,
- mathematical tools,
- evidence fields,
- confirmation rules,
- failure modes,
- and future skill candidates.

## Strategy Families

### Trend / Momentum Continuation

Examples:

- Controlled breakout.
- Moving-average continuation.
- Pullback-to-VWAP continuation.
- Gap-and-go.

Mathematics to learn:

- Rolling averages and rolling windows.
- Return distribution after a trend signal.
- Autocorrelation and momentum persistence.
- Volatility-adjusted distance from moving averages or VWAP.
- Hypothesis testing against baseline returns.

Evidence fields:

- rolling_return,
- moving_average_slope,
- relative_volume,
- distance_from_vwap,
- realized_volatility,
- post_signal_return_distribution.

Skill candidates:

- `trend_evidence_scoring`
- `vwap_distance_validator`
- `momentum_persistence_tester`

### Breakout / Range Expansion

Examples:

- Opening range breakout.
- Controlled breakout swing.
- Breakout retest.
- Pre-run breakout confirmation.

Mathematics to learn:

- Range statistics.
- Volatility compression and expansion.
- Percentile position inside a recent price range.
- Event study of returns after range breaks.
- False breakout rate.

Evidence fields:

- range_high,
- range_low,
- range_width_pct,
- atr_percentile,
- breakout_close_strength,
- retest_success,
- false_breakout_rate.

Skill candidates:

- `range_breakout_event_tester`
- `false_breakout_guard`
- `volatility_expansion_detector`

### Mean Reversion / Fade

Examples:

- Mean-reversion fade on overextended names.
- VWAP rejection.
- Gap-fade.
- Failed breakout fade.

Mathematics to learn:

- Z-score / standardized distance from mean.
- Reversion half-life.
- Tail risk and skewness.
- Volatility-adjusted stop distance.
- Conditional return after overextension.

Evidence fields:

- z_score_from_vwap_or_ma,
- percent_from_recent_high_low,
- rsi_extreme,
- atr_multiple_extension,
- reversal_candle_confirmed,
- post_extension_reversion_rate.

Skill candidates:

- `overextension_zscore_guard`
- `mean_reversion_evidence_scoring`
- `tail_risk_blocker`

### Support / Resistance / Reclaim

Examples:

- Support bounce.
- Failed breakdown reclaim.
- VWAP reclaim.
- Breakout retest.

Mathematics to learn:

- Local extrema detection.
- Distance-to-level statistics.
- Bounce/rejection event testing.
- Conditional probability of follow-through after reclaim.
- Noise bands around support/resistance.

Evidence fields:

- support_level,
- resistance_level,
- distance_to_level_pct,
- reclaim_close_strength,
- level_hold_count,
- failed_level_break_count.

Skill candidates:

- `level_detection`
- `reclaim_confirmation`
- `support_bounce_probability`

### Candle Pattern / Microstructure Confirmation

Examples:

- Bullish engulfing confirmation.
- Bearish rejection candle.
- Inside-bar compression.
- Doji caution.

Mathematics to learn:

- Objective candle feature extraction.
- Body-to-range ratios.
- Wick ratios.
- Pattern frequency versus baseline outcome.
- Small-sample false-positive risk.

Evidence fields:

- candle_body_pct,
- upper_wick_pct,
- lower_wick_pct,
- close_location_value,
- pattern_frequency,
- pattern_forward_return.

Skill candidates:

- `candle_feature_extractor`
- `candle_pattern_event_tester`
- `small_sample_pattern_guard`

### Gap Strategy

Examples:

- Gap-and-go.
- Gap-fade.
- Premarket continuation.

Mathematics to learn:

- Gap size distribution.
- Premarket volume percentile.
- Opening range confirmation.
- Conditional probability of continuation versus fade.
- Liquidity and slippage risk.

Evidence fields:

- gap_pct,
- premarket_volume_percentile,
- opening_range_high_low,
- gap_fill_probability,
- liquidity_score,
- slippage_risk.

Skill candidates:

- `gap_context_classifier`
- `gap_go_fade_decision_tree`
- `opening_range_validator`

### Risk / Sizing / Holding Period

Examples:

- Fixed notional.
- User-configured TP/SL.
- Same-day flat.
- Future agent-specific risk suggestions.

Mathematics to learn:

- Expected value.
- Payoff ratio.
- Win-rate versus risk/reward relationship.
- Drawdown and losing streak probability.
- Kelly-inspired sizing as education only, not default automation.

Evidence fields:

- take_profit_pct,
- stop_loss_pct,
- reward_to_risk,
- win_rate_needed,
- max_expected_drawdown,
- holding_period_rule.

Skill candidates:

- `risk_reward_validator`
- `drawdown_scenario_checker`
- `position_size_explainer`

### Backtest / Overfit / Promotion Control

Examples:

- Deciding whether any strategy graduates from `training_only`.
- Comparing Yankos versus Periklis.
- Deciding whether a skill should influence paper trades.

Mathematics to learn:

- In-sample versus out-of-sample testing.
- Bootstrap / resampling.
- Multiple testing and data snooping.
- Probability of backtest overfitting.
- Forward paper-test validation.

Evidence fields:

- sample_size,
- tested_variant_count,
- in_sample_score,
- out_of_sample_score,
- paper_forward_score,
- overfit_risk_label.

Skill candidates:

- `overfit_guard`
- `strategy_promotion_gate`
- `paper_forward_validator`

## Trader Learning Order

1. Risk/reward and expected value.
2. Candle/objective feature extraction.
3. Support/resistance and reclaim statistics.
4. Trend/momentum continuation.
5. Breakout/range expansion.
6. Mean reversion/fade.
7. Gap strategy classification.
8. Backtest hygiene and promotion control.

This order keeps the traders grounded before they learn more aggressive setups.

