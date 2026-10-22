# Strategy Library

The machine-readable multi-perspective catalog is `../playbook_catalog_v2.json`; its routing and promotion contract is documented in `../docs/PLAYBOOK_FRAMEWORK_V2.md`.

- Version: 1
- Updated: 2026-04-27
- Purpose: Give the agents one shared, auditable strategy language built only from approved institutional and regulatory sources.

## Source Policy

- Use only official or institutional educational sources from the local allowlist.
- Do not use social media, newsletters, trading rooms, or anonymous forums as strategy inputs.
- Treat each strategy as a rule-based house playbook, not as a copied guru method.
- When a strategy is not stated directly by one source, mark it as an internal synthesis inferred from approved sources.
- Use [Mathematical Research Sources](math_research_sources.md) and [Strategy Research Intake](strategy_research_intake.md) before promoting any new idea into trader training.
- Use [Math-to-Strategy Taxonomy](math_strategy_taxonomy.md) and [Trader Math Skill Curriculum](trader_math_skill_curriculum.md) to teach the traders the mathematics behind each setup family.
- Use [Sokratis Strategy Teacher](sokratis_strategy_teacher.md) to turn approved math and strategy ideas into advisory skill proposals for the traders.
- Use [Sokratis Backtest / Event-Test Plan](sokratis_backtest_plan.md) before promoting any Sokratis research note toward paper-candidate status.

## Trader Application Policy

This library gives the traders a shared language, not a shared decision engine.

`sokratis` can teach the same mathematics or filter to multiple traders, but each trader applies it separately:

- `yankos` applies lessons to his own support-bounce / controlled-breakout style board.
- `periklis` applies lessons to his own early-accumulation / pre-run confirmation style board.
- Neither trader inherits the other trader's symbols, thesis, or execution decision from a shared lesson.
- Any overlap is reviewed as independent evidence, not as a merged board.

## Standard Strategy Frame

Every strategy in this library is organized the same way:

1. Philosophy
2. Setup Rules
3. Disqualifiers
4. Outcome Tracking
5. Analysis Template
6. Source Basis

## Research Intake

New strategy ideas must move through this path:

1. `research_note`
2. `training_only`
3. `paper_candidate`
4. `active_paper_playbook`
5. `paused` or `retired`

No outside source, even an academic one, can skip testing and paper review.

## Future Specialist Agents

When a strategy becomes mature enough, it can gradually receive its own specialist agent.

A strategy-specific agent can be created only after:

- the strategy has a documented playbook,
- the math category is mapped,
- trader training is complete,
- backtest and overfit review are complete,
- paper evidence is available,
- and the user approves the promotion.

These specialist agents should start advisory-only and should not receive broker authority by default.

## Shared Analysis Template

Every ideal swing setup should be reviewed with the same questions:

- How close is price to its local lows?
- Are fundamentals improving or at least not getting weaker?
- Is volume confirming real participation?
- Is the sector or theme waking up?
- Is support clear and nearby?
- Is there dilution risk or another structural risk?

## Current Strategies

- `support_bounce_swing`
- `controlled_breakout_swing`
- `early_accumulation_swing`
- `pre_run_breakout_confirmation`
- `failed_breakdown_reclaim`

## Training Candidates

- `vwap_reclaim_rejection`
- `opening_range_breakout_breakdown`
- `pullback_to_vwap_continuation`
- `breakout_retest_failed_breakout`
- `mean_reversion_fade_overextended`
- `gap_go_gap_fade_decision_tree`

## Sokratis Research Notes

- `sokratis_volatility_adjusted_vwap_reclaim`
- `sokratis_volatility_compression_breakout`

## Sokratis Backtest Priority

- First event-test candidate: `sokratis_volatility_adjusted_vwap_reclaim`
- Second event-test candidate: `sokratis_volatility_compression_breakout`
- Reason: VWAP reclaim directly addresses the observed trader timing problem, while compression breakout needs more parameters and therefore more overfit control.

## Notes

- `support_bounce_swing` and `controlled_breakout_swing` are directly grounded in Schwab / IBKR support-resistance and swing-trade material.
- `early_accumulation_swing`, `pre_run_breakout_confirmation`, and `failed_breakdown_reclaim` are internal rule-based syntheses inferred from the approved source set.
- Training candidates are not active execution playbooks yet. They are library lessons that must be converted into code, tested, and reviewed before they can open paper trades.
- The library is meant to improve consistency, not to force every agent into the same exact behavior.
