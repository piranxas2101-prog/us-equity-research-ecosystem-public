# pullback_to_vwap_continuation

## Philosophy

- Avoid chasing the first move by waiting for a controlled pullback into VWAP or nearby intraday support.
- The goal is to join a trend after buyers or sellers prove they can defend the fair-value area.

## Setup Rules

- Daily setup must already support the intended direction.
- Long: price is in an intraday uptrend, pulls back toward VWAP, holds it, and prints a confirming 15Min candle.
- Short: price is in an intraday downtrend, pulls back toward VWAP from below, rejects it, and prints a confirming 15Min candle.
- Volume should contract during the pullback and improve on continuation.
- RSI/MACD should not contradict the intended continuation.
- Stop should be near the failed VWAP hold/rejection area.

## Disqualifiers

- Pullback slices through VWAP without stabilization.
- Continuation trigger happens too far from VWAP.
- Volume expands against the intended direction.
- Price has already made an overextended move before the pullback.
- Risk/reward is worse than the current trader policy.

## Outcome Tracking

- 1-day check: did VWAP hold/reject after entry?
- 3-day check: did continuation follow through?
- 5-day check: did waiting for pullback improve entry quality?
- 10-day check: did the trend continue or stall?
- 20-day check: did pullback entries outperform breakout entries?
- What counts as success: better entries after patience.
- What counts as failure: waiting too long and missing valid moves.

## Analysis Template

- Distance from local lows: relevant for long continuation.
- Fundamental improvement: supportive.
- Volume confirmation: important.
- Sector wake-up: helpful.
- Clear support: VWAP/support must be visible.
- Dilution / structural risk: must stay manageable.

## Source Basis

- Direct sources:
  - https://www.interactivebrokers.com/campus/trading-lessons/support-and-resistance/
  - https://www.schwab.com/learn/story/ins-and-outs-swing-trade
  - https://www.schwab.com/learn/story/trading-stocks-with-macd
  - https://international.schwab.com/story/average-true-range-indicator-and-volatility
  - https://www.interactivebrokers.com/campus/trading-course/cme-building-a-trade-plan/
- Internal inference notes:
  - This is an internal continuation-entry synthesis from approved trend, support/resistance, momentum, and volatility material.
