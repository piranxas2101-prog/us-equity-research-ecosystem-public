# Next Day Tasks

- Last updated: 2026-09-01
- Applies to: phased platform-readiness roadmap
- Purpose: Review and improve every agent, then improve the full multi-agent collaboration before a public GitHub release.

## First Task — 2026-09-03: Research-Scoped Background Services

### Status: Completed on 2026-09-02

- `MarketResearchSlackGateway` and `MarketResearchSlackHealth` remain disabled.
- The Slack gateway and health monitoring now start only with `start_research_session.ps1`.
- `stop_research_session.ps1` stops the session owner and its gateway child.
- Periodic health checks run windowlessly inside `research_session_service.py`.
- Slack authentication, stale-event, and overdue-Notion checks remain active only during research.
- Lifecycle, crash-recovery, restart-limit, Windows PID, and no-orphan tests were added.
- Live start → Slack response → stop verification ended with zero research/Ollama processes.
- The workflow remains research-only; no broker execution authority was added.

## Tomorrow Focus — 2026-09-02 At 11:00

- Execute the controlled UNKNOWN follow-up benchmark plan in
  `docs/tasks/TOMORROW_2026-09-02_UNKNOWN_FOLLOWUP.md`.
- Start with frozen fixtures; do not start a new full market pipeline.
- Benchmark direct SEC Companyfacts/XBRL extraction and an FMP supplementary fallback.
- Build the labelled catalyst content-resolver evaluation.
- Add retry/cache failure injection for provider, schema, identity, freshness and conflict
  failures.
- Shadow-grade real Babis, Giannis and Petros reports using the UNKNOWN rubric.
- Update the backlog with `implemented`, `pilot`, `defer` or `reject` decisions and produce
  a final evidence-based promotion report.
- No live/paper trading and no provider promotion without point-in-time regression and
  licensing approval.

## Task 0 — Research And Reduce Avoidable UNKNOWN States

### Status

- Completed on 2026-09-01: research baseline, reason taxonomy, immediate controls,
  handoff traceability, identity gate, content-review queue, reports and regression tests.
- Controlled provider/XBRL pilots remain in the ranked upgrade backlog and require frozen
  coverage/licensing benchmarks before promotion.

### Priority

- Priority 0 data-architecture task. Diagnose before changing prompts, scores or safety gates.

### Goal

- Determine whether UNKNOWN values are caused by genuinely unavailable evidence or by the
  fields requested from each agent, provider coverage, symbol mapping, freshness, parsing,
  schema mismatch or unnecessary requirements.
- Preserve UNKNOWN as a safe output when evidence is genuinely missing; never replace it
  with an estimate simply to improve coverage.

### Required Work

- Build an UNKNOWN baseline by agent, field, symbol, sector, provider, reason and decision impact.
- Require every agent to justify each requested field as `required`, `conditional`,
  `optional` or `diagnostic_only`.
- Introduce reason codes such as `not_applicable`, `not_disclosed`, `provider_no_coverage`,
  `temporarily_unavailable`, `stale`, `symbol_mapping_error`, `parse_or_schema_error`,
  `conflicting_sources`, `contract_mismatch` and `unnecessary_requirement`.
- Research field-level provider routing, approved fallbacks, retries, caching, identity
  mapping, conditional schemas and better freshness rules.
- Rank proposed upgrades by expected coverage improvement, correctness, cost, licensing,
  security and maintenance burden.

### Deliverables And Gate

- Full specification: `docs/tasks/UNKNOWN_REDUCTION_RESEARCH.md`.
- Produce a baseline/Pareto report, agent-field matrix, provider capability matrix,
  versioned reason taxonomy and ranked upgrade backlog.
- Do not relax Miltos/Koulis/Thymios fail-closed behavior or change historical outputs.
- Do not implement provider or prompt changes until the diagnosis and regression fixtures
  show which UNKNOWN states are avoidable.

## Deferred Design Note - Position Holding Horizon

- Keep the current paper safety contract as `same_day_flat` until a separate overnight-risk review is complete.
- Later separate the **playbook thesis horizon** from the **execution holding horizon**. A setup may describe `1-5 sessions` or `3-10 sessions` while the current experiment still forces same-day closure.
- Design explicit horizon policies such as `intraday`, `overnight_1_5_sessions`, and `swing_3_10_sessions` instead of one global rule.
- Before enabling overnight paper positions, add earnings/calendar risk, gap-risk limits, borrow and carry costs for shorts, overnight news monitoring, maximum portfolio exposure, next-session reconciliation, stale-thesis invalidation, and emergency flatten behavior.
- Evaluate each horizon on a separate trade-performance scoreboard so intraday exits are not compared directly with multi-session swing outcomes.
- Do not change the live/paper execution horizon merely to improve reported win rate; require a frozen forward-paper evaluation and explicit acceptance gate.

