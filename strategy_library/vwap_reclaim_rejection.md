# vwap_reclaim_rejection

## Philosophy

- Use VWAP as an intraday fairness line after the daily setup has already been approved.
- For longs, wait for price to reclaim VWAP instead of buying while the stock is still falling.
- For shorts, wait for price to reject VWAP instead of shorting while the stock is still squeezing upward.

## Setup Rules

- Daily setup must already be valid from the analyst and trader playbooks.
- Long: price trades below or around VWAP, then closes a 15Min candle back above VWAP with constructive volume.
- Short: price trades above or around VWAP, then closes a 15Min candle back below VWAP with rejection behavior.
- The trigger candle must agree with direction: green for long, red for short.
- Risk should be defined around VWAP loss/reclaim failure or the trigger candle invalidation level.
- The setup is stronger when RSI/MACD agree with the reclaim or rejection.

## Disqualifiers

- Price chops around VWAP without a decisive 15Min close.
- Volume is too weak to confirm institutional participation.
- Long setup is already far above VWAP before entry.
- Short setup is already far below VWAP before entry.
- ATR is too high to define a reasonable stop.

## Outcome Tracking

- 1-day check: did VWAP hold after entry?
- 3-day check: did the move continue in the intended direction?
- 5-day check: did the entry avoid chasing?
- 10-day check: did the setup create a clean swing or only a scalp?
- 20-day check: did VWAP confirmation improve win rate versus setup-only entries?
- What counts as success: cleaner entry timing and lower immediate drawdown.
- What counts as failure: repeated fake reclaims/rejections that reverse immediately.

## Analysis Template

- Distance from local lows: relevant for long reclaim quality.
- Fundamental improvement: supportive, not sufficient alone.
- Volume confirmation: important.
- Sector wake-up: helpful.
- Clear support: VWAP plus local structure should be visible.
- Dilution / structural risk: must stay manageable.

## Source Basis

- Direct sources:
  - https://www.interactivebrokers.com/campus/trading-lessons/support-and-resistance/
  - https://www.schwab.com/learn/story/ins-and-outs-swing-trade
  - https://www.schwab.com/learn/story/trading-stocks-with-macd
  - https://international.schwab.com/story/average-true-range-indicator-and-volatility
  - https://www.interactivebrokers.com/campus/trading-course/cme-building-a-trade-plan/
- Internal inference notes:
  - This is an internal execution-timing synthesis from approved support/resistance, trend, momentum, and risk-management material.
