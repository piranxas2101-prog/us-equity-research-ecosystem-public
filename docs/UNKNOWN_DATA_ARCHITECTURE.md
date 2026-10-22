# UNKNOWN Data Architecture

## Purpose

The ecosystem preserves `UNKNOWN` when evidence is missing, but no longer treats every
absence as the same problem. A versioned event explains the cause, source route, retry
policy, coverage effect, decision effect and responsible agent.

## Contract

`unknown_data_contract.json` defines:

- eleven reason codes;
- provider capabilities and fallbacks;
- required, conditional, optional and diagnostic-only fields for all nine agents;
- field-level decision impact and missing-data action;
- fail-closed requirements at the execution boundary.

The contract is verified before the daily pipeline starts.

## Reason Codes

- `not_applicable`
- `not_disclosed`
- `insufficient_source_detail`
- `provider_no_coverage`
- `temporarily_unavailable`
- `stale`
- `symbol_mapping_error`
- `parse_or_schema_error`
- `conflicting_sources`
- `contract_mismatch`
- `unnecessary_requirement`

Only required or applicable conditional fields can reduce coverage. Diagnostic fields and
`not_applicable` values carry zero penalty.

## Fundamental-Lane Flow

1. Babis creates reason-coded events for missing discovery/fundamental evidence and every
   top-level literal UNKNOWN.
2. Giannis preserves Babis events, creates his own events and uses weighted penalty units
   instead of raw unknown-field count.
3. Petros preserves the upstream trace and creates separate readiness/valuation events.
4. The information ledger stores the complete immutable event trace.
5. The frozen Fundamental Research Packet carries the trace by agent without changing the
   source events.

## Identity And Catalyst Controls

- SEC filing collection requires an exact ticker-to-CIK mapping. Missing identity is a
  visible `symbol_mapping_error`; the system does not guess another issuer.
- Source-backed but unclassified filings enter `catalyst_content_review_queue`. The queue
  may return a source-backed classification or retain UNKNOWN. It cannot invent numeric
  facts or produce an automatic trade direction.

## Provider Authority

- SEC is authoritative for issuer disclosures.
- Alpaca and Polygon provide market-data observations and cross-checks.
- FMP and OpenBB/yfinance are supplementary and cannot override conflicting SEC evidence.
- FRED and EIA are authoritative distributors for their macro and energy series.
- Benzinga remains disabled while authentication fails.

## Evaluation

The frozen baseline and generated matrices are under `output/unknown_analysis/`:

- `unknown_baseline_latest.*`
- `unknown_events_latest.csv`
- `agent_field_matrix_latest.*`
- `provider_capability_matrix_latest.*`
- `unknown_upgrade_backlog_latest.*`

Information quality remains separate from trade-performance evaluation. Reducing UNKNOWN
does not imply a better trade and cannot be used to rewrite historical rationales.
