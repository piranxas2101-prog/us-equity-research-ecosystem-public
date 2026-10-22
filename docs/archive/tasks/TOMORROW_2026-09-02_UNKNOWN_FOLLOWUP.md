# Tomorrow Task — UNKNOWN Follow-Up Benchmarks

- Date: `2026-09-02`
- Planned start: `11:00 Europe/Athens`
- Status: `complete — controlled benchmarks finished; gated follow-ups retained in backlog`
- Priority: `P0 evidence and data quality`
- Starting reports:
  - `output/unknown_analysis/unknown_completion_report.md`
  - `output/unknown_analysis/unknown_baseline_latest.json`
  - `output/unknown_analysis/agent_field_matrix_latest.md`
  - `output/unknown_analysis/provider_capability_matrix_latest.md`
  - `output/unknown_analysis/unknown_upgrade_backlog_latest.md`

## Objective

Measure which remaining UNKNOWN states can be reduced safely, then promote only upgrades
that improve point-in-time evidence coverage without fabricating values, hiding conflicts,
breaking licensing rules or expanding trading authority.

## Work Order

### 1. Frozen Benchmark Fixtures

- Freeze representative historical cases for the largest UNKNOWN contributors:
  `forward_pe`, catalyst category/direction, revenue and earnings growth, margins, ROE,
  debt-to-equity, current ratio, dilution/ATM review, concentration risk,
  `fundamental_coverage` and `priced_in_status`.
- Include profitable and loss-making issuers, financial companies, foreign issuers,
  missing CIK mappings, conflicting providers, stale filings and genuinely undisclosed data.
- Preserve `as_of`, retrieval timestamp, filing accession, source hash, provider and expected
  reason code.
- Establish baseline coverage, legitimate-not-applicable rate, conflict rate and latency.

### 2. SEC Companyfacts/XBRL Pilot

- Build a read-only adapter for selected fundamental fields.
- Preserve exact SEC fact, taxonomy name, units, period, filing accession and accepted date.
- Prevent future-period leakage and select facts using point-in-time availability.
- Compare coverage and conflicts against the current supplementary normalized source.
- SEC remains authoritative for issuer disclosures.

### 3. FMP Supplementary Fallback Pilot

- Query only fields missing after the primary route.
- Keep source identity and never overwrite a conflicting SEC value.
- Measure incremental coverage, disagreement rate, response latency, quotas and cost.
- Review plan terms, redistribution rights and suitability for a public GitHub project.
- Keep the route disabled in the daily pipeline until both evidence and licensing gates pass.

### 4. Catalyst Content Resolver Benchmark

- Create a labelled fixture set from the pending catalyst content-review queue.
- Require source-backed category, direction, evidence span, confidence and reason.
- Allow `remain_unknown_with_reason` as a correct result.
- Prohibit automatic trade direction and invented numeric facts.
- Measure classification precision, false-direction rate and unresolved rate.

### 5. Retry, Cache And Failure Injection

- Test `401`, `403`, `429`, timeout, empty response, stale response, malformed JSON,
  parse/schema error, symbol/CIK mismatch and source conflict.
- Verify bounded retry, exponential backoff where appropriate, cache identity, circuit-breaker
  behavior and exact attempt logging.
- Confirm non-retryable states do not waste quota and execution-boundary agents remain
  fail-closed.

### 6. Shadow Evaluation Of Real Agent Reports

- Evaluate real Babis, Giannis and Petros reports with the UNKNOWN rubric.
- Check whether every field request is justified and whether reason codes match the evidence.
- Compare before/after coverage and scoring without modifying historical artifacts.
- Record disagreements between agents and preserve upstream event IDs.

### 7. Promotion Decision And Final Report

- Update every backlog item to `implemented`, `pilot`, `defer` or `reject`.
- Promote only changes that pass frozen regression, point-in-time, source-conflict, licensing
  and maintenance gates.
- Produce a report with coverage gain, regressions, costs, residual UNKNOWN states and the
  next safe action.

## Required Acceptance Gates

- No fabricated or interpolated factual value.
- No future-data leakage.
- No provider value silently overriding SEC evidence.
- Every value and UNKNOWN state has source identity and `as_of` information.
- `not_applicable` remains outside the denominator and score penalty.
- Historical signals, rationales and artifacts remain unchanged.
- Information-quality evaluation remains separate from trade-performance evaluation.
- No live trading, paper trading, broker account mutation or full pipeline run is authorized
  by this task.

## Expected Deliverables

1. Frozen benchmark dataset and manifest.
2. SEC XBRL pilot report.
3. FMP fallback pilot and licensing report.
4. Catalyst resolver benchmark report.
5. Failure-injection and retry/cache test report.
6. Shadow agent-report conformance report.
7. Updated ranked backlog and final promotion decision.
