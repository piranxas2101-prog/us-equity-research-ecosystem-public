# Agent follow-up research — 20 September 2026

Status: planning note only. Review after the current full-research run and its repairs; no agent behavior or broker authority changes are authorized by this note. Sokratis's existing role is explicitly retained.

## Proposed order

1. **Information completeness:** The 11 sector agents produce sourced business-model, sector KPI, competitor, risk and unknown-field context. The unresolved-classification agent proposes evidence-backed labels to Babis only. Petros turns the same-run evidence into a concise human research brief: what changed, countercase, prove/kill criteria and review queue. Do not mix trader outputs into Petros's fundamental verdict.
2. **Independent trader interpretation:** Yankos reports trend, confirmation trigger, invalidation and wait/flat alternatives. Periklis reports supply-demand phase hypotheses, competing interpretations and confirmation requirements. Keep separate boards and display disagreements rather than forcing consensus.
3. **Separate paper-only controls:** Miltos evaluates only predeclared waiting triggers on a new relevant event and records waiting/confirmed/blocked/expired. Koulis explains cross-agent conflicts, current exposure, caps and blocks. Thymios checks exact same-session order-intent identity, freshness, limits and duplicates, returning accept/reject reasons. None of these roles originates a trade or expands broker access.

## Shared information contract

Every handoff should carry `run_id`, canonical issuer/security identity, as-of and observation times, source IDs, evidence status and gaps, packet hash, owner, and next recipient. An outdated, cross-run or unsupported claim remains visibly unknown or blocked. The human brief should contain only material changes, disagreements, important gaps and links to the underlying packets.

## Evaluation after the current run

- Measure sector-context coverage and provenance, Petros packet completeness, cross-run consistency, trader disagreement/overlap and reasons for abstention.
- Compare with the previous full-run baseline before changing agent rules. Record per-run metrics and immutable artifacts.
- Validate Miltos/Koulis/Thymios in a separate, bounded paper-only scenario with duplicate, stale-data and broker-failure fixtures. No recurring LLM monitoring loop.

## Research patterns to adapt locally

- [TradingAgents](https://github.com/TauricResearch/TradingAgents): bounded specialist roles, counterarguments and point-in-time integrity.
- [OpenBB standardization](https://docs.openbb.co/odp/python/developer/standardization): shared provider input/output models.
- [Dagster asset checks](https://docs.dagster.io/guides/test/asset-checks): explicit quality gates on downstream packets.
- [Qlib recorder](https://github.com/microsoft/qlib/blob/main/docs/component/recorder.rst): run-level metrics and artifacts.
- [LEAN framework](https://github.com/QuantConnect/Lean/blob/master/Algorithm/QCAlgorithm.Framework.cs): separate portfolio risk and execution roles.
- [alpaca-py](https://github.com/alpacahq/alpaca-py): paper trade updates and client-order-ID reconciliation for later controlled tests.

These are design references, not runtime dependencies or account connections.
