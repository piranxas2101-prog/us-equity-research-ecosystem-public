# gap_go_gap_fade_decision_tree

## Philosophy

- Treat gaps as decision points, not automatic entries.
- First decide whether the gap has continuation quality or fade risk, then require 15Min confirmation.

## Setup Rules

- Identify whether the symbol gaps up or down relative to the prior close.
- Gap-and-go long: gap up holds above opening range/VWAP and prints a confirming 15Min continuation candle.
- Gap-fade short: gap up fails to hold opening range/VWAP and prints a bearish 15Min rejection candle.
- Gap-down reclaim long: gap down fails lower, reclaims VWAP or opening range, and prints bullish confirmation.
- Volume and catalyst quality must support the chosen path.
- Risk should be defined around VWAP, opening range, or the failed gap level.

## Disqualifiers

- No clear catalyst or context behind the gap.
- Price chops inside the opening range without direction.
- Spread/liquidity is too poor.
- Gap is already too extended before confirmation.
- Analyst layer flags unacceptable structural or dilution risk.

## Outcome Tracking

- 1-day check: did the gap path hold through the session?
- 3-day check: did the gap create follow-through?
- 5-day check: did the decision tree avoid false gap trades?
- 10-day check: did the move become a swing or only an intraday event?
- 20-day check: did gap logic improve timing versus setup-only entries?
- What counts as success: correct path selection after confirmation.
- What counts as failure: trading gaps before direction is proven.

## Analysis Template

- Distance from local lows: relevant for gap-down reclaims.
- Fundamental improvement: catalyst quality matters.
- Volume confirmation: mandatory.
- Sector wake-up: useful.
- Clear support: opening range/VWAP must be clear.
- Dilution / structural risk: must stay manageable.

## Source Basis

- Direct sources:
  - https://www.schwab.com/learn/story/ins-and-outs-swing-trade
  - https://www.interactivebrokers.com/campus/trading-lessons/support-and-resistance/
  - https://www.schwab.com/learn/story/trading-stocks-with-macd
  - https://international.schwab.com/story/average-true-range-indicator-and-volatility
  - https://www.interactivebrokers.com/campus/trading-course/cme-building-a-trade-plan/
  - https://www.sec.gov/about/reports-publications/investor-publications/day-trading-your-dollars-at-risk
- Internal inference notes:
  - This is an internal gap decision synthesis from approved swing-trade, support/resistance, momentum, volatility, and risk-warning material.
