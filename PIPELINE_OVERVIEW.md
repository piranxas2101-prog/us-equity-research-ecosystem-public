# Pipeline Overview

This document explains how the market research and paper-trading pipeline is organized.

## Purpose

The pipeline is designed as an AI-assisted research and paper-trading workflow. It scans the market, filters opportunities, asks specialized agents to evaluate setups, records decisions, and uses paper trading to test whether the logic works in practice.

It is not financial advice and does not promise returns.

## High-Level Flow

1. `babis` classifies and routes every listed symbol, collects the market and SEC data, and keeps the shortlist as an index-style membership (entry and exit rules, see `docs/SHORTLIST_MEMBERSHIP.md`); technical fields do not contribute. It does not rank: the stage-1 rules only decide membership.
2. `giannis` scores fundamental quality SEC-first (yfinance only as a labelled fallback, see `docs/SEC_FIRST_FUNDAMENTALS.md`) and reviews balance-sheet risk, evidence gaps, and structural risk; fundamental freshness is the age of the last periodic report (`docs/GIANNIS_FUNDAMENTAL_FRESHNESS.md`).
3. `petros` performs the final fundamental-only synthesis and separates company-thesis quality from security readiness.
4. The pipeline freezes an immutable, same-run `Fundamental Research Packet` with source hashes, blockers, and prove/kill criteria.
5. Only packet rows eligible for technical review reach `yankos` and `periklis`; they then perform independent technical analysis and create frozen trade plans.
6. `sokratis` researches or receives strategy ideas, maps them to math families, and converts them into advisory training skills for the traders.
7. The technical research lab evaluates frozen trader signals with point-in-time indicators, next-bar execution costs, walk-forward windows and regime scorecards before promotion review.
8. `miltos` watches trader setups that are waiting for `15Min` confirmation.
9. `koulis` reconciles cross-agent conflicts, current exposure, and portfolio-wide limits.
10. `thymios` validates the approved same-session intent contract before a separate broker service can consume it.
11. Reports, meetings, and outcome tracking create the learning loop for the next day.

## Agent Roles

| Agent | Role | Can Decide | Cannot Decide |
| --- | --- | --- | --- |
| `babis` | Classification, routing, data collection and shortlist membership | Which names are shortlist members, and each symbol's category and route | Fundamental ranking or tiers, and final trade execution |
| `giannis` | SEC-first fundamental scoring, quality and risk filter | Which names are higher/lower quality (the tiers) | Trade entries or exits |
| `petros` | Final fundamental synthesis and memory | Fundamental verdict, security readiness, and technical-review eligibility | Technical timing or broker execution |
| `yankos` | Independent technical trader focused on swing timing | Technical candidates for his own playbook after the fundamental gate | Changing the frozen fundamental thesis or other traders' positions |
| `periklis` | Independent technical trader focused on early accumulation and clean swing entries | Technical candidates for his own playbook after the fundamental gate | Changing the frozen fundamental thesis or other traders' positions |
| `sokratis` | Central Strategy Specialist / Skill Creator / Teacher | Advisory research notes, training lessons, and skill proposals | Broker actions or direct playbook promotion |
| `miltos` | Entry stalker | Whether a waiting setup now has `15Min` confirmation | New strategy selection |
| `koulis` | Portfolio Risk Gate | Block/cap intents using exposure and portfolio limits | Trading direction or broker submission |
| `thymios` | Intent Contract Validator | Accept/reject the structural validity of Koulis-approved intents | Market opinion, portfolio selection, or broker submission |

## Team Architecture

The pipeline is organized as cooperating agent teams, not as one monolithic decision maker.

Each agent has:

- a clear job,
- its own inputs,
- its own output artifacts,
- its own decision boundaries,
- and its own reasoning style.

Together, the teams create a professional decision architecture:

