# Research Task — Explainable UNKNOWN Reduction

- Status: `completed_research_and_immediate_controls_implemented`
- Priority: `P0 data architecture`
- Created: `2026-09-01`
- Scope: all nine agents and every data handoff
- Safety rule: never replace missing evidence with an estimate merely to reduce the UNKNOWN count

## Problem Statement

`UNKNOWN` currently protects the ecosystem from invented certainty, but it groups together
different situations. A field may be genuinely undisclosed, irrelevant to the instrument,
temporarily unavailable, stale, rejected by a parser, requested under the wrong name, or
unnecessarily required by an agent. Treating all of these as one state hides the corrective
action and makes coverage harder to improve.

The research must determine whether the high UNKNOWN rate is caused by what agents are
asked to inspect, by provider coverage, by symbol/instrument classification, by freshness
rules, by parsing and mapping, or by an incorrect required-versus-optional contract.

## Central Research Question

For every field requested by every agent:

1. What decision is this field allowed to influence?
2. Why is it required for that agent and stage?
3. Is it universally required, or only for a sector, instrument type, catalyst, playbook or
   holding horizon?
4. Which primary and fallback sources can legitimately provide it?
5. What freshness window is appropriate?
6. What does its absence mean: `not_applicable`, `not_disclosed`, `temporarily_unavailable`,
   `stale`, `provider_gap`, `mapping_error`, `parse_error`, `conflict`, or `contract_error`?
7. Should absence block the stage, reduce confidence, trigger retry, route to manual review,
   or have no effect?

## Workstream 1 — Baseline And UNKNOWN Inventory

- Scan existing artifacts without starting a new full market pipeline.
- Count UNKNOWN values by `run_id`, agent, stage, field, ticker, sector, industry, instrument
  type, provider and data age.
- Separate operational errors from legitimate missing disclosure and from `not_applicable`.
- Identify the top fields responsible for 80% of avoidable UNKNOWN results.
- Measure how each UNKNOWN affected eligibility, information score, confidence, ranking,
  thesis readiness, technical readiness, risk approval or contract validation.
- Preserve the original artifact and rationale; do not rewrite historical outputs.

## Workstream 2 — Required-Field Review By Agent

Each agent must produce a field-justification report containing:

- fields read and the artifact/schema location;
- decision or transformation supported by each field;
- `required`, `conditional`, `optional` or `diagnostic_only` classification;
- sector, instrument, catalyst, playbook and horizon applicability;
- primary source, fallback source and provenance requirements;
- freshness requirement and acceptable reporting lag;
- missing-data state and downstream effect;
- fields that are duplicated, weakly justified or outside the agent's role;
- proposed additions, removals and renames.

Review order:

1. Babis — discovery inputs, catalyst evidence and source coverage.
2. Giannis — financial quality, balance sheet, cash flow, dilution and structural-risk fields.
3. Petros — valuation, priced-in state, prove/kill criteria and synthesis requirements.
4. Yankos and Periklis — technical/playbook data required after the frozen fundamental packet.
5. Sokratis — whether requested features have a testable mechanism and point-in-time evidence.
6. Miltos — bar freshness and exact frozen-trigger fields.
7. Koulis — account, exposure, conflict, sizing and portfolio freshness.
8. Thymios — identity, schema and invariant fields only.

## Workstream 3 — UNKNOWN Reason Taxonomy

Replace the undifferentiated state with a reason-coded envelope while preserving `UNKNOWN`
as the safe value:

| Reason code | Meaning | Default treatment |
|---|---|---|
| `not_applicable` | Field does not apply to this instrument or scenario | Exclude from coverage denominator |
| `not_disclosed` | Issuer or official source does not disclose it | UNKNOWN; no retry unless a new filing appears |
| `provider_no_coverage` | Selected provider does not cover it | Try approved fallback or record provider gap |
| `temporarily_unavailable` | Timeout, rate limit or temporary provider failure | Bounded retry; then fail visibly |
| `stale` | Value exists but exceeds its freshness contract | UNKNOWN/stale; block or reduce confidence by stage |
| `symbol_mapping_error` | Ticker, CIK, share class or instrument identity mismatch | Block and send to identity repair queue |
| `parse_or_schema_error` | Source exists but ingestion failed | Quarantine artifact and create actionable error |
| `conflicting_sources` | Valid sources disagree materially | Preserve both values and route by conflict policy |
| `contract_mismatch` | Upstream/downstream field or version mismatch | Fail closed; repair the contract, not the value |
| `unnecessary_requirement` | Agent requests a field that its decision does not need | Remove or demote the requirement after review |

