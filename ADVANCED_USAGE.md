# Advanced usage and legacy workflows

This guide preserves the detailed commands and background for the repository. It covers both current and older workflows; check [ecosystem status](docs/ECOSYSTEM_STATUS.md) before treating an optional path as fully validated. The short introduction is in the [README](README.md).

## Current architecture

This repository contains a local, research-first US-equity ecosystem. Discovery and classification feed sector context and financial analysis; a frozen fundamental packet then feeds independent trader agents. Portfolio and intent gates sit between trader plans and the separate, opt-in paper broker service. Evidence, missing-data reasons, source hashes, run IDs and lineage are recorded across these handoffs. See [the current ecosystem status](docs/ECOSYSTEM_STATUS.md) for what has been implemented and what still needs full-run validation.

The four-level internal sector taxonomy and local issuer registry are implemented in [research_registry](research_registry/README.md). Use `python -m research_registry summary` or `python market_intel_db.py registry summary` to inspect the dedicated research database. The symbol classifier includes a versioned `research_classification` path; unresolved information remains visible for review. The older daily workflow remains available alongside the newer research control plane.

Rule-driven workflow for US equities:

- Dynamic universe loading from SEC (NYSE/Nasdaq).
- Fundamental-first discovery score with explicit missing-data coverage; technical indicators are evaluated later by the trader agents.
- Fast deterministic agent panels driven by cited market evidence and explicit rules.
- Separated research/trader pipeline (`babis -> giannis -> petros -> frozen fundamental packet -> yankos/periklis`).

## Disclaimer

This project is a research, education, and paper-trading toolkit. It is not financial advice, does not promise returns, and should not be used to manage third-party money or third-party broker accounts. Users are responsible for their own decisions, credentials, data subscriptions, position sizing, take-profit/stop-loss settings, holding-period rules, and broker configuration.

## User Risk Settings

Before running any paper-trading workflow, each user must set risk parameters that fit their own account and testing goals.

The repository defaults are paper-testing examples only. They are not recommendations.

- Set your own position size / notional per trade.
- Set your own take-profit percentage.
- Set your own stop-loss percentage.
- Set your own holding period / maximum time-in-trade.
- Confirm your broker rules, shortability constraints, liquidity assumptions, and data subscriptions.

Trader agents can suggest setups and explain their reasoning, but risk parameters must be reviewed and configured by the user.

## Documentation

- [Information ledger, information evaluation, and trade evaluation](docs/SYMBOL_CLASSIFICATION_AND_EVALUATION.md)
- [Information evaluation contract](docs/INFORMATION_EVALUATION_CONTRACT.md)
- [Canonical ecosystem theory contract](ecosystem_theory_contract.json)
- [Agent theoretical assimilation reports](docs/agent_reports/)

- [Pipeline Overview](PIPELINE_OVERVIEW.md): agent roles, daily flow, paper-trading flow, trading window, ownership, and safety rules.
- [Setup Guide](SETUP.md): local installation, environment variables, and first-run commands.
- [GitHub Readiness](docs/GITHUB_READINESS.md): what is safe to commit and what must stay local.
- [Studio Agentic Review](docs/archive/reviews/STUDIO_AGENTIC_REVIEW.md): historical review of optional Studio features.
- [Strategy Library](strategy_library/README.md): approved playbooks, mathematical source policy, research intake rules, and trader math curriculum.
- [Donations](DONATIONS.md): optional support information for future public releases.
- [Catalyst Taxonomy](docs/CATALYST_TAXONOMY.md): Long/Short lenses, category/subcategory hierarchy, scoring, and evidence contract.
- [Problem & Risk Standard](docs/readiness/REPORTING_STANDARD.md): evidence, severity, risk, and acceptance-test requirements.
- [Readiness Baseline](docs/readiness/00_BASELINE_REPORT.md): tracked release blockers and implementation status.

## 1) Install

```powershell
python -m pip install -r requirements.txt
```

If `python` is not available on PATH, use the local launcher:

```powershell
.\run_py.cmd --version
.\run_py.cmd -m pip install -r requirements.txt
```

## 2) Configure

Edit:

- `scanner_config.json`: rules, weights, universe size, and disabled legacy-LLM settings.
- `agent_prompt.txt`: Agent-1 prompt (`Γιαννης` - fundamental desk).
- `agent_prompt_agent2_catalyst_hunter.txt`: Agent-2 prompt (`μπαμπης` - company discovery across all prices).
- `.env`: API keys and provider credentials.

Agent naming (for workflow clarity):

- Agent-1: `Γιαννης`
- Agent-2: `μπαμπης`

Current pipeline roles:

- `babis`: discovery and catalyst/opportunity generation.
- `giannis`: quality, balance-sheet, and risk filter.
- `petros`: final fundamental-only synthesis, security-readiness gate, memory, and explicit feedback.
- `yankos`: technical-analysis trader focused on swing timing after receiving the frozen fundamental packet.
- `periklis`: technical-analysis trader focused on early accumulation and cleaner swing entries after receiving the frozen fundamental packet.
- `sokratis`: Central Strategy Specialist / Skill Creator / Teacher. Researches or receives strategy ideas, maps them to mathematics, and converts them into advisory training skills for the traders.
- `miltos`: Entry Stalker. Rechecks waiting trader setups during the session and activates paper orders only after `15Min` confirmation.
- `koulis`: Portfolio Risk Gate. Reconciles both traders, blocks opposing/duplicate ideas and existing exposure, and applies total order/notional/short limits.
- `thymios`: Broker Intent Validator. Accepts only same-session, Koulis-approved intents with deterministic identity before any paper submission.

The daily pipeline begins with an offline theoretical-assimilation gate. All nine
agent reports must match the current ecosystem contract and
`agent_methodology_registry.json` plus `agent_personality_registry.json`, including
methodology/personality hashes, explicit data-processing sequence, forbidden inferences
and authority boundaries. The two LLM agents keep their base prompts and receive separate
versioned methodology and professional-personality appendices at runtime. Rule-based
agents record both identities in their artifacts, while their personality is explicitly
forbidden from changing deterministic decisions. This check does not run market research
or authorize paper submission.

The next offline gate is `agent_personality_behavior.py`. It runs 45 frozen rehearsals:
uncertainty, constructive disagreement, observable-error disclosure, downstream handoff
and pressure to exceed role authority for each of the nine agents. The gate fails closed
on personality-driven decision drift, authority expansion, silent repair or an invalid
handoff. It starts no scan, trading pipeline or broker execution.

Missing data uses `unknown_data_contract.json` and `unknown_data.py`. Every contracted
`UNKNOWN` is emitted as a reason-coded event with provider route, retryability, applicability,
coverage impact, decision impact and owner. `not_applicable` and `diagnostic_only` fields do
not reduce coverage or add score penalties. Babis, Giannis and Petros preserve the event
trace through the information ledger and frozen fundamental packet. Missing SEC ticker/CIK
identity fails visibly, while unclassified source-backed catalyst filings enter a non-executing
content-review queue instead of receiving an invented category or direction.

The daily data path is fundamental-first. `babis`, `giannis`, and `petros` finish their
work before the trader agents start. Their same-run output is frozen in
`output/fundamental_research_packet_latest.json`. Yankos and Periklis validate that
packet and its hash, keep the thesis unchanged, and add only their own technical setup,
entry confirmation, invalidators, exit logic, and sizing plan.

Trader knowledge policy:

- `sokratis` is a shared source of strategy knowledge, not the decision-maker.
- `yankos` and `periklis` keep separate symbol boards and separate execution logic.
- Each trader applies Sokratis lessons only through his own playbooks, rules, and trade justification.
- Cross-trader overlap is reviewed for learning, not for merging boards or copying trade decisions.

Team architecture principle:

- The pipeline is built as cooperating agent teams, not one monolithic agent.
- Each agent has its own job, logic, artifacts, and decision boundaries.
- The professional structure comes from separation plus collaboration: agents can share information, but they keep ownership of their own reasoning.