- Research team: `babis`, `giannis`, and `petros` discover, filter, and synthesize market opportunities.
- Trader team: `yankos` and `periklis` evaluate their own trader-owned boards and decide whether their own rules justify paper execution.
- Strategy education team: `sokratis` teaches math, strategy filters, and improvement proposals without replacing trader logic.
- Control team: `miltos`, `koulis`, and `thymios` observe confirmation, enforce portfolio limits, and validate contracts. The Alpaca paper-trader is a separate execution service.

The value comes from separation plus collaboration: agents share useful information, but they do not collapse into the same logic.

The boundary between the teams is explicit: the research team writes the immutable
`Fundamental Research Packet`; the trader team reads it but cannot rewrite it. A missing
valuation, unassessed priced-in state, incompatible `run_id`, changed source hash, or
non-fundamental Petros artifact blocks execution eligibility rather than silently
becoming a neutral input.

## Agent Review Checklist

When reviewing an agent, answer these questions:

- Which team does this agent belong to?
- What does this agent own?
- What does this agent read?
- What does this agent write?
- What is this agent's own reasoning style or strategy logic?
- What information can this agent receive from other agents?
- How does this agent use shared knowledge without losing autonomy?
- What decisions can this agent make?
- What decisions are explicitly outside this agent's authority?
- What artifacts must this agent produce so the decision is auditable?
- How does this agent collaborate with the rest of the team?
- What boundaries prevent overlap, duplicated authority, or hidden handoffs?
- What failure modes should we monitor?

Before a daily run, `agent_assimilation.py verify` checks that all nine theoretical
self-reports match both `ecosystem_theory_contract.json` and the versioned
`agent_methodology_registry.json` plus `agent_personality_registry.json`. The reports document
purpose, inputs, step-by-step data-processing logic, outputs, authority, boundaries,
handoffs, missing-data behavior, failure modes, methodology identity, public exemplars,
counterweights, forbidden inferences and professional personality. Babis and Giannis
receive their methodology and personality as separate runtime appendices to their
existing prompts. Deterministic agents record both identities in their artifacts, but
personality is prohibited from influencing their rules or decisions. This gate verifies declared
architectural alignment only; it does not evaluate market predictions or trades.

Immediately afterward, `agent_personality_behavior.py` runs a frozen 45-case practical
rehearsal: uncertainty, disagreement, error disclosure, handoff and authority pressure for
each agent. It is non-executing and fail-closed on decision drift, authority expansion,
silent repair or invalid handoff. Passing proves the deterministic adapter on these cases,
not unconstrained LLM consistency, market skill or profitability.

The daily preflight also validates `unknown_data_contract.json`. In the fundamental lane,
Babis, Giannis and Petros attach immutable `unknown_events` rather than passing only an
undifferentiated field list. Events distinguish non-applicability, non-disclosure, insufficient
source detail, provider gaps, temporary failure, stale evidence, identity errors, parser/schema
errors, source conflicts and contract mismatches. Their provenance survives the information
ledger and frozen fundamental packet. The catalyst content-review queue and SEC identity gate
are non-executing and fail closed.

## Trader Knowledge And Execution Policy

`sokratis` is a shared strategy teacher. He gives the trader team mathematics, filters, questions, and strategy lessons.

He does not create one shared trader brain.

`yankos` and `periklis` remain separate traders:

- Each trader keeps his own symbol board.
- Each trader analyzes only the symbols produced by his own playbooks.
- Each trader applies Sokratis lessons through his own rules and preferred strategies.
- Each trader must justify entries, exits, holds, and skips independently.
- Cross-trader overlap is used for learning and outcome review only; it does not merge symbol lists or transfer execution authority.

Sokratis can improve how a trader thinks, but he cannot decide the trade for that trader.

## Daily Research Pipeline

The configured SEC-universe research flow uses the `-FullMarket` flag. In this mode the runner explicitly bypasses the daily `universe.max_symbols` cap in `scanner_config.json` and scans all eligible SEC exchange symbols. The ordinary daily mode retains the configured cap:

```powershell
.\start_dual_watchlist_background.ps1 -FullMarket -MeetingDurationMinutes 0
```

This launches the market scan in the background and writes status/log files under `output/` and `logs/`.

Check status:

```powershell
.\check_dual_watchlist_status.ps1
```

The important outputs are:

- `output/babis_shortlist_latest.json`
- `output/giannis_rule_based_latest.json`
- `output/petros_review_latest.json`
- `output/fundamental_research_packet_latest.json`
- `output/yankos_strategy_latest.json`
- `output/periklis_strategy_latest.json`
- `output/sokratis_strategy_teacher_latest.md`
- `output/today_action_report.md`
- `output/team_decision_latest.md`

Generated output files are intentionally ignored by Git.

Every controlled daily run receives one `run_id`. Principal artifacts and config hashes are copied under `output/runs/<run_id>/manifest.json`; `*_latest` files are convenience pointers, not the audit record.

## Research information control plane

Reusable research results are also projected into the shared local control plane at
`data/market_intel.db`. The control plane stores immutable observations, evidence packets,
packet-to-source lineage, workflow checkpoints, explicit evidence gaps and deterministic
asset-check results. Company Profile continues to own its records in the dedicated research
registry, while its reusable packet reference is published to the shared control plane.

Inspect the four-stage pilot without running providers, LLMs or trading code:

```powershell
.\run_py.cmd research_asset_graph.py status
.\run_py.cmd research_asset_graph.py check
.\run_py.cmd research_asset_graph.py lineage <packet_id>
```

The pilot graph is:

`company_profile → financial_evidence → fundamental_analysis → fundamental_research_packet`

### Canonical information registry

The control plane also projects every new observation, packet, claim and thesis into
`information_registry.py`. This semantic layer gives each object a stable
`urn:ecosystem:*` identifier and keeps domain, owner, verification state, lifecycle,
valid/as-of time, source locator, taxonomy assignments and typed relationships separate.
The existing producer APIs remain compatible; registration happens automatically.

The governed relationship vocabulary includes `derived_from`, `contains`, `supports`,
`contradicts`, `supersedes`, `depends_on`, `affects`, `peer_of` and `member_of`. Research
information categories are copied into a versioned taxonomy table keyed by scheme, version
and term. Taxonomy changes use a proposal/review record instead of replacing the active
definition in place.

Inspect or migrate the registry without making provider, LLM or broker calls:

```powershell
.\run_py.cmd information_registry.py init
.\run_py.cmd information_registry.py backfill
.\run_py.cmd information_registry.py summary
.\run_py.cmd information_registry.py validate
.\run_py.cmd information_registry.py search revenue --domain issuer --type claim --category growth_and_margins
.\run_py.cmd information_registry.py dossier issuer:CIK:0000320193
.\run_py.cmd information_registry.py conflicts --subject issuer:CIK:0000320193
```

Document evidence is additionally decomposed into a `document → document_element` hierarchy.
Each element keeps its structural path and available page/section metadata, so claims can
reference exact evidence when a producer supplies a `document_element_urn`. Structured claims
must have a governed category; incompatible values for the same subject, predicate and period
create review candidates. Packet freshness changes are append-only state events, and `impact`
traces downstream claims and theses. `dossier` separates current verified claims from open
conflicts and claims affected by stale inputs. Workflow checkpoints emit
append-only `START` and `COMPLETE` lineage events with run, subject, code and input/output
facets. Run `information_registry.py impact <information_urn>` with a URN returned by search
or dossier to inspect a source's downstream dependencies. Reviewers can close a candidate
with `information_registry.py review-conflict <conflict_id> --decision confirmed|dismissed
--reviewer <name> --note <reason>`; this does not alter either immutable claim. The software
SEC filing worker indexes only its matched source excerpts, with URL and character offsets,
as provisional document elements rather than validated financial facts.

The optional `information_extensions.py` adds a bounded full-body adapter for local IR/news
HTML or text, local vector ranking, peer/member connected-component summaries, a static review
inbox and OpenLineage-compatible JSONL export. It does not fetch source pages. Import only a
complete body obtained through an authorized source; its URI and checksum remain linked to the
observation, document elements and packet. Example commands (from the project directory):

