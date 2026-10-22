# Full research rerun — 20 September 2026

Run ID: `full_research_20260920_170826`. The run started at 17:08 and finished at 20:55 Athens time. It was research-only: zero LLM calls in the fundamental control-plane batch, zero broker calls, and no paper or live orders. The worker finished as `completed_review_required`; the control-plane receipt is `review_required`, which accurately reports remaining evidence gaps rather than a pipeline crash.

| Measure | Earlier run `full_research_20260920_111956` | Rerun |
| --- | ---: | ---: |
| Eligible symbols processed | 5,390 | 5,026 |
| Successful market-data snapshots | 5,132 / 5,390 (95.21%) | 4,847 / 5,026 (96.44%) |
| Technical provider errors in the universe scan | 3 | 0 |
| Insufficient-history exclusions | 255 | 179 |
| Research shortlist | 71 | 68 |
| Fundamental control-plane outcomes | 50 completed, 1 reused, 20 blocked | 52 completed, 1 reused, 15 blocked |
| Same-run sector-context packets | 0 | 59 of 68 |
| Research-agent artifact audit | 6 of 6 passed | 6 of 6 passed |

The eligible population changed because the SEC universe now checks instrument type and a dated trading-halt exclusion. These before/after percentages are operational coverage, not a controlled investment-performance experiment. The old and new top-ten shortlist symbols were identical; three names dropped from the 71-name shortlist and none were added.

## What the rerun established

- The previously identified `FBYDP`, `NE-WTA` and `SVA` technical scan errors did not recur. The scan finished all 5,026 eligible symbols with zero technical errors.
- The revised financial extractor and readiness rules processed 68 shortlist requests: 52 completed, one reused, 15 blocked, zero failed and zero identity gaps. Among the 15, seven had stale income-statement periods, seven had no comparable revenue fact, and one had an unavailable SEC source. The blocked rows were not filled by inference.
- The same-run sector handoff published 59 packets. Database lineage confirms 59 company-profile-to-sector edges and 45 sector-to-Giannis-analysis edges. All 59 sector packets remain conditional because sector-specific evidence or KPIs were not fully sourced. Nine symbols were not routed: five had no taxonomy node and four had only a top-level sector. Seven of the 52 completed financial analyses therefore had no sector-context input.
- The frozen fundamental packet held 68 theses: 42 `ready`, 26 `not_decision_grade`; 20 were eligible for technical review and 12 carried the packet's `execution_ready` label. That label alone does not authorize an order.
- The control-plane receipt recorded 246 packets, 161 open evidence gaps, 121 checkable claims and 52 thesis revisions. The six-agent research-only audit passed for Babis, Giannis, Petros, Yankos, Periklis and Sokratis. All 19 immutable run artifacts were present and their stored SHA-256 hashes matched on recheck.

## Remaining review items

The `review_required` result is appropriate. The 15 financial blocks, nine unresolved sector routes and 59 conditional sector packets are visible limitations of the current evidence. Sector agents did not invent KPIs to turn these into `ready` packets. Miltos, Koulis and Thymios were deliberately outside this research-only run; their shared paper-only path remains unvalidated. The strategy scorecard is not an execution backtest and does not establish trading performance.

Post-run code repairs record an explicit `classification_unresolved` control-plane gap for a future unroutable sector row and make the summary graph derive its edges from actual packet inputs. The historical run and its immutable snapshots are unchanged. The graph repair was checked against an in-memory copy of this run's database and displayed the four actual profile/sector/financial/Giannis relationships; the unroutable-gap repair is covered by a bounded test.

The research path is suitable for review as a documented, research-only system with open evidence gaps. It is not a claim that all issuer or sector data are complete, that paper execution has passed end-to-end validation, or that live trading is ready.