Price/selection filters live under `scan` in `scanner_config.json`:

- `max_price` (`null` by default: no price cap; set a number only for an explicitly limited run)
- `min_composite_score` (example: `70.0`)

Suggested `.env` keys:

- `ALPACA_API_KEY_ID`
- `ALPACA_API_SECRET_KEY`
- `POLYGON_API_KEY`
- `FMP_API_KEY`
- `FRED_API_KEY`
- `SEC_USER_AGENT` (format: `YourName your-email@domain.com`)

Source routing:

- `SEC` is used as primary filing source.
- `market_data.provider_priority` controls bar-data fallback order (default: `alpaca -> polygon -> yfinance`).
- Symbols that have fewer bars than `scan.min_history_days` are recorded as `history_exclusions`, not technical/provider `errors`. They carry a history-gap count and a suggested future trading-session retry horizon; immediate same-day retries are not recommended.
- The `errors` collection is reserved for actual provider, validation, transport, authentication, rate-limit, or processing failures. This keeps eligibility coverage separate from operational reliability.
- `OpenBB` is available as an opt-in local adapter by adding `openbb` or `openbb:<provider>` into `market_data.provider_priority`.
- `market_data.openbb_provider` sets the default OpenBB provider when the routing entry is just `openbb`.
- Current safe OpenBB choices for this project are `openbb:yfinance` and `openbb:fmp`.
- The scanner relies on the current process environment and `.env` keys, so we do not need to persist credentials into `~/.openbb_platform` for this workflow.
- `data_sources.openbb_research` adds supplementary ownership / insider / OTC-flow context for the fundamental agents.
- The current default OpenBB research mix is:
  - `share_statistics` via `yfinance`
  - `insider_trading` via `SEC`
  - `darkpool.otc` via `FINRA`
- These OpenBB datasets are treated as secondary evidence and never replace SEC filings.
- `FRED` macro series are fetched once per run when `FRED_API_KEY` is set.

Example OpenBB-enabled routing:

```json
"market_data": {
  "provider_priority": ["alpaca", "openbb:fmp", "openbb:yfinance", "yfinance"],
  "openbb_provider": "yfinance",
  "alpaca_feed": "iex",
  "adjustment": "raw",
  "bars_limit": 10000
}
```

Example supplementary OpenBB research block:

```json
"openbb_research": {
  "enabled": true,
  "share_statistics_provider": "yfinance",
  "insider_limit": 80,
  "insider_lookback_days": 180,
  "darkpool_weeks": 4
}
```

## 3) Run daily scan

```powershell
python us_equity_monitor.py --config scanner_config.json scan
```

Optional:

```powershell
python us_equity_monitor.py --config scanner_config.json scan --top-n 10
python us_equity_monitor.py --config scanner_config.json scan --tickers AAPL MSFT NVDA --no-llm
python us_equity_monitor.py --config scanner_config.json scan --min-composite-score 70 --no-llm
```

## 4) Run one company brief (agent mode)

```powershell
python us_equity_monitor.py --config scanner_config.json brief --ticker AAPL
```

With a focused question:

```powershell
python us_equity_monitor.py --config scanner_config.json brief --ticker AAPL --question "Ελέγξε αν το debt profile αλλάζει το investment case 1-4 τριμήνων."
```

## 5) Run collaborative pipeline (babis -> giannis)

```powershell
python us_equity_monitor.py --config scanner_config.json pipeline
```

Useful options:

```powershell
python us_equity_monitor.py --config scanner_config.json pipeline --stage1-limit 300 --stage1-llm-refine-limit 40 --stage2-limit 30 --stage2-llm-limit 10
python us_equity_monitor.py --config scanner_config.json pipeline --no-stage2-llm
python us_equity_monitor.py --config scanner_config.json pipeline --no-stage1-llm --no-stage2-llm
python us_equity_monitor.py --config scanner_config.json pipeline --respect-universe-cap
```