```powershell
python information_extensions.py ingest-text .\source_article.html --subject issuer:CIK:0000320193 --run-id ir_20260919 --provider issuer_ir --uri https://example.org/article --title "Issuer update" --type ir_document --published-at 2026-09-19
python information_extensions.py index-vectors --subject issuer:CIK:0000320193
python information_extensions.py vector-search "cash flow" --subject issuer:CIK:0000320193 --category source_provenance
python information_extensions.py communities --subject issuer:CIK:0000320193
python information_extensions.py review-view --out .\output\information_review.html
python information_extensions.py export-lineage --out .\output\lineage.jsonl
```

The default local character-ngram index measures lexical similarity, not semantic meaning.
A Python caller may pass its own installed local `embedding_fn` and `model_id` to both indexing
and searching. Candidate-only SEC snippets, provisional claims and stale or rejected sources
are excluded from vector results. Communities appear only when `peer_of` or `member_of` edges
exist. The HTML inbox is read-only; taxonomy decisions use `review-taxonomy`, and claim-conflict
decisions use the registry CLI. Lineage export maps stored events without transmitting them.

`check` persists contract, lineage, quality-state and evidence-gap checks. `status` is
read-only. The optional Dagster projection is defined in `dagster_research_assets.py`; its
packages are isolated in `requirements-extensions.txt`, so the core pipeline does not require
them.

Two on-demand evidence adapters publish into the same packet store:

```powershell
.\run_py.cmd edgartools_evidence_adapter.py --identifier AAPL --subject-id issuer:CIK:0000320193 --run-id <run_id> --as-of 2026-09-18
.\run_py.cmd document_evidence_adapter.py .\path\to\investor-presentation.pdf --subject-id issuer:CIK:0000320193 --run-id <run_id> --document-type investor_presentation
```

EdgarTools uses the existing `SEC_USER_AGENT`; Docling processes local documents and needs no
API key. Both adapters are research-only, perform zero LLM calls and have no broker authority.

## Agent coordination and external projections

The agent artifacts are the communication mechanism inside the pipeline. Each material handoff must carry the same `run_id`, resolve to its expected artifact and pass the receiving contract. At the end of the run, `ecosystem_coordination.py` validates these links and writes one append-only event under `output/integration_outbox/`.

The outbox contains operational summaries only. A separate Codex automation may project it to:

- Notion Run Log and Agent Handoff Log for durable review;
- Slack `daily-research` for one digest per run;
- Slack `ecosystem-alerts` only for warnings, contract failures, stale-data failures, risk blocks or reconciliation mismatches.

Neither Slack nor Notion is an execution authority. No API keys, broker credentials, order approvals or trade commands are allowed in the integration payload.

### Interactive group research

The real-time Slack path is separate from the scheduled daily digest. `research_session_service.py` owns a windowless Socket Mode gateway and its health loop only for the duration of an explicit research session; the legacy logon and 15-minute Windows tasks remain disabled. The gateway maps the five private team channels to fixed agent rosters, accepts user-supplied tickers, builds a fresh API-first evidence packet, and runs deterministic opening, challenge, and synthesis rounds in the originating Slack thread. Source priority is SEC/Alpaca/Polygon/FMP/Benzinga, with Yahoo Finance used only for a missing price or news component. The resulting immutable `ecosystem.integration_outbox.v2` conversation event is projected to the Notion `Research Conversations` data source. This path requires no Ollama model and never calls the paper-trading or broker modules; Koulis, Thymios, and Miltos are read-only conversational reviewers.

## Paper Trading Flow

The daily pipeline is research-only by default. Paper submission is enabled only with an explicit `-SubmitPaper` flag. When enabled, the broker clock and configured entry window still determine whether entries are allowed.

Manual start is still available for recovery or testing:

```powershell
.\start_trader_paper_trading_today.ps1
```

The trader wrapper does the following:

