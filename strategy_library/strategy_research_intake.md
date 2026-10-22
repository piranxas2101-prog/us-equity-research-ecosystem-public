# Strategy Research Intake Framework

- Version: 1
- Updated: 2026-04-30
- Purpose: Define how outside mathematical, academic, institutional, or educational ideas become local strategy lessons without blindly copying trading systems.

## Intake Principle

We can borrow ideas, but the pipeline must own the final rules.

Every strategy or mathematical method must be translated into:

- a local hypothesis,
- objective setup rules,
- invalidation rules,
- risk/reward assumptions,
- evidence requirements,
- review fields,
- and promotion criteria.

## Intake States

### `research_note`

The idea has been collected from an approved source but has not entered trader training.

Requirements:

- Source URL and source type.
- What mathematical or market behavior it claims to explain.
- What data fields we would need to test it.
- Obvious risks, including overfitting and lookahead bias.

### `training_only`

The idea can be studied by `yankos` and `periklis`, but cannot open paper trades.

Requirements:

- Local playbook draft under `strategy_library/`.
- Clear long/short interpretation if relevant.
- Required `15Min` confirmation rule if it affects entries.
- Disqualifiers and invalidation.
- Post-trade review questions.

### `paper_candidate`

The idea can appear in reports as a candidate for controlled paper testing.

Requirements:

- Minimum historical sample review.
- No lookahead fields.
- Clear TP/SL/holding assumptions.
- Expected failure mode.
- Evidence that the idea adds information beyond existing playbooks.

### `active_paper_playbook`

The idea can influence paper orders through `yankos`, `periklis`, or `miltos`.

Requirements:

- Paper-trading audit trail.
- Trader justification in reports.
- Broker constraints checked by `thymios`.
- Position ownership tracked by `koulis`.
- Regular review against realized outcomes.

### `specialist_agent_candidate`

The strategy is mature enough to consider a dedicated strategy-specific agent.

Requirements:

- Documented playbook and math category.
- Trader training completed.
- Backtest and `overfit_guard` review completed.
- Forward paper evidence available.
- Clear boundaries: what the agent can decide and what it cannot decide.
- User approval before implementation.

### `paused` / `retired`

The idea failed, became noisy, or stopped fitting the current market regime.

Requirements:

- Reason for pause/retirement.
- Evidence from paper results or review.
- Whether it can be revisited later under a different regime.

## Required Research Fields

Every new strategy idea should answer:

- `source_name`
- `source_url`
- `source_type`: academic, institutional, regulatory, official_docs, internal_synthesis
- `hypothesis`
- `market_behavior`
- `required_data`
- `setup_rules`
- `confirmation_rules`
- `disqualifiers`
- `invalidation`
- `risk_reward_assumptions`
- `sample_size_requirement`
- `overfit_risk`
- `lookahead_risk`
- `paper_test_plan`
- `promotion_threshold`
- `retirement_rule`

## Mathematical Review Checklist

- Is the rule objective enough to be coded?
- Does it use only data that would have been available at decision time?
- Is the sample size large enough to trust the result?
- Did we test too many variations and only keep the winner?
- Does it work across regimes or only in one narrow market state?
- Does it improve timing, selection, risk control, or only add complexity?
- Does it overlap with an existing playbook?
- Can `yankos` and `periklis` explain it in plain language?

## Promotion Gate

A strategy cannot be promoted unless it passes:

- Source gate: approved source or documented internal synthesis.
- Math gate: hypothesis, evidence, and failure mode are explicit.
- Data gate: no lookahead and no missing critical fields.
- Risk gate: TP/SL/holding period are user-configurable and visible.
- Agent gate: trader can justify the idea without hallucinated claims.
- Broker gate: `thymios` can execute safely in Alpaca paper trading.
- Review gate: results are recorded and can be compared against alternatives.
- Specialist-agent gate: no dedicated strategy agent is created until the strategy is mature, reviewed, and explicitly approved.

## First Focus

The first research wave should not add another chart pattern.

It should add mathematical discipline:

- confidence scoring,
- overfit protection,
- regime detection,
- event testing,
- and risk/reward validation.

After that foundation is in place, we can safely adapt more strategies.
