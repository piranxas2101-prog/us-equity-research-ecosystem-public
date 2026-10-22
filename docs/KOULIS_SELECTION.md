# Koulis selection: standing inside each strategy, at most two per strategy

Owner's decision 2026-10-15 (option C), after the first paper session.

## What happened

On 2026-10-15 Koulis approved six intents, all Yankos `support_bounce_swing` longs with score 10.0. Koulis ranked
every candidate of both traders by the raw signal score, and the strategies score on different scales:
`support_bounce_swing` gave 10.0 to eight names that day, while no other strategy reached 10 (momentum 9.9,
early accumulation 9.65, rsi2 9.6). The raw ranking therefore handed every slot to one strategy, although the
five-year lab rates its day edge among the weakest (+0.4 points against a random entry;
`docs/STRATEGY_LAB_FINDINGS_2026-10-13.md`).

## The rule

1. **Standing inside the strategy.** Each candidate gets `strategy_rank_pct`: the mid-rank percentile of its score
   among the same (agent, playbook, direction) candidates of the day. Being near the top of one's own strategy is
   comparable across strategies; a strategy with a single candidate stands at 0.5, since the top of one is weaker
   evidence than the top of a hundred. Ties keep the raw score as the next key.
2. **At most two approved intents per (agent, playbook)** (`--max-per-strategy 2`; blocked rows carry
   `portfolio_strategy_limit`).

Unchanged: every eligibility check, the order, notional and short limits, the duplicate-symbol and opposite-
direction rules, existing-exposure blocks and Thymios validation. `--selection score --max-per-strategy 0`
restores the old raw-score order.

Replay of 2026-10-15 with the new rule: REGN (early accumulation), AMAT (rsi2), LIF (momentum short), ASND (early
accumulation), CASS and KARO (support bounce) - four strategies of both traders instead of six support bounces.

## Fresh primary data (same day, owner's decision)

`stale_primary_data` blocked 117 candidates (85 companies) on 2026-10-15, every one with a current 10-Q/10-K: the
old flag means "any SEC filing in the last 30 days", which a company quiet between two quarterly reports fails
(481 of 1,068 shortlisted companies). Koulis and the paper trader now share `primary_data_freshness.py`: Giannis's
periodic-report freshness (`fundamentals_fresh`, which the traders now pass along) decides, and only when it is
unknown does the old 30-day flag decide. Replay of 2026-10-15: 0 stale blocks.
