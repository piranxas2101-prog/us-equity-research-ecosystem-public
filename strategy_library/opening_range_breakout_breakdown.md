# opening_range_breakout_breakdown

## Philosophy

- Use the opening range to avoid guessing direction too early.
- Let the first part of the session define the battleground, then trade only when price breaks that range with confirmation.
- This is mainly an execution strategy for already-approved daily setups.

## Setup Rules

- Define the opening range from the first completed 15Min candle of the regular session.
- Long: price closes a later 15Min candle above the opening-range high with a green candle.
- Short: price closes a later 15Min candle below the opening-range low with a red candle.
- Volume should be at least acceptable relative to the symbol's recent intraday activity.
- The trigger should not be too far from the range boundary, or risk/reward becomes poor.
- Stop should be defined around failed reclaim of the breakout/breakdown level or the trigger candle.

## Disqualifiers

- Breakout happens on weak volume.
- Trigger candle is too extended and leaves no clean stop.
- Price breaks the range and immediately re-enters it.
- Spread or liquidity makes execution unreliable.
- Broader analyst read says the name is structurally too risky.

## Outcome Tracking

- 1-day check: did the opening-range break hold through the session?
- 3-day check: did the trade create follow-through?
- 5-day check: did the opening range identify real momentum?
- 10-day check: did the trade become a swing or fade back into range?
- 20-day check: did opening-range confirmation outperform immediate entry?
- What counts as success: fewer premature entries and cleaner direction selection.
- What counts as failure: many false breaks or poor risk/reward after the trigger.

## Analysis Template

- Distance from local lows: relevant for long ORB quality.
- Fundamental improvement: supportive.
- Volume confirmation: mandatory.
- Sector wake-up: useful.
- Clear support: opening range boundary must be clear.
- Dilution / structural risk: must stay manageable.

## Source Basis

- Direct sources:
  - https://www.interactivebrokers.com/campus/trading-lessons/support-and-resistance/
  - https://www.schwab.com/learn/story/ins-and-outs-swing-trade
  - https://international.schwab.com/story/average-true-range-indicator-and-volatility
  - https://www.interactivebrokers.com/campus/trading-course/cme-building-a-trade-plan/
- Internal inference notes:
  - This is an internal rule-based opening-range execution synthesis from approved support/resistance and trade-plan material.