## Platform Objective

- Turn the current ecosystem into a reliable, auditable, open-source paper-trading platform.
- Prepare the repository for a public GitHub release and optional donations without implying guaranteed returns or financial advice.
- Keep live-capital trading outside the first public release. Any future live mode requires a separate safety, compliance, broker, and operational review.

## Task 1 - Review And Upgrade Every Agent Individually

### Priority

- Priority 0. Complete this task before redesigning communication between agents.

### Goal

- Review every agent one by one to understand what it is, how it actually works in code, what decisions it owns, where it fails, and what changes or tools would materially improve its results.
- Separate documented agent identities from real implementations. Mark every agent as `implemented`, `partially implemented`, or `conceptual`.

### Review Order

1. `babis` - market discovery and catalyst scanning.
2. `giannis` - fundamental quality, balance-sheet, and structural-risk filter.
3. `yankos` - support-bounce and controlled-breakout trader.
4. `periklis` - early-accumulation and pre-run trader.
5. `petros` - final synthesis, memory, feedback, and model-assisted review.
6. `sokratis` - strategy researcher, teacher, and playbook-governance layer.
7. `miltos` - intraday entry-confirmation stalker.
8. `koulis` - position ownership and stewardship layer.
9. `thymios` - broker execution layer.

### Mandatory Review Template For Each Agent

- Identity and team.
- Implementation status: real module, embedded responsibility, LLM prompt, deterministic rules, or conceptual role.
- Purpose and explicit decision authority.
- Decisions the agent is forbidden to make.
- Code entry points and configuration files.
- Input artifacts, data providers, timestamps, and freshness requirements.
- Output artifacts and downstream consumers.
- Current scoring, rules, prompts, models, and fallback behavior.
- Evidence provenance and handling of missing or conflicting data.
- Known strengths and useful specialization.
- Failure modes, hallucination risks, stale-data risks, duplication, and hidden assumptions.
- Runtime, cost, rate-limit, and reliability profile.
- Existing safeguards and missing safeguards.
- Unit, integration, regression, and outcome tests required.
- Proposed improvements ranked as `must fix`, `should improve`, and `experiment`.
- Tools or libraries that may improve the agent, with license, cost, data rights, security, and maintenance assessment.
- Before/after benchmark and acceptance criteria.

### Evaluation Method

- Build a frozen, timestamped evaluation dataset containing normal cases, missing-data cases, stale filings, warrants/preferreds, delisted symbols, conflicting providers, illiquid names, false catalysts, and broker failures.
- Record the current baseline before changing an agent.
- Evaluate discovery precision, false-positive rate, false-negative examples, data coverage, source freshness, latency, failure rate, decision stability, and outcome quality where valid point-in-time labels exist.
- Keep research quality, trading quality, and execution safety as separate scores.
- Do not promote a change because its narrative sounds better; require measurable evidence from regression tests, point-in-time backtests, or forward paper results.

### Tooling Research

- Data and filings: evaluate direct SEC Companyfacts/XBRL ingestion, official company/IR sources, Alpaca data, Polygon, OpenBB, and paid providers only where licensing and cost are acceptable.
- Broker layer: evaluate the official `alpaca-py` SDK, broker clock/calendar endpoints, bracket/OCO orders, reconciliation, and idempotent client-order handling.
- Market calendar: evaluate `exchange_calendars` or `pandas_market_calendars` instead of fixed Athens hours.
- Validation and contracts: evaluate Pydantic, Pandera, and JSON Schema for versioned agent inputs and outputs.
- Testing: evaluate pytest, Hypothesis, and mocked HTTP/broker fixtures.
- Backtesting: compare vectorbt, Backtrader, LEAN, or a small custom event engine for point-in-time data, realistic fills, slippage, liquidity, and walk-forward tests. Check licenses before adoption.
- Data and runtime performance: evaluate Polars, DuckDB, persistent HTTP caching, and batched provider requests where benchmarks justify them.
- Orchestration and observability: compare a simpler internal orchestrator with Prefect or Dagster; add structured logs, run IDs, health checks, metrics, and alerts without adding unnecessary infrastructure.
- Agent evaluation: create local golden-set and adversarial evaluations before considering any general-purpose agent-evaluation framework.
- Do not install or adopt a tool solely because it is popular. Record the problem solved, expected benefit, license, operating cost, data policy, maintenance risk, and exit plan.

### Deliverables

