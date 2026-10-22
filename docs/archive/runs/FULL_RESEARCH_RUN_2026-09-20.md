# Full research run review — 20 September 2026

For the later full-universe rerun and comparison, see [the rerun review](FULL_RESEARCH_RERUN_2026-09-20.md).

Run ID: `full_research_20260920_111956`. This was a research-only run: no LLM calls, broker calls, paper orders or live orders were made. The final control-plane receipt is `review_required`, so completion means the pipeline finished, not that every security or evidence gate passed.

| Stage | Observed result |
| --- | --- |
| Babis universe scan | 5,390 eligible symbols processed; 5,132 market-data snapshots succeeded (95.21% coverage); 71 on the shortlist. There were 255 insufficient-history exclusions and three unresolved provider errors (`FBYDP`, `NE-WTA`, `SVA`). |
| Fundamental control plane | 71 requested; 50 completed, one reused, 20 blocked, zero failed or identity-gap rows. Missing revenue and core financial evidence account for most blocks. |
| Frozen fundamental packet | 71 theses: 44 `ready`, 26 `not_decision_grade`, one `conditional`. Twenty were eligible for technical review; 12 met the packet's `execution_ready` label. This label alone does not authorize an order. |
| Downstream research | Petros reviewed 71 rows. Yankos and Periklis each evaluated 40 rows from the same run and packet hash; eight Yankos and two Periklis signals were candidates. Sokratis produced five skill and seven change proposals with `advisory_only` authority. The team report marked one company `focus_now` and five `review_next` for human research review. |
| Information and provenance | The receipt contains 191 packets, 164 open evidence gaps, 118 checkable claims, 50 thesis revisions and 71 subject aliases. Its asset graph marks `sector_context` missing and the overall research packet degraded. The immutable manifest captured 15 artifacts with zero missing files; all 15 snapshot SHA-256 hashes were rechecked successfully. |
| Validation | The research-only audit passed for Babis, Giannis, Petros, Yankos, Periklis and Sokratis (6/6). The local unit suite passed 302 tests. |

The three provider errors are real technical failures, not ordinary short-history exclusions. `FBYDP` is a preferred issue in the local exchange directory; `SVA` needs equity identity review; `NE-WTA` was absent from the dated directory export. They remain visible for a targeted data/source review. The 20 fundamental blocks also remain blocked; missing financial values were not filled by inference.

The run did not exercise the separate sector-agent pipeline, so its `sector_context` gap is still open. It also did not exercise Miltos, Koulis or Thymios, because this launch was research-only. Their older local artifacts cannot prove same-run behavior. The audit fix after this run taught the checker to read trader triggers and invalidators from the nested `paper_trade_plan`; it did not alter the run's market data or decisions. Future manifests will also capture Sokratis's output and Petros's training note.

The later [full-universe rerun](FULL_RESEARCH_RERUN_2026-09-20.md) resolved the three technical scan errors and measured sector-context integration. A research-only merge into `main` must describe its remaining evidence gaps accurately. A separate controlled paper-only test is required before claiming that Miltos, Koulis and Thymios work end to end or enabling their execution path. OpenClaw host isolation and the remaining shared-Python launchers are separate readiness items. Generated outputs, databases, secrets and local credentials remain excluded from Git.

## Follow-up repairs prepared for the next run

The SEC universe now cross-checks instrument types against the dated local exchange directory. The warrant suffix `NE-WTA` is recognized; `FBYDP` is excluded as a preferred issue by the asset master. A dated, expiring exclusion for halted `SVA` cites its [SEC annual filing](https://www.sec.gov/Archives/edgar/data/1084201/000119312526258534/sva-20251231.htm) and must be reviewed after 20 October 2026. These exclusions are recorded in a local eligibility receipt. Ambiguous `fund_candidate` listings remain eligible pending review, because that category includes listed investment-company common shares.

The SEC financial extractor now accepts standard IFRS total-revenue, cash, equity and operating-cash-flow concepts in the issuer's reporting currency. It selects the newest eligible concept, avoids mixing currencies and blocks stale income-statement periods. A bounded SEC replay of the 20 previously blocked issuers found five newly ready, six with stale income statements, eight still without a comparable total-revenue fact, and one SEC Companyfacts HTTP 404. The latter 15 remain blocked or source-unavailable; this is not a claim that all missing evidence was recovered. Old ready packets cannot be reused under the changed extraction version.

The current-run company profile now routes into a sector-agent packet where the stored taxonomy node supports it. That packet and the resulting Giannis analysis share the same canonical subject and run, with profile-to-sector-to-analysis lineage. Unverified sector KPIs remain unknown and create an explicit review gap. A two-symbol research-only integration pilot completed both financial analyses, linked both sector packets to Giannis, and correctly produced a `review_required` receipt. The full research launcher now performs a one-shot six-agent audit and sends its completion notice from the worker itself; the former 15-second polling notifier loop has been removed. The next full-universe run is needed to measure these changes at scale.

All 312 local unit tests passed after these repairs. The full research launcher requires the locked `.venv-next` Python runtime and will fail closed if it is missing. The local one-shot Gitleaks audit reported zero history and working-tree findings before publishing this change.