Default pipeline behavior:

- scans a controlled SEC universe capped by `universe.max_symbols` (currently 1,200)
- applies `pipeline.stage1_min_composite_score` (currently 70)
- deep-enriches the configured shortlist before the downstream handoff
- a deliberate full-universe run requires configuration/CLI override and is a heavier operational mode

Pipeline tuning keys in `scanner_config.json`:

- `pipeline.stage1_llm_refine_limit`
- `pipeline.stage1_llm_num_predict`
- `pipeline.stage1_llm_timeout_seconds`
- `pipeline.checkpoint_enabled`
- `pipeline.checkpoint_every`
- `pipeline.checkpoint_file`

Default setup keeps Stage-1 LLM refine disabled (`stage1_llm_refine_limit: 0`) for maximum stability.
Stage-1 checkpoint/resume is enabled by default so long runs continue from last saved progress.

Pipeline outputs:

- `output/babis_shortlist_*.json`
- `output/babis_shortlist_latest.json` (latest 300 baseline for incremental monitoring)
- `output/babis_shortlist_delta_*.json` (what changed vs previous latest run)
- `output/babis_shortlist_delta_latest.json`
- `output/babis_traps_*.json`
- `output/giannis_briefs_*.md`
- `output/pipeline_report_*.md`

## 6) Daily runner script

```powershell
.\run_daily_scan.ps1
```

## 7) Local SQLite memory

The scanner and collaborative pipeline automatically write new scan/shortlist rows into SQLite when `database.enabled` is true in `scanner_config.json`.

Backfill or inspect the local SQLite database from existing scanner outputs:

```powershell
python market_intel_db.py init
python market_intel_db.py ingest
python market_intel_db.py summary
python market_intel_db.py top --limit 20
```

Default database path:

- `data/market_intel.db`

This keeps the existing `output/*.json`, `*.csv`, and `*.md` files intact while giving the project one queryable local store for scan rows, shortlist rows, and FRED macro observations.

Technical-analysis fields stored for new runs include:

- `rsi_14`
- `macd_histogram`
- `atr_14_pct`
- `volume_ratio_20`
- `return_20d_pct`
- `volatility_20d_pct`

Watchlist refresh (re-check latest 300 only, produce delta vs previous baseline):

```powershell
.\run_watchlist_refresh.ps1
```

Detached non-LLM dual-watchlist run:

```powershell
.\start_dual_watchlist_background.ps1
.\check_dual_watchlist_status.ps1
.\stop_dual_watchlist_background.ps1
```

Fresh full SEC-exchange non-LLM run with automatic outcome tracking and meeting report (`-FullMarket` bypasses the daily symbol cap):

```powershell
.\start_dual_watchlist_background.ps1 -FullMarket
```

When the run completes, the workflow writes `output/RUN_FINISHED_READ_ME.txt` and attempts to play a short sound plus a Windows popup notification.

The daily runner is research-only unless `-SubmitPaper` is supplied. It assigns one `run_id` and captures checksummed artifacts plus config hashes in `output/runs/<run_id>/manifest.json`.

Structured research information is indexed in the canonical information registry. To inspect
its coverage or run an idempotent migration of existing control-plane artifacts:

```powershell
.\run_py.cmd information_registry.py backfill
.\run_py.cmd information_registry.py summary
.\run_py.cmd information_registry.py validate
.\run_py.cmd information_registry.py dossier issuer:CIK:0000320193
```

The registry uses stable information URNs, versioned taxonomy terms, source locators when
available, typed evidence relationships and metadata-first search. New structured claims
require a governed category. It also exposes reviewable claim conflicts and downstream
impact when packet freshness changes. It is local and research-only.

