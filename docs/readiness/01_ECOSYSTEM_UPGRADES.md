# Ecosystem Upgrade Implementation

Updated: 2026-09-02

## Implemented controls

| Control | Result | Primary evidence |
|---|---|---|
| Research-only default | Paper submission now requires `-SubmitPaper` | `run_daily_dual_watchlist.ps1` |
| Portfolio risk gate | Koulis resolves cross-agent direction conflicts, duplicates, existing exposure and total limits | `koulis_risk_gate.py` |
| Broker intent validation | Thymios validates schema, session, run, identity, uniqueness and notional limits | `thymios_order_intent.py` |
| No Miltos bypass | Waiting setups require the same approved intent | `miltos_entry_stalker.py` |
| Run lineage | Babis, Giannis and Petros create one compatible fundamental run; both traders must consume its same-run frozen packet | `run_context.py`, `fundamental_research_packet.py` |
| Immutable audit snapshots | Principal artifacts and configuration hashes are captured per run | `run_manifest.py` |
| Fundamental/technical separation | Petros excludes trader, technical and model inputs from the default review and freezes thesis identity before the trader stages | `petros_agent.py`, `fundamental_research_packet.py` |
| Layered meeting score | Fundamental packet eligibility is applied first; only then are the independent technical views compared for reporting | `run_babis_giannis_meeting.ps1` |
| Catalyst handoff | Long/Short catalyst parent scores become a small advisory lens for both traders | `catalyst_trade_lens.py` |
| Clean learning objective | Petros trains on market outcomes by default; subjective feedback is opt-in | `petros_trainer.py` |
| Honest evaluation label | Strategy output is explicitly a signal outcome scorecard, not an execution backtest | `strategy_backtest.py` |
| Research readiness | Outputs distinguish idea triage, valuation gaps, paper testing and live-capital readiness | `research_readiness.py` |
| Pre-Koulis eligibility partition | `watch`/`reject`/non-executable observations remain visible as `prefiltered_observations`; only genuine execution candidates enter portfolio risk evaluation | `koulis_risk_gate.py` |
| Explicit zero-intent completion | A valid run with no eligible orders is recorded as `completed_no_eligible_intents`, not as an ambiguous success or failure | `koulis_risk_gate.py` |
| Standalone Thymios evidence | Every run writes a session-fresh validation artifact, including `validated_no_intents` and safe upstream refusal | `thymios_order_intent.py`, `agent_run_audit.py` |
| Standalone Miltos no-op evidence | When no monitor is required, Miltos writes a current-run `completed_noop` artifact with a reason and empty action lists | `miltos_entry_stalker.py`, `start_trader_paper_trading_today.ps1` |
| Actionable readiness diagnostics | Fundamental blockers now include category, missing input, primary blocker and concrete resolution action, plus packet-level aggregate counts | `research_readiness.py`, `fundamental_research_packet.py` |
| Evidence-backed priced-in triage | Petros now produces a dated, hashed expectations-burden assessment from current price, valuation, growth, profitability, balance-sheet and catalyst inputs; insufficient or stale inputs fail closed | `priced_in_assessment.py`, `petros_agent.py`, `research_readiness.py` |
| Required-field liveness audit | A universally missing required field with zero recorded attempts makes agent conformance partial and exposes the provider route and affected row count | `agent_run_audit.py`, `unknown_data.py` |
| Verdict transition ledger | Technical verdicts preserve their pre-gate and post-gate values plus every fundamental filter reason before Koulis prefiltering | `fundamental_research_packet.py`, `koulis_risk_gate.py` |

## Safety invariants

1. Research generation never implies authorization to submit an order.
2. Paper submission requires a current Koulis-approved intent that passes Thymios validation.
3. One symbol can have at most one approved opening per run.
4. Opposing Long/Short agent proposals for the same symbol are blocked, not averaged.
5. Existing broker exposure blocks a new autonomous batch by default.
6. Trader budgets are portfolio-wide, not duplicated per agent.
7. `*_latest` is not accepted as historical evidence without a run manifest.
8. No current output is labeled live-capital eligible.
9. A trader candidate cannot reach Koulis without a passed fundamental gate and immutable thesis identity.
10. Non-candidate research observations do not inflate Koulis rejection metrics; they remain auditable in the prefilter ledger.
11. A zero-order run is a first-class, session-fresh outcome for Koulis, Thymios and Miltos.
12. A priced-in label cannot unlock paper execution without dated evidence, a valid evidence hash and at least Medium confidence.
13. The priced-in engine is screen-grade and paper-only; it is not a fair-value model or price target.

## Remaining work before a public trading-platform claim

- Complete the planned one-by-one audit and evaluation suite for every agent.
- Add portfolio exposure by sector, factor, liquidity and correlated thesis—not only order/notional counts.
- Build a genuine event-driven execution backtester with fees, slippage, borrow availability and intraday path metrics.
- Add model-backed fair-value ranges, peer/consensus expectation anchors, thesis KPIs and catalyst calendars; the current priced-in engine is deliberately screen-grade.
- Run repeatable shadow/paper trials over enough independent market regimes and publish the methodology.
- Add secrets scanning, dependency pinning, release versioning, contribution policy and public security policy.