- One review document per agent under `docs/agent_reviews/`.
- One agent inventory showing implementation status and code ownership.
- One scorecard comparing current and proposed performance.
- One tooling matrix with `adopt`, `pilot`, `defer`, or `reject` decisions.
- A prioritized implementation backlog with dependencies and safety gates.
- Regression fixtures and acceptance tests for every approved change.

### Completion Gate

- Every documented agent has a verified implementation owner and enforceable authority boundary.
- Every agent has versioned inputs/outputs, freshness rules, error behavior, tests, and measurable performance criteria.
- Conceptual agents such as `koulis` and `thymios` are either implemented as enforceable components or renamed/documented honestly.
- No agent change is allowed to reach broker submission before passing paper-only safety tests.

## Task 2 - Review And Upgrade Multi-Agent Communication

### Priority

- Priority 1. Start after Task 1 establishes clear individual roles, contracts, and baseline behavior.

### Goal

- Examine how agents exchange information, disagree, hand off decisions, recover from errors, and combine their work into the final result.
- Improve communication without merging independent trader logic or creating one opaque shared agent brain.

### Work Items

- Draw the real end-to-end dependency graph from data collection to reconciliation, including every file, database write, prompt, process, and broker call.
- Replace undocumented `latest`-file coupling with immutable run directories and a manifest containing `run_id`, `as_of`, config hash, code version, schema version, provider timestamps, and completion status.
- Define a versioned message contract for every handoff with evidence IDs, source timestamps, confidence, missing fields, warnings, vetoes, and requested next action.
- Define who may propose, approve, block, execute, reconcile, and close each action.
- Preserve separate Yankos and Periklis boards while allowing structured comparison and disagreement tracking.
- Create an explicit dissent protocol: agents must state why they disagree, which evidence would resolve the disagreement, and whether the disagreement blocks execution.
- Prevent duplicate or contradictory actions across agents, especially the same ticker, opposing directions, stale runs, repeated orders, and overlapping portfolio exposure.
- Add idempotency, retries, timeouts, circuit breakers, dead-letter handling, and recovery rules for failed handoffs.
- Add data-freshness and completeness gates before each downstream agent runs.
- Make Petros synthesis evidence-based and auditable; it must not silently rewrite another agent's decision.
- Make Koulis the enforceable owner of position identity and cross-agent exposure checks.
- Make Thymios accept only validated, approved, idempotent paper-order intents and return broker acknowledgements and reconciliation evidence.
- Separate research warnings, trade proposals, risk vetoes, broker commands, and broker results into different message types.
- Add an end-to-end replay mode that can reproduce a historical run without contacting the broker.
- Add failure-injection tests for provider outages, malformed artifacts, stale data, partial orders, monitor crashes, duplicate messages, and clock/calendar errors.

### Collaboration Metrics

- End-to-end completion and failure rate.
- Stale or malformed handoffs blocked.
- Duplicate/conflicting order intents prevented.
- Evidence coverage and source freshness at final decision time.
- Agent disagreement rate and later outcome resolution.
- Time spent in each stage and critical-path latency.
- Research shortlist precision, trader conversion rate, fill quality, slippage, drawdown, and risk-adjusted paper outcomes.
- Reconciliation accuracy between internal artifacts and Alpaca paper state.

### Deliverables

- A current-state and target-state architecture diagram.
- A RACI/authority matrix for all agent decisions.
- Versioned handoff schemas and examples.
- End-to-end sequence diagrams for research-only, paper-entry, monitoring, exit, failure, and recovery flows.
- Integration and replay tests covering the complete pipeline.
- A communication-performance report comparing the old and new architecture.

### Completion Gate

- A run can be replayed and audited from source data through final broker reconciliation.
- No broker action can occur from stale, incomplete, ambiguous, duplicated, or unauthorized agent output.
- Agent disagreements and risk vetoes remain visible in the final report.
- The platform can recover safely from a process or provider failure without silently continuing from mixed-run artifacts.

## Public GitHub And Donations Release Gate

- Release the first public version as research and paper trading only.
- Fix CI and require automated tests, linting, schema validation, dependency scanning, and secret scanning on every change.
- Remove secrets, personal paths, account identifiers, private outputs, local databases, logs, and broker snapshots from Git history and release artifacts.
- Add an explicit open-source license after reviewing dependency and data-provider licenses.
- Add `README`, architecture overview, setup guide, example configuration, safe demo data, contribution guide, code of conduct, security policy, changelog, roadmap, and support boundaries.
- Add prominent financial-risk, no-guaranteed-results, data-license, and paper-trading disclaimers.
- Publish reproducible demonstrations using mocked or clearly dated data; do not publish performance claims that cannot be independently reproduced.
- Add GitHub Sponsors or another donation method through `.github/FUNDING.yml` and keep `DONATIONS.md` transparent about what donations support.
- Do not tie donations to signals, returns, privileged research, or access to live broker actions.
- Tag a public release only after Task 1, Task 2, security review, licensing review, clean-install test, and paper-trading acceptance test are complete.