Optional local information extensions are exposed by `information_extensions.py`. A complete
licensed or local IR/news HTML or text body can be imported with `ingest-text`; a headline or
URL alone is never treated as a full article. `index-vectors --subject <canonical-id>` indexes
only supported claims and elements from complete, current documents. The built-in character
ngram vectors need no model download and provide lexical similarity; callers can inject a local
embedding function for semantic ranking. `communities` summarizes explicit peer/member edges,
`review-view --out <path>` writes a read-only review inbox, and `export-lineage --out <path>`
writes OpenLineage-style JSONL. See `PIPELINE_OVERVIEW.md` for commands and limits.

At completion it also writes an immutable, credential-free coordination event to `output/integration_outbox/<run_id>_daily_research_digest.json`. The event summarizes coverage, candidate counts, warnings and the expected Babis → Giannis → Yankos/Periklis → Petros → Koulis → Thymios → Miltos handoffs. Slack and Notion credentials are intentionally not stored in this repository; an authorized Codex automation publishes unsent outbox events and records an acknowledgement only after both projections succeed.

### Slack group research rooms

`slack_agent_gateway.py` provides a research-only Socket Mode gateway for five private rooms:

- `team-research`: Babis, Giannis, Petros
- `team-traders`: Yankos, Periklis, Petros
- `team-strategy`: Sokratis, Yankos, Periklis
- `team-risk`: Koulis, Thymios, Miltos (strictly read-only)
- `team-ecosystem`: all nine agents

The user may introduce any uppercase ticker, preferably with a `$` prefix such as `$COST`. When a `$` ticker exists, other uppercase prose tokens are ignored. The gateway builds a current cited packet, runs an independent opening round, a cross-examination round, and a final team synthesis. Each conversation is written to `output/integration_outbox/research_conversations/` for idempotent projection into the Notion `Research Conversations` database.

The live source order is configured-API first: SEC filings, Alpaca price history,
Polygon price cross-check, FMP quote/profile fundamentals, and Benzinga news. Yahoo
Finance is queried only for the missing price or news component after the configured
sources fail. Provider status, timestamps, fallback use, and price disagreement remain
visible in the evidence packet. Panel text is deterministic and requires no Ollama model.

The Slack app is defined in `slack_app_manifest.yaml`. After installing it in the workspace, keep its tokens only in `.env`:

```dotenv
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
```

Validate configuration, then start and stop one research-scoped session:

```powershell
.\start_slack_agent_gateway.ps1 -Check
.\start_research_session.ps1
.\research_session_status.ps1
.\stop_research_session.ps1
```

`research_session_service.py` owns the gateway and the health loop for exactly one
research session. It runs through `pythonw.exe`, so periodic health checks do not create
PowerShell windows. It also restarts a crashed gateway up to the configured limit and
terminates its child process when the session stops or is cancelled.

```powershell
.\start_research_session.ps1
# conduct Slack research
.\stop_research_session.ps1
```

The legacy Windows tasks `MarketResearchSlackGateway` and `MarketResearchSlackHealth`
remain disabled. Health checks for Slack authentication, stale events, and overdue
Notion acknowledgements run every 15 minutes only inside an active research session.
They post to `team-ecosystem` only when health changes to unhealthy or recovers.

Slack and Notion remain communication and memory surfaces only. The gateway has no broker imports, and requests containing execution language are downgraded to discussion with an explicit safety notice.

Direct chat with each agent:

```powershell
.\talk_to_agent.ps1 -Agent giannis -Message "Ανάλυσε την CGEN με βάση το framework σου."
.\talk_to_agent.ps1 -Agent babis -Message "Δώσε catalyst update για τα top 5 του watchlist."
```

Direct non-LLM queries against the latest reports:

```powershell
python ask_watchlist.py summary
python ask_watchlist.py top --limit 10
python ask_watchlist.py top --tier "Core Watchlist" --limit 20
python ask_watchlist.py avoid --limit 10
python ask_watchlist.py ticker CGEN
```

Concise daily report:

```powershell
python today_action_report.py
```

Petros review and training labels:

```powershell
python petros_agent.py review
python petros_agent.py ticker ASM
python petros_agent.py feedback ASM interested "καλό setup, θέλω filing check"
python petros_agent.py feedback-list
```

Talk directly to Petros without Ollama:

