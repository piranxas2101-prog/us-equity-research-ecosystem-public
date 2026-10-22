# Readiness Baseline — 2026-08-25

This is the first evidence-based baseline before individual agent reviews. Severity follows `REPORTING_STANDARD.md`.

## Summary

| ID | Finding | Score | Status |
|---|---|---:|---|
| CAT-001 | Generic 8-K could be interpreted as a commercial catalyst | 16 High | Resolved in working tree |
| CAT-002 | Catalyst materiality and bullishness were not cleanly separated | 16 High | Resolved in working tree |
| CAT-003 | No machine-readable Long/Short category aggregation | 9 Medium | Resolved in working tree |
| OUT-001 | Outcome horizons use the latest price rather than the horizon-date price | 20 Critical | Resolved in working tree |
| CI-001 | GitHub Actions workflow contains Python followed by YAML | 16 High | Resolved in working tree |
| TIME-001 | Athens market-open value is hard-coded and can drift during DST transitions | 12 High | Resolved in working tree |
| DATA-001 | Composite scoring has no minimum fundamental-coverage gate | 16 High | Resolved in working tree |

## CAT-001 — Generic 8-K classification

- Type/status: data-quality defect / resolved in working tree.
- Condition: an issuer has a generic 8-K without a mapped SEC item.
- Expected: content review or `unknown` until evidence identifies the event.
- Prior observed behavior: the coarse filing mapping could label an 8-K as a contract/partnership.
- Problem: unsupported catalyst specificity was passed to downstream agents.
- Risk: a false catalyst can raise candidate ranking and contaminate agent reasoning. Likelihood 4, impact 4, score 16, high confidence.
- Change: SEC item metadata, deterministic form/item rules, and an explicit unknown category were added in `catalyst_taxonomy.py` and `catalyst_taxonomy.json`.
- Acceptance: `test_generic_8k_is_not_called_a_contract` passes.

## CAT-002 — Materiality versus direction

- Type/status: design limitation / resolved in working tree.
- Condition: a highly material negative event such as dilution or delisting is found.
- Expected: high attention, negative direction, and Short-lens support without an automatic order.
- Problem: a single catalyst bucket/weight could be read as positive opportunity strength.
- Risk: negative evidence can accidentally increase a long-oriented ranking. Likelihood 4, impact 4, score 16, high confidence.
- Change: attention, direction, evidence confidence, and Long/Short relevance are now independent fields.
- Acceptance: delisting and shelf-registration tests produce higher Short than Long scores.

## CAT-003 — Hierarchical machine contract

- Type/status: improvement opportunity promoted to agreed requirement / resolved in working tree.
- Expected contract: `Long|Short → category → subcategory/event → evidence`.
- Problem: downstream readers previously received a flat label and could not reconstruct how a parent conclusion was formed.
- Risk: inconsistent interpretation and weak auditability. Likelihood 3, impact 3, score 9, high confidence.
- Change: bounded child-to-parent aggregation and complete evidence fields are propagated Babis → Giannis → Petros.
- Acceptance: `test_parent_score_aggregates_category_children` passes and parent scores remain within 0–100.

## OUT-001 — Exact-horizon outcome measurement

- Type/status: software/data-quality defect / resolved in working tree.
- Condition: `outcome_tracker refresh` runs after a 1/5/20-day horizon becomes due.
- Expected: price from the target trading date (with an explicit market-calendar policy).
- Observed: `refresh_outcomes` checks elapsed calendar days and calls `latest_close`, so a late refresh stores the current latest close as the historical horizon result.
- Affected scope: all performance labels, agent evaluation, and later training based on `price_outcomes`.
- Risk: corrupted labels can teach the system that bad decisions were good and vice versa. Likelihood 5, impact 4, score 20, high confidence.
- Recommended change: store target session date, fetch the adjusted/unadjusted close according to a declared policy, record data provenance, and make refresh idempotent.
- Acceptance: a fixture refreshed several days late still records the target session’s price.
- Implemented: actual completed benchmark sessions determine the target date; raw close, target session, source, and adjustment policy are persisted. Existing databases are migrated in place.
- Reference: `outcome_tracker.py`, `refresh_outcomes` and `latest_close`.

## CI-001 — Invalid CI workflow structure

- Type/status: software/configuration defect / resolved in working tree.
- Condition: GitHub attempts to parse `.github/workflows/blank.yml`.
- Expected: a valid YAML workflow that installs dependencies and runs deterministic tests.
- Observed: the file starts with a complete Python report generator and then appends YAML workflow content.
- Risk: CI does not run, allowing syntax and contract regressions into the public repository. Likelihood 4, impact 4, score 16, high confidence.
- Recommended change: move/remove the Python content, create valid workflow YAML, run compile plus unit tests, and pin supported Python versions.
- Acceptance: a local YAML parse succeeds and GitHub Actions runs the test suite on push/PR.
- Implemented: the report generator was preserved as `nightly_report.py`; `.github/workflows/ci.yml` tests Python 3.11 and 3.12.

## TIME-001 — Market schedule and daylight saving time

- Type/status: control gap / resolved in working tree.
- Condition: US and European daylight-saving transitions occur on different dates.
- Expected: derive US market sessions from an exchange calendar and convert using timezone-aware datetimes.
- Observed: `trading_window_config.json` treats regular market open as a fixed `16:30` Athens time.
- Risk: entries, exits, or monitors can start one hour early/late during transition weeks. Likelihood 3, impact 4, score 12, high confidence.
- Recommended change: use the XNYS session calendar, `America/New_York`, and `Europe/Athens`; keep explicit early-close handling.
- Acceptance: tests cover both DST mismatch weeks and a US early-close session.
- Implemented: Eastern hours are canonical and converted at runtime; the broker clock is a hard pre-submit gate for holidays and early closes.

## DATA-001 — Fundamental coverage gate

- Type/status: control gap / resolved in working tree.
- Condition: most fundamental fields are missing but at least one available rule passes.
- Expected: below an agreed coverage threshold, exclude the issuer or cap confidence/ranking.
- Observed: `evaluate_rules` calculates coverage, while `weighted_score` combines normalized scores without enforcing minimum coverage.
- Risk: sparse-data issuers can appear comparable to fully evidenced issuers and be promoted downstream. Likelihood 4, impact 4, score 16, high confidence.
- Recommended change: add configurable minimum coverage by pipeline stage, confidence caps, and explicit exclusion reasons.
- Acceptance: a low-coverage fixture cannot enter the actionable shortlist even if its available fields pass.
- Implemented: `min_fundamental_coverage` is configurable, checkpoint-aware, reported in filters, and counted as an explicit exclusion reason.

## Release gate

These baseline blockers are resolved in the working tree but still require full local and GitHub verification before the repository can be called release-ready. Catalyst scores remain advisory-only and do not authorize execution.
