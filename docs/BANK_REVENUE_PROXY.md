# Bank revenue proxy in the SEC companyfacts extractor

Decision date: 2026-10-01 (item 3 of the analysis of 2026-09-29: banks/insurers lacked revenue).

## Problem

Most banks tag no `Revenues` / `RevenueFromContract...` fact, so `sec_xbrl_pilot.extract_companyfacts`
left `revenue` unknown and the financial evidence packet carried a `revenue_missing` blocker.

## Rule

When no revenue concept resolves, `bank_revenue_proxy` sums `InterestIncomeExpenseNet` (net interest income) and
`NoninterestIncome` of the **same filing and period** (same unit, period start/end and accession). Net rather
than gross interest income is the banking convention for revenue and does not reward a high-rate balance sheet.

- Reported revenue always wins; the proxy is used only when that field would otherwise be unknown.
- Labelled: `proxy: true`, `proxy_method`, both `components`, `concept` = `InterestIncomeExpenseNet+NoninterestIncome`.
- Any missing component, or components from different filings/periods, leaves revenue unknown.
- Same point-in-time cutoff and newest-period selection as every other field (so, like reported revenue, the
  value can be a quarter; `period_start`/`period_end` show the span).

## Measured on the local store (1056 companies, as of 2026-10-01)

1015 extractions are byte-identical to before; 41 banks gain a proxy and nothing else changes. 26 companies stay
without revenue (BDCs, foreign filers without US-GAAP/IFRS revenue facts, companies with no XBRL): unknown stays
unknown. Insurers were not covered by a separate proxy; those that report `Revenues` already have it.

Tests: `tests/test_bank_revenue_proxy.py`.
