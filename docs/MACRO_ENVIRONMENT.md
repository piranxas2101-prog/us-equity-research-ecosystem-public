# Macro environment (shadow)

Owner's question (2026-10-13): is anyone teaching and computing the long-term environment? Before this, the
nightly Babis scan already classified a FRED regime (`macro_regime.py`), but the result stayed inside the Babis
checkpoint: no agent, report or learning step read it. `macro_environment.py` makes it a visible, recorded input.

## What it computes (every night, step `macro_environment`, after the traders)

| Part | Source | Output |
|---|---|---|
| Economy | FRED: 10y-2y curve, unemployment, industrial production, BAA-10y credit spread, CPI, Fed funds | regime (expansion, transition, contraction risk, insufficient data) and sector tail/headwinds (`macro_regime`) |
| Market | SPY from the price store | above/below the 200-day average, 50 vs 200, 3- and 12-month return, distance from the 52-week high, 20-day realized volatility (calm < 15%, normal < 25%, elevated < 35%, stressed) |
| Breadth | every stored company priced on SPY's last session | share above its own 200- and 50-day average (broad >= 60%, narrow < 40%); unknown with fewer than 2,000 priced companies |
| Backdrop | the three above | supportive / mixed / hostile, with the reasons and whether any part was unknown |

To keep breadth representative, the step first brings every company's stored prices up to date (new sessions
only, about 10 minutes); it runs after the traders, so it never delays a decision, and a failure is a warning.

Written to `output/macro_environment_latest.json` (kept in the run manifest) and a dated copy under
`output/macro_environment/`. The daily report has a section that explains each signal in plain words.

## Market state (rebuilt for any past session)

`market_state_history(spy)` labels every session from SPY alone: trend (above or below the 200-day average) and
volatility (20-day realized below or from 25% annualized): up_calm, up_volatile, down_calm, down_volatile. Because it
needs nothing but SPY, the lab and the outcome reviews can use the same label for past dates. The FRED half has no
point-in-time history before 2026-10-13 and is collected from then on.

## How it is used (owner's decision 2026-10-13: all three, shadow)

1. Teaching: the strategy lab reports each strategy's edge per market state (against a random entry in the same
   state), and Sokratis's `market_backdrop_lesson` tells each trader which of his daily strategies held up in
   today's state (>= 1 point with 100+ signals), which were weak and which have too few signals. Advisory only; the
   daily report repeats it in the macro section. Note: the 2021-2026 window is mostly up_calm, so the other states
   have small samples.
2. Measuring: `outcome_evaluator` (Giannis, Petros, quality factors) and `trader_calibration` (risk tiers) group
   outcomes by the market state of their baseline session, so they show whether a label holds up in rising and in
   falling markets.
3. Using, in shadow: `holding_class_view` records what the backdrop would mean per holding class (none for a day,
   minor for a week, leaning long/short for a month and a quarter in a supportive/hostile backdrop). Nothing applies
   it; turning it into a criterion waits for the holding-class rule and the owner's decision.