Every UNKNOWN event should record at minimum:

`run_id`, `agent`, `stage`, `ticker`, `field`, `reason_code`, `provider`, `source_id`,
`source_as_of`, `freshness_limit`, `attempts`, `fallbacks_tried`, `retryable`,
`decision_impact`, `coverage_impact`, `owner` and `resolution_status`.

## Workstream 4 — Upgrade Proposals To Evaluate

- Conditional schemas by instrument type, sector, catalyst category and playbook.
- Explicit `required/conditional/optional/diagnostic_only` field contracts.
- Provider capability matrix and field-level source routing instead of one generic fallback.
- Bounded retry, backoff, caching and circuit-breaker policies by reason code.
- Symbol/CIK/share-class identity service before fundamental collection.
- Point-in-time freshness rules based on the natural reporting cadence of each field.
- Direct SEC/XBRL and issuer-source extraction for fields where third-party normalization
  causes avoidable gaps.
- Conflict preservation with source priority and manual-review routing rather than silent
  winner selection.
- Coverage denominators that exclude `not_applicable` and keep operational failures separate.
- An UNKNOWN dashboard by agent, field, provider and impact, plus a resolution backlog.
- Regression fixtures for every approved reason code and fallback path.

Every proposed tool or provider must include expected coverage benefit, fields improved,
latency, licensing/data rights, cost, rate limits, security, maintenance burden and exit plan.

## Requested Agent Contributions

- Babis and Giannis: identify the exact discovery/fundamental fields producing the most
  UNKNOWN values and challenge whether each one belongs in the required set.
- Petros: document which missing fields genuinely prevent a thesis and which only reduce
  confidence.
- Yankos and Periklis: document technical fields that are conditional on their active
  playbooks rather than universally required.
- Sokratis: design the baseline, attribution method and regression experiment; reject any
  upgrade whose improvement cannot be measured without leakage.
- Miltos, Koulis and Thymios: define fail-closed UNKNOWN states that must never be relaxed
  because they protect execution safety.

## Deliverables

1. UNKNOWN baseline report and Pareto table.
2. Agent-by-field justification matrix.
3. Provider-by-field capability and fallback matrix.
4. Versioned UNKNOWN reason-code schema.
5. Ranked upgrade backlog: `must_fix`, `pilot`, `defer`, `reject`.
6. Before/after fixtures and measurement plan.
7. Final recommendation identifying which UNKNOWN states should be reduced, reclassified,
   preserved or made stricter.

## Acceptance Criteria

- 100% of observed UNKNOWN values receive a reason code.
- 100% of requested fields have an owner, decision purpose and applicability rule.
- `not_applicable` never lowers coverage.
- Operational errors never appear as legitimate missing disclosure.
- No fallback invents, interpolates or silently repairs an evidence value.
- Safety-critical UNKNOWN states for Miltos, Koulis and Thymios remain fail-closed.
- A measurable reduction target for avoidable UNKNOWN is set only after the baseline, then
  verified on frozen regression data before any daily-pipeline promotion.
- Information-quality and trade-performance scoreboards remain separate.

## Completion Gate

Do not change the meaning of UNKNOWN or relax a downstream gate until the baseline,
field-justification matrix, reason taxonomy and regression fixtures have been reviewed.
The objective is more explainable and recoverable missing data—not artificial certainty.

## Completion Record — 2026-09-01

- Implemented `unknown_data_contract.json` with eleven reason codes and field contracts for
  all nine agents.
- Implemented `unknown_data.py` for deterministic event creation, applicability-aware
  coverage, weighted penalties and contract verification.
- Integrated reason-coded UNKNOWN traces into Babis, Giannis, Petros, the information ledger
  and the frozen Fundamental Research Packet.
- Implemented a fail-closed SEC ticker/CIK identity gate with no guessed identity.
- Implemented a non-executing catalyst content-review queue for source-backed but unclassified
  filings.
- Generated the historical baseline/Pareto, agent-field matrix, provider capability matrix
  and ranked upgrade backlog under `output/unknown_analysis/`.
- Verified current read-only provider status without exposing credentials. SEC, Alpaca,
  Polygon, FMP, FRED and EIA responded; Benzinga returned 401 and remains deferred.
- Direct SEC XBRL, FMP field-level fallback and field-level retry/cache work remain controlled
  pilots because their coverage, point-in-time correctness, licensing and quota cost must be
  proven on frozen benchmarks before promotion.