```powershell
python petros_agent.py chat "ASM τι γνωμη εχεις;"
python petros_agent.py chat "ASM μου αρεσει, κρατα το για παρακολουθηση"
python petros_agent.py chat "BDTX θελω filing review πριν το δεχτω"
python petros_agent.py chat "ANNA διαφωνω, πολυ μεγαλο volatility"
.\talk_to_agent.ps1 -Agent petros -Message "ASM τι γνωμη εχεις;"
```

When Petros detects your opinion in `chat`, he stores it in `petros_feedback`.
The next `python petros_agent.py review` uses that feedback to adjust his score and verdict.

Safe Petros trainer:

```powershell
python petros_trainer.py status
python petros_trainer.py train
```

The trainer is intentionally conservative:

- It reads from the local SQLite memory only.
- It saves `models/petros_model_latest.json` as plain JSON coefficients.
- It does not save or load pickle/joblib model files.
- It cannot place trades. Its model is excluded from the default fundamental-only Petros review and is available only in the explicitly requested legacy mixed-review mode.
- Market outcomes are the default training objective; subjective feedback is excluded unless `--include-feedback` is explicitly supplied.
- If there are not enough outcome labels, it writes a report and skips training safely.

Intelligent Investor workshop (read -> individual summaries -> discussion -> what changed):

```powershell
# 1) Συμπλήρωσε πρώτα το source:
# education\intelligent_investor_source.md

# 2) Τρέξε detached (background):
.\start_intelligent_investor_workshop_background.ps1

# 3) Έλεγχος προόδου:
.\check_intelligent_investor_workshop_status.ps1
```

Self-training loop from real market outputs (babis + giannis + joint rulebook):

```powershell
# Run one cycle now
.\run_self_training_cycle.ps1

# Start detached daemon (keeps running in background with retry)
.\start_self_training_background.ps1 -IntervalMinutes 180

# If PATH/Python resolution is unstable, pin explicit interpreter path
.\start_self_training_background.ps1 -IntervalMinutes 180 -PythonPath ".\.venv-next\Scripts\python.exe"

# Check status / logs / latest cycle
.\check_self_training_status.ps1

# Stop background daemon
.\stop_self_training_background.ps1
```

Self-training outputs:

- `output/self_training/cycle_YYYYMMDD_HHMMSS/01_babis_initial_summary.md`
- `output/self_training/cycle_YYYYMMDD_HHMMSS/02_giannis_initial_summary.md`
- `output/self_training/cycle_YYYYMMDD_HHMMSS/03_babis_peer_review.md`
- `output/self_training/cycle_YYYYMMDD_HHMMSS/04_giannis_peer_review.md`
- `output/self_training/cycle_YYYYMMDD_HHMMSS/05_joint_synthesis.md`
- `output/self_training/cycle_YYYYMMDD_HHMMSS/06_what_changed.json`
- `output/self_training/cycle_YYYYMMDD_HHMMSS/06_what_changed.md`
- `output/self_training/rulebook.json`
- `output/self_training/latest_cycle.txt`
- `output/self_training/latest_manifest.json`

Outputs:

- `output/scan_*.csv`
- `output/scan_*.json`
- `output/scan_*.md`
- `output/mini_report_*.md` (short summary with "why selected")
- `output/llm_briefs_*.md` (legacy optional LLM workflow; disabled in the main config)
- `output/brief_<TICKER>_*.md` for single-company briefs

## Existing GitHub Brief Workflow

This repository also contains the earlier GitHub-side market brief workflow under `intel/`.

- `intel/run_brief.py`: lightweight brief generator.
- `intel/latest.md`: latest generated market brief.
- `intel/YYYY-MM-DD/*.md`: dated AM / pre-open notes.
- `.github/workflows/ci.yml`: Python compilation and deterministic unit tests for pushes and pull requests.

## Notes

- The scanner is fully rule-based and not tied to a fixed watchlist.
- Data completeness varies by issuer and data provider.
- This workflow is for research support, not automated order execution.
