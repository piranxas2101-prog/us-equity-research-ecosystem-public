# Trader Math Skill Curriculum

- Version: 1
- Updated: 2026-04-30
- Purpose: Teach `yankos` and `periklis` the mathematical foundations needed to create safe internal skills.

## Learning Objective

The traders should not memorize internet strategies. They should learn why a setup might work, when it fails, and what evidence is required before it can affect paper trading.

## Trader Roles

### `yankos`

Primary learning bias:

- practical execution,
- momentum confirmation,
- support-bounce behavior,
- breakout timing,
- avoiding late entries.

Best first math modules:

- risk/reward and expected value,
- support/reclaim probability,
- trend continuation statistics,
- range breakout event testing.

### `periklis`

Primary learning bias:

- cleaner confirmation,
- early accumulation,
- avoiding noisy setups,
- timing discipline,
- false-breakout protection.

Best first math modules:

- candle feature extraction,
- false-positive and small-sample risk,
- level detection,
- overfit guard,
- volatility regime detection.

## Shared Lessons

### Lesson 1: Expected Value and Risk/Reward

Questions each trader must answer:

- What win rate is needed if TP is 6% and SL is 3%?
- Does the setup justify the risk before entry?
- Is the stop logically tied to invalidation?

Future skill:

- `risk_reward_validator`

### Lesson 2: Objective Candle Features

Questions each trader must answer:

- Is this candle actually bullish/bearish by measurable features?
- Is the candle body meaningful relative to the full range?
- Is the close near the high/low or in the middle?

Future skill:

- `candle_feature_extractor`

### Lesson 3: Event Testing

Questions each trader must answer:

- What happened after this pattern historically?
- Is the post-signal return better than normal baseline behavior?
- Is the sample size large enough to matter?

Future skill:

- `pattern_event_tester`

### Lesson 4: Regime Awareness

Questions each trader must answer:

- Is the market trending, ranging, volatile, or news-driven?
- Does this strategy historically work in this regime?
- Should the trader reduce confidence or skip?

Future skill:

- `regime_detector`

### Lesson 5: Overfit Discipline

Questions each trader must answer:

- Did this idea win because we tested many versions?
- Was it checked outside the sample that discovered it?
- Has it shown forward paper evidence?

Future skill:

- `overfit_guard`

## Skill Creation Rule

A skill can be created only when:

- the math category is documented,
- the strategy family is identified,
- the data fields are available,
- the expected failure mode is written,
- the skill starts as advisory,
- and paper evidence is collected before automation.

## First Skills To Build

1. `risk_reward_validator`
2. `candle_feature_extractor`
3. `pattern_event_tester`
4. `regime_detector`
5. `overfit_guard`

These skills should support the traders. They should not replace trader reasoning.

