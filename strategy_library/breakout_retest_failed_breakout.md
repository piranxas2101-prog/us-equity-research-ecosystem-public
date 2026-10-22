# breakout_retest_failed_breakout

## Philosophy

- Separate real breakouts from traps by waiting for either a retest hold or a failed breakout reversal.
- A valid breakout should defend the broken level. A failed breakout often becomes a short or avoid signal.

## Setup Rules

- Identify a clear breakout level from daily or intraday structure.
- Long: price breaks above resistance, pulls back, and holds the breakout level with a confirming 15Min candle.
- Short/fade: price breaks above resistance but fails, closes back below the level, and prints a bearish 15Min confirmation.
- Volume should confirm the break or the failure.
- Risk is defined around the retest level or failed-breakout high.
- This strategy should be used only after a clear level exists.

## Disqualifiers

- No obvious support/resistance level.
- Retest does not happen and price is already extended.
- Failed breakout lacks bearish confirmation.
- Spread/liquidity makes the level unreliable.
- Analyst layer flags unacceptable structural risk.

## Outcome Tracking

- 1-day check: did the retest/failure behave as expected?
- 3-day check: did the move follow through?
- 5-day check: did waiting for retest reduce false entries?
- 10-day check: did the level remain important?
- 20-day check: did retest logic improve trader win rate?
- What counts as success: fewer chase entries and cleaner invalidation.
- What counts as failure: too many missed moves or late entries.

## Analysis Template

- Distance from local lows: secondary unless retest is near support.
- Fundamental improvement: supportive.
- Volume confirmation: important.
- Sector wake-up: helpful.
- Clear support: mandatory.
- Dilution / structural risk: must stay manageable.

## Source Basis

- Direct sources:
  - https://www.interactivebrokers.com/campus/trading-lessons/support-and-resistance/
  - https://www.schwab.com/learn/story/ins-and-outs-swing-trade
  - https://www.schwab.com/learn/story/trading-stocks-with-macd
  - https://international.schwab.com/story/average-true-range-indicator-and-volatility
  - https://www.interactivebrokers.com/campus/trading-course/cme-building-a-trade-plan/
- Internal inference notes:
  - This is an internal breakout-quality synthesis from approved support/resistance, swing trading, momentum, and trade-plan material.
