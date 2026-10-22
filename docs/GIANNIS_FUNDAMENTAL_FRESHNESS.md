# Giannis: fundamental freshness is the age of the last periodic report

Decision date: 2026-10-01. Owner approved the reasoning; scoring change, documented here as required.

## Problem

`giannis_rule_based_from_shortlist.score_row` treated primary data as stale when the company had filed
*nothing* in 30 days (`sec_filing_age_days`). Stale cost about 1.55 points (+1.2 penalty and one
`no_fresh_sec_filing_30d` flag at 0.35). A company that filed its 10-Q six weeks ago has current ratios, yet
it was penalised, and the same company flipped tier as soon as it filed an unrelated 8-K. Observed: CPRT
7.23 -> 4.58, CINF 5.85 -> 8.55, GTY 3.46 -> 6.16 with identical rules and values; 109 of 1065 Giannis tiers
changed in five days.

## Rule

- Fundamental freshness = age of the last periodic SEC report (10-K, 10-KT, 10-Q, 10-QT; 20-F, 40-F).
  Amendments and 8-K/6-K do not count.
- Limits: 135 days for quarterly filers (a quarter plus the 40-45 day filing deadline, with margin); 400 days
  for annual-only filers (20-F, 40-F).
- Fresh: the recent-filing gap no longer costs points. Stale: the 1.2 penalty and a `stale_periodic_filing`
  flag apply, even when an 8-K is recent.
- Unknown (no stored report for the company): the previous any-filing rule applies unchanged.
- Unchanged on purpose: `fresh_primary_data` and `no_fresh_sec_filing_30d` keep their meaning for Koulis,
  Petros, Periklis, playbooks and the paper trader. No risk limit or execution eligibility moves.

## Source (revised 2026-10-01 after the first nightly run)

`periodic_filing.py` takes the newer of two dates:

1. the newest periodic `filed` date in the local SEC companyfacts store (`cache/sec_companyfacts`, sidecar index
   keyed by each document's sha256), and
2. the newest periodic report in the SEC daily filing calendar (`sec_filing_event` in `research_registry.db`).

Ticker -> CIK comes from the cached SEC ticker file. Why two: the first run flagged 60 companies as stale because
companyfacts lagged. EDGAR showed PPG (10-Q 2026-07-29), PYPL (2026-07-28) and ED (2026-08-06) had filed their
Q2 10-Q, but the companyfacts stored on 2026-09-30 still ended at the Q1 report. The calendar only ever supplies a
*newer* date, never an older one. The calendar was read back to 2026-05-14 (`research_registry.sec_filing_index
sync --lookback-days 140`, one small file per day).

Old evidence is conclusive only if the calendar reaches back at least as far as the limit. A 20-F/40-F is filed once
a year (limit 400 days), so 140 days of calendar cannot show that no newer one exists: such rows stay unknown
(`periodic_filing_note: calendar_window_shorter_than_limit`) and are scored by the previous any-filing rule.

## Measured effect (shortlist of 2026-09-30, dry run)

First version (companyfacts only): 1054 of 1065 rows had the date, 60 stale, of which most were false.
Revised version on the 2026-10-01 shortlist (1064 rows): 1046 fresh, 0 stale, 18 unknown; Core Watchlist 105,
Selective 267, Speculative 306, Avoid 366, Complex Security Risk 20.

## Quiet companies are not a negative catalyst (2026-10-01)

The same recency effect also entered through the catalyst: a company with no filing in the Babis window has
category `unknown` and the scorer forced `catalyst_points` to at most -0.35 (PPG: 0.39 -> -0.35, i.e. -0.74 in
one day for filing nothing). Now the -0.35 applies only when a filing (or catalyst evidence) exists but could not
be classified. No filing at all gives 0.0 points: no catalyst is the normal state and says nothing about the
fundamentals. 533 of 1064 rows were in that case.

Measured on the 2026-10-01 shortlist: Core Watchlist 105 -> 146, Selective 267 -> 262, Speculative 306 -> 282,
Avoid 366 -> 349, Complex Security Risk 20 -> 25. The Core increase is large because many names sat just under the
7.5 threshold and 0.35 moves them over; the threshold itself is unchanged. Revert: restore `filing_present` to
always true in `score_row`.

## Research readiness and Petros follow the same rule (2026-10-01, owner approved)

`research_readiness.assess` and Petros' stale-data penalty used the any-filing flag `fresh_primary_data`, so a company
with a current 10-Q but no filing in 30 days was `fresh_primary_evidence_missing`, not eligible for technical
review, and lost 1.0 in the Petros score. Both now call `research_readiness.fundamentals_current`: when Giannis
knows the age of the last periodic report (`fundamentals_fresh` True/False) that decides; when it is unknown (None)
the any-filing flag applies as before. Petros passes `fundamentals_fresh` and `periodic_filing_age_days` through.

What does not change: `fresh_primary_data` keeps its value and meaning, and the risk gate (`koulis_risk_gate.py`),
the paper trader (`alpaca_paper_trader.py`) and the playbook gate (`playbook_framework.py`) still read it directly.
So a quiet company with a current report can now reach the traders for technical review, but any later gate that
requires a filing in 30 days still blocks it; nothing becomes executable that was not before, and default and
full-universe runs still cannot submit orders. `paper_test_eligible` can now be true for such a name while the
gates above still stop it; that is fail-closed, not a path to an order.

Measured on the 2026-10-01 Petros output: technical-review eligible 341 -> 496 (155 names: 142 `watch`,
13 `manual_review`). Revert: make `fundamentals_current` return its `fallback`.

Tests: `tests/test_fundamental_freshness_gate.py`.

## Tests

`tests/test_periodic_filing.py`: parsing, index refresh, unknown stays unchanged, fresh/stale/annual cases,
downstream flags preserved.

## Follow-up

Review the first real nightly run: tier churn day to day should fall. Limits are in
`periodic_filing.MAX_AGE_DAYS`.