1. Runs Alpaca preflight in blocking mode; existing positions/open orders stop a new autonomous batch unless an explicit flatten workflow is requested.
2. `koulis` blocks trader conflicts, duplicate symbols and existing exposure, then applies one portfolio-wide order/notional/short budget.
3. `thymios` validates the same-session approved-intent contract before broker-facing submission.
4. Each trader can select only rows present in the approved-intent file and still needs entry confirmation.
5. Monitors start only after orders; `miltos` can activate a waiting setup only with the same approved intent.
6. End-of-day reconciliation compares trader-owned reports with Alpaca positions/open orders.

Research only (default):

```powershell
.\run_daily_dual_watchlist.ps1
```

Explicit paper-submission request:

```powershell
.\run_daily_dual_watchlist.ps1 -SubmitPaper
```

`-NoAutoTraderStart` remains only as a backward-compatible emergency disable flag.
The default `miltos` polling plan is every 5 minutes for up to 84 cycles, so it covers the whole `16:30-23:00` Athens market session instead of stopping after the first hour.
After `22:00`, `miltos` continues watching and reporting, but the trading-window rule blocks new entries.

## Entry Confirmation

New entries require `15Min` candle confirmation:

- Long setup: latest `15Min` close must break above the previous candle high with a bullish candle.
- Short setup: latest `15Min` close must break below the previous candle low with a bearish candle.

Setups that do not pass confirmation stay in `waiting_confirmation`.

## Trading Window

The trading window is configured in `trading_window_config.json`.

Current rules:

- Market workflow window: `16:30-23:00` Athens time.
- New entries allowed: `16:30-22:00` Athens time.
- Position management/exits allowed: `16:30-23:00` Athens time.
- Same-day flatten safety cutoff: `15:55` US/Eastern.

After `22:00` Athens time, `miltos` should not open new positions.

## Position Ownership

The system should evaluate only positions owned by the trader workflow.

Ownership is tracked through:

- Trader paper-trade reports.
- Alpaca `client_order_id`.
- Run timestamps.
- Position review history.
- End-of-day reconciliation reports.

Manual or old Alpaca paper positions should be treated as external context unless the user assigns them to a trader.

## Position Sizing, Risk Parameters, And Holding Period

Paper-trade notional size, take-profit, stop-loss, and holding-period settings are configurable risk parameters, not universal recommendations.
Each user must adjust them for their own account size, risk tolerance, broker rules, liquidity constraints, and testing goals.

For this project, the trader agents can propose a setup and explain why it fits their playbook, but the pipeline documentation must make clear that:

- Position size is a user-controlled setting.
- Take-profit and stop-loss percentages are user-controlled settings.
- Holding period / maximum time-in-trade is a user-controlled setting.
- Risk/reward defaults are starting assumptions for paper testing only.
- No default setting should be treated as financial advice or as suitable for every user.
- Future trader reports should include the notional size, take-profit, stop-loss, holding-period rule, and risk/reward assumptions used for that run.

## Safety Rules

- Paper trading only by default.
- No live deployment without explicit review.
- No order without fresh daily output.
- No fixed position size, take-profit, stop-loss, or holding period should be treated as universally correct.
- No short order unless Alpaca marks the asset as shortable.
- No duplicate order for the same trader/symbol/day.
- No new entries after `22:00` Athens time.
- `thymios` validates only the intent contract. A separate broker service performs paper submission when explicitly enabled.

## Learning Loop

At the end of each trading day:

- Trader reports explain entries, holds, exits, and waiting setups.
- Team meetings compare analyst and trader logic without merging trader-owned boards.
- Outcome tracking updates later when enough market data exists.
- Mistakes become training examples for the next run.

The goal is not to make the system look right. The goal is to learn where the logic is strong, where timing is weak, and how each agent should improve.
# Company deep-research stage

After Petros completes the fundamental synthesis, `company_deep_research.py`
reviews only `candidate` and `manual_review` companies. It records reverse-DCF
expectations, growth-driver gaps, margin durability, company KPIs, Form 4
classification, measurable thesis criteria, and valuation zones. Missing
primary evidence remains explicit and is never inferred. The resulting artifact
is frozen into the Fundamental Research Packet before downstream analysis.
