# Ecosystem redesign: status of the owner's principles (2026-10-01)

Principles stated by the owner on 2026-10-01 and what has been done about each. "Off" means built and tested but not
switched on until it has been tried on real providers.

| Principle | Status | Where |
| --- | --- | --- |
| Babis classifies and routes; other agents analyse; each finds the information through Babis | Role table and flow updated; the stage-1 rules decide shortlist membership only, ranking and tiers are Giannis' | `PIPELINE_OVERVIEW.md` |
| SEC is primary, yfinance secondary and labelled | Done in Giannis (SEC-first rules, yfinance fallback `yfinance_secondary`, `forward_pe` stays analyst data). On | `docs/SEC_FIRST_FUNDAMENTALS.md` |
| Fundamental freshness is the last periodic report, not any filing | Done in Giannis, readiness and Petros; the any-filing flag is unchanged for the risk gate and the paper trader | `docs/GIANNIS_FUNDAMENTAL_FRESHNESS.md` |
| Shortlist like an index: entry and exit by rules, no nightly flipping | Done: entry 70, exit 55, ledger of additions and removals with reasons. On | `docs/SHORTLIST_MEMBERSHIP.md` |
| Agents work concurrently | Stage-1 parallel fetch built and tested (identical output); **off** (`stage1_workers: 1`) until tried on real providers | `docs/STAGE1_PARALLEL_FETCH.md` |
| Fix the empty qualitative layer | Not done: needs a decision, see below | this file |

## Where concurrency pays

From the run of 2026-10-01 (4 h 04 min in total): the Babis scan took 3.5 h (price history 68 min, yfinance info and news
1.5 h, SEC recent filings 31 min); the profile, sector, SEC financials and analysis batch about 30 min; every other step
(Giannis, ledger, Petros, packet, traders, outcomes, reports) together under 3 min. Running those steps side by side
would save minutes, not hours, so the pipeline-level orchestration was left as it is. The time is inside the Babis
scan, which is why the parallel fetch is the lever. Streaming companies from the scan into the batch is not worth its
complexity at 30 minutes.

## Qualitative layer (open)

All 958 sector-context packets have every field unknown (products, peers, risks, KPIs) and 871 of 1065 profiles lack a
description, products, headquarters or website. The 12 sector agents ran once on 2026-09-17 with no network calls and are
not in the daily run. Two routes, in the order I would take them:

1. **Deterministic profile fields from the SEC** (no LLM): the SEC submissions document of each company carries the
   business address (headquarters), SIC description, fiscal year end, website, former names and exchanges. One request per
   company, about 1100 requests at under 7 a second (3 minutes), refreshed rarely. This fills the profile fields from
   the primary source and needs only a new collector plus a profile mapping.
2. **Filing text** (products, risks, KPIs): the local-Ollama extractor (`filing_llm_extractor.py`, qwen2.5:3b) found 14
   correct, 11 partly right and 5 wrong of 30 items in the owner-reviewed pilot, and never answers "none"; it is not
   ready for promotion. A larger local model or stricter quote rules would be the next experiment, judged on a labelled
   sample before anything reads it. These are candidate evidence for human review, not decisions.

Decision needed from the owner: start with route 1 (a daily or weekly SEC submissions collector wired into the profile
step), and which local model to try for route 2.

## Findings worth knowing

- `execution_eligible` on a trader row means only that the **fundamental thesis** is execution-ready
  (`enforce_execution_gate` copies `fundamental_execution_ready`); it says nothing about the trader's own verdict.
  That is why about half of the rows are `execution_eligible` while most verdicts are reject or watch. An order still
  needs a `candidate` verdict and passes the risk gate and the paper trader, which read `fresh_primary_data` themselves.
- `trader_experiments` stores no `confidence_probability` (all null), only the High/Medium/Low label. `trader_calibration.py`
  checks the labels against realised outcomes; on the 2026-10-01 data they barely separate (Yankos 1d: High 44.2 % vs
  Medium 44.0 % wins; at 3d the order inverts).
