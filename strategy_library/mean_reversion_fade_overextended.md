# mean_reversion_fade_overextended

## Philosophy

- Trade against overextended moves only after price confirms exhaustion.
- This strategy is for short or avoid decisions on stretched names, not for guessing tops.

## Setup Rules

- Price must be near the top of its 20-day and/or 60-day range.
- RSI, recent return, or volume spike should show extension.
- Short requires a 15Min bearish confirmation candle, ideally after a failed push higher.
- The asset must be shortable before it can become an executable short candidate.
- Risk should be defined above the rejection high or failed breakout high.
- Fundamentals and catalyst context should not strongly contradict the fade.

## Disqualifiers

- Stock continues making higher highs without rejection.
- Asset is not shortable.
- Bullish catalyst is too strong or fresh.
- Volume confirms continuation rather than exhaustion.
- ATR is too high for a controlled stop.

## Outcome Tracking

- 1-day check: did rejection appear quickly or did the squeeze continue?
- 3-day check: did mean reversion start?
- 5-day check: did the fade reach a reasonable target?
- 10-day check: did the move normalize or reverse back up?
- 20-day check: did the strategy avoid shorting strong trends too early?
- What counts as success: cleaner short entries after confirmed rejection.
- What counts as failure: repeated premature shorts into momentum.

## Analysis Template

- Distance from local lows: less relevant.
- Fundamental improvement: can weaken the short thesis.
- Volume confirmation: mandatory.
- Sector wake-up: important, because sector strength can kill fades.
- Clear support: resistance/rejection level must be clear.
- Dilution / structural risk: may support the fade but does not replace confirmation.

## Source Basis

- Direct sources:
  - https://www.schwab.com/learn/story/identifying-trend-reversals-with-rsi
  - https://www.schwab.com/learn/story/trading-stocks-with-macd
  - https://international.schwab.com/story/average-true-range-indicator-and-volatility
  - https://www.interactivebrokers.com/campus/trading-lessons/support-and-resistance/
  - https://www.interactivebrokers.com/campus/trading-course/cme-building-a-trade-plan/
  - https://www.sec.gov/about/reports-publications/investor-publications/day-trading-your-dollars-at-risk
- Internal inference notes:
  - This is an internal short-side risk-management synthesis from approved reversal, momentum, support/resistance, and risk-warning material.