## Tomorrow Focus - 2026-06-14

### Priority 0
- Review the controlled trading ecosystem scan from 2026-06-13.

### Goal
- Re-enter the market-scanning workflow calmly and understand what each agent produced.
- Decide which names deserve deeper manual research before any paper-trading or execution discussion.

### Review Inputs
- `output/pipeline_report_20260613_133923.md`
- `output/babis_shortlist_latest.json`
- `output/giannis_rule_based_latest.md`
- `output/yankos_strategy_latest.md`
- `output/periklis_strategy_latest.md`
- `output/petros_review_latest.md`

### Main Review Notes
- Babis found 20 shortlist names from a 1200-symbol controlled scan.
- Giannis filtered them into quality/risk tiers.
- Yankos and Periklis reviewed the technical/swing setups.
- Petros gave the final triage: 3 candidates, 6 watch, 11 reject.

### Candidates To Review First
- `ANNA`: Petros 10/10, Giannis Core Watchlist, Yankos candidate long.
- `ASM`: Petros 10/10, Giannis Core Watchlist, Yankos candidate long.
- `ABEV`: Petros 9/10, Giannis Core Watchlist, but needs caution because timing is closer to highs.

### Secondary Watchlist
- `ABEO`
- `CAAS`
- `AVAH`

### Avoid / Reject For Now
- `BMHL`
- `BGY`
- `ARBK`
- `AISPW`
- `ANNAW`
- `AFCG`
- `ALTO`
- `AMBO`
- `ATHE`
- `BFLY`
- `BBBY`

### Checklist
- Confirm ticker identity and current source data before trusting any result.
- Review why Babis ranked `BMHL` highly but the later agents rejected it.
- Open the Petros report first, then compare against Giannis, Yankos, and Periklis.
- Decide whether to run deep reviews on `ANNA`, `ASM`, and `ABEV`.
- Keep this as research and review only; no live trade decision from this task.

## Tomorrow Focus - 2026-09-17

### Priority 0 — Post-Babis ecosystem review

- Confirm that the canonical Babis pipeline finished successfully.
- Open the generated `output/ecosystem_review/latest.json` pointer and read the linked `ECOSYSTEM_REVIEW.md` first.
- Review symbol coverage, source-request totals, provider limits, assessment counts and every remaining evidence-gap category.
- Separate genuine company-data gaps from provider cooldowns, daily-budget exhaustion, identity problems and insufficient source detail.
- Confirm that the one-shot post-Babis handoff registered all 11 sector agents in the production registry.
- Review how many symbols entered each sector queue and how many were blocked because classification did not reach a subcategory.

### Sector-agent architecture review

- Verify the 11 sector agents and all 28 subcategory frameworks against taxonomy `2.1.0`.
- Review the common `Sector Context Packet` and confirm that business model, value chain, peers, operating drivers, sector KPIs, risks and catalysts have clear ownership.
- Confirm that sector agents cannot change Babis classifications, perform technical analysis, issue trade instructions or invent missing evidence.
- Decide the source adapters required for sector research, using cache-first and incremental updates rather than repeated full-universe runs.
- Define acceptance gates before any mass sector-analysis backfill begins.

### Giannis financial-analysis handoff

- Review `research_information_taxonomy.json` and confirm the division between Babis, sector agents, Giannis and the shared evidence registry.
- Review the `Financial Analysis Packet` contract: normalized periods, units, currency, growth, margins, earnings quality, cash flow, balance sheet, dilution, capital allocation and financial fragility.
- Plan the Giannis runtime upgrade so it consumes same-run Sector Context Packets and normalized financial statements without rewriting upstream evidence.
- Keep missing financial values explicit and block decision-grade output when sources, periods or comparability are insufficient.

### Dashboard roadmap

- Preserve the current dashboard design as the permanent visual foundation.
- Plan the **Πλήρης ανάλυση** button and canonical page for every symbol.
- The symbol page must combine Babis classification, Sector Context Packet, Giannis Financial Analysis Packet, sources, timestamps, confidence, evidence gaps and run history.
- Show unavailable analysis as `analysis_not_available`; never present missing values as zero or as negative findings.

### Upgrade decisions

- Identify bottlenecks, duplicated work and opportunities to reduce API requests and token usage.
- Confirm that future work is event-driven or incremental and does not create unnecessary polling loops.
- Decide which improvements can enter production immediately and which must remain documented backlog items.
- Do not start the next mass run until the post-Babis report and sector queues have been reviewed.
