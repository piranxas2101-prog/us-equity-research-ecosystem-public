# SEC-first fundamentals in Giannis

Decision date: 2026-10-01. Owner principle: the SEC is the primary, reliable source; yfinance is secondary and lags
(its articles and ratios often echo what the SEC published days earlier). A yfinance value must never override or
outrank a SEC-backed fact.

## Problem

The ratios behind the shortlist and the Giannis base score (roe, profit_margin, gross_margin, revenue_growth,
earnings_growth, debt_to_equity, current_ratio, forward_pe) came only from yfinance `info` in Babis stage 1. SEC
companyfacts were used later, in the control-plane analysis, and never for these rules.

## Rule

`sec_fundamental_ratios.py` computes the same ratios from the stored SEC companyfacts (TTM for flows, latest balance
sheet; definitions in its docstring). `sec_first_rules` re-evaluates the **same rules, thresholds and weights** with the SEC
value where there is one, and with the yfinance value only as a fallback labelled `yfinance_secondary`. `forward_pe`
is analyst data with no SEC equivalent and stays yfinance, labelled secondary. A missing value stays missing and, as in
stage 1, stays in the denominator of the score.

Giannis always reports `fundamental_score_sec_first`, `fundamental_rule_details_sec_first` (with `value_source` per rule)
and `fundamental_value_sources`. When `pipeline.giannis_sec_first` is true in `scanner_config.json` (now true) the SEC-first
score drives the Giannis base points (`fundamental_attention_score`, basis `sec_first`); the Babis stage-1 value is kept
in the shortlist and still decides entry to the shortlist (membership). Rows without a SEC document keep the Babis score.
CLI override: `--sec-first on|off|config`. Revert: set the key to false.

## Measured (shortlist of 2026-10-01, 1064 rows, 1056 with a SEC document)

Per rule, SEC and yfinance give the same pass/fail verdict in: current_ratio 98.6 %, profit_margin 93.2 %,
debt_to_equity 92.7 %, gross_margin 90.4 %, roe 88.1 %, revenue_growth 81.6 %, earnings_growth 78.1 %. 5833 of 8512
rule evaluations use a SEC value, 2494 fall back to yfinance, 185 stay missing. The stage-1-style score differs by more than
10 points for 385 companies (262 lower, 123 higher), but the Giannis base is capped at 4 of 10 points, so 172 of 1064
Giannis tiers change: Core 146 -> 131, Selective 262 -> 260, Speculative 282 -> 271, Avoid 349 -> 380.

## Known differences

- Growth is the latest year-to-date against the same period a year earlier; yfinance reports the latest quarter.
- `debt_to_equity` uses long-term debt only (SEC has no single total-debt fact), a lower bound; yfinance totalDebt
  includes short-term debt and leases.
- Banks and companies without a gross-profit fact leave `gross_margin` to yfinance or missing.
These are why the agreement on growth is lowest. Use `python sec_fundamental_ratios.py` to refresh the per-rule comparison.

Tests: `tests/test_sec_fundamental_ratios.py`, `tests/test_periodic_filing.py` (Giannis switch).
