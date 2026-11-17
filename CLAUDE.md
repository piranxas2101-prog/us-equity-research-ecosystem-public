# CLAUDE.md

US Equity Research Ecosystem — local, evidence-first research for US-listed symbols with a multi-agent pipeline and an explicitly separate, paper-only execution path. Owner: the project owner (Greek-speaking; reply in Greek).

## Working model

- Everything runs on the owner's Windows machine. Claude helps design, edit, test and review; the owner runs the long pipelines and all trading workflows.
- Commit and push finished, tested changes to `main` automatically (owner's standing instruction, 2026-10-01); skip when the owner says not to. Never force-push.
- Public mirror (owner's decision 2026-10-22): after every push to the private `main`, run `.\.venv-next\Scripts\python.exe publish_public.py --push`. It publishes one clean snapshot commit to `us-equity-research-ecosystem-public` (owner's name and private links rewritten, no private history, blocked on personal markers or gitleaks findings). Never change repository visibility.
- Do not start full-research runs, background daemons, Slack gateways or any `start_*` / `run_*` / `resume_*` launcher without asking — they run for hours and hit rate-limited providers.
- Never run or modify behavior of anything that talks to Alpaca (`alpaca_*.py`, `start_trader_*`, `thymios_order_intent.py`, `koulis_risk_gate.py`) without explicit approval. Paper trading is started by the owner: manually, or by the owner-registered Mon-Fri task "Ecosystem Preopen Check" running `start_trader_afternoon_session.ps1` (owner's decision 2026-10-13; paper account, launcher's default limits).
- Scheduled tasks: "Ecosystem Nightly Research" (01:30) and "Ecosystem Preopen Check" (Mon-Fri 15:00 Athens, the script waits for 09:00 New York so the clock changes of Europe and the US never move it past the open; `start_trader_afternoon_session.ps1`: pre-open check, paper orders at 09:35 New York, session report after the close) both wake the computer; the computer is kept awake only while there is work: after the nightly run, after the afternoon session's report, when the afternoon has nothing to trade, or after an error (always notified), `hibernate_if_idle.ps1` hibernates it if idle 15 min on mains power (switches `power.hibernate_after_nightly` / `power.hibernate_after_afternoon` in scanner_config.json). PowerShell launchers call native programs only through `Invoke-Logged` (stderr must not end a Windows PowerShell 5.1 script; `tests/test_powershell_native_calls.py`).
- Never read or print `.env`. Credentials come from `.env` (template: `.env.example`).

## Commands

Use the locked runtime `.venv-next` (Python 3.12):

```powershell
.\.venv-next\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"   # full test suite
.\.venv-next\Scripts\python.exe -m unittest tests.test_<module>               # one test module
.\.venv-next\Scripts\python.exe agent_assimilation.py verify --contract ecosystem_theory_contract.json --reports-dir docs/agent_reports
.\.venv-next\Scripts\python.exe agent_personality_practical_exam.py
.\run_py.cmd <script.py> ...                                                  # launcher wrapper
.\build_demo.ps1                                                              # rebuild local HTML dashboards from saved runs
.\.venv-next\Scripts\python.exe daily_pipeline.py plan --full-market         # research-only Python runner: show the steps, run nothing
.\.venv-next\Scripts\python.exe daily_pipeline.py run --resume <run_id>       # continue a failed run from the failed step
.\.venv-next\Scripts\python.exe shadow_replay.py --variant shadow_variants/<file>.json  # test a trader variant on archived runs (read-only)
.\.venv-next\Scripts\python.exe filing_llm_extractor.py run --limit 5         # local-Ollama evidence pilot on cached 10-Ks (model in filing_llm_extraction_config.json)
.\.venv-next\Scripts\python.exe filing_llm_extractor.py review                # review CSV/MD under output/
.\start_control_center.ps1                                                    # Κέντρο Ελέγχου at http://127.0.0.1:8770 (localhost only)
.\start_full_research_with_notify.ps1                                         # research-only full run in background, notifies on end or failure
.\.venv-next\Scripts\python.exe -m research_registry.classification_reassessment [--apply]  # multi-source classification reassessment
.\.venv-next\Scripts\python.exe -m research_registry.instrument_links build   # link warrants/preferreds/units/rights to their issuer (rerun after a universe build)
.\.venv-next\Scripts\python.exe -m research_registry.sec_filing_index sync    # read missing days of the SEC daily form index (also run by the universe preparation)
.\.venv-next\Scripts\python.exe companyfacts_cache.py status|shadow           # SEC companyfacts store; mode in scanner_config (off/populate/reuse)
.\.venv-next\Scripts\python.exe run_performance_log.py backfill|record --run-id <id>  # per-run performance history: output/run_performance_history.csv (+ .md), written at the end of every run
.\.venv-next\Scripts\python.exe exit_lab.py --horizon-curve                 # evidence for the agents' holding horizons (output/strategy_lab/horizon_curve_latest.json, read-only, ~5 min)
.\.venv-next\Scripts\python.exe fundamental_lab.py [--paper-positions]      # fundamental rules + factors point in time (output/strategy_lab/fundamental_factors_latest.md, read-only; docs/FUNDAMENTAL_FACTORS_PLAN.md)
.\.venv-next\Scripts\python.exe tier_beta_check.py                       # Giannis tiers: SPY vs beta-adjusted outcomes (output/strategy_lab/giannis_tier_beta_latest.md, read-only, ~5 min)
.\.venv-next\Scripts\python.exe insider_transactions.py sync|build|daily|status   # Form 4 open-market trades: SEC quarterly data sets + daily filings (cache/sec_insider; needs SEC_USER_AGENT; nightly in the universe preparation); fundamental_lab + Giannis shadow tag
.\.venv-next\Scripts\python.exe flip_shadow.py                            # the opposite position after a direction flip, in shadow (output/paper_learning/flip_shadow_latest.md)
.\.venv-next\Scripts\python.exe news_watch.py --label manual           # headlines for held positions + candidates (Yahoo feed, read-only; output/news_watch_latest.md; nightly + pre-open)
.\.venv-next\Scripts\python.exe market_state.py [--rebuild]                 # the state of the market: reading, Greek paragraph, evidence Koulis uses (owner 2026-11-04)
.\.venv-next\Scripts\python.exe jarvis.py after-nightly|after-session [--today YYYY-MM-DD]  # Jarvis pass (run by the launchers at their end): output/jarvis/report_<day>.md, Control Center -> Jarvis
.\.venv-next\Scripts\python.exe debate_shadow.py run|evaluate                # debate shadow (RULES_REVIEW s.12): run is started by the session launcher after Koulis; evaluate runs via Jarvis
```

`.venv-next` is built from the standalone python.org 3.12 in `%LOCALAPPDATA%\Programs\Python\Python312` (no dependency on any Codex runtime).

Supported runtime is **Python 3.12** (the only dependency lock is `requirements-windows-py312.lock`; SETUP.md and the preflight require 3.12; `.venv-next` is 3.12). 3.11 is not supported.

CI (`.github/workflows/ci.yml`) runs on Windows with Python 3.12: `compileall` over the tree, `py_compile` on core modules, the assimilation verify and personality exam above, then the unit tests; a separate ubuntu `lint` job runs pinned ruff; plus `pip-audit` on the lock and `zizmor` on workflows. PowerShell launcher changes must pass the PowerShell parser.

## Architecture

Flow: sources → identity/evidence registry → **Babis** (discovery, classification, routing) → sector context (`sector_agents/`, 12 sector sub-agents) → **Giannis** (deterministic fundamental scoring) → **Petros** (synthesis, deterministic valuation) → frozen, hashed Fundamental Research Packet → traders **Yankos** (short-horizon) and **Periklis** (swing) → **Sokratis** (teaches traders only) → control team **Miltos** (15Min entry confirmation), **Koulis** (portfolio risk gate), **Thymios** (intent contract validation) → separate Alpaca paper service. **Jarvis** (operator, owner 2026-11-15) runs at the end of the nightly and of the afternoon session: health, due re-checks, loss rules as shadow lessons, proposals for the owner's yes/no/later, daily report and a Control Center page with a local-Ollama chat; it decides nothing (design: `docs/superpowers/specs/2026-11-15-jarvis-design.md`).

Details: `README.md`, `PIPELINE_OVERVIEW.md`, `ADVANCED_USAGE.md`, `docs/` (learning control plane, personality system, playbook framework, technical research lab, unknown-data architecture, security).

## Invariants — do not break

- Unknown stays unknown: a missing value is never silently treated as zero or a default. Preserve explicit unknown fields and evidence-gap reporting.
- Research packets and run manifests are immutable and hashed; don't mutate them after freezing.
- Agents keep their documented responsibilities (see role table in `PIPELINE_OVERVIEW.md`). Personality never changes evidence, scoring, risk limits, authority or execution eligibility.
- Learning is candidate → shadow → champion with **manual** promotion. New lessons go straight to shadow (owner's standing decision 2026-11-05): followed out of sample and taught by Sokratis, never changing active scores, policies, limits or orders; accepting one stays the owner's call.
- Risk tiers for Jarvis's own changes (owner 2026-11-17, after OpenJarvis's risk tiers): a fixed table in `jarvis_tiers.py`, never a model, gives each change a tier. AUTO: only Jarvis's own chat settings, within fixed bounds, logged in the report and undone by a regression check. REVIEW: everything else - every lesson, check, debate verdict and run resume stays the owner's decision. Nothing that touches scores, limits, strategies, orders or positions can ever be AUTO.
- Do not change agent thresholds, risk settings or promotion gates without a documented rationale and focused tests.
- Default/full-universe workflows must stay unable to submit orders.
- General note (owner 2026-11-07): the opposite position after a direction flip (a short closed by `thesis_direction_flip` becoming a long) is measured in shadow by `flip_shadow.py` and is meant to move into the main code at some point, once its cases pass the test in `docs/RULES_REVIEW.md` section 8. Until then it sends no order. When the verdict passes, raise it with the owner; moving it stays the owner's call.
- LLMs run only locally through Ollama (never Claude, Anthropic, OpenAI or any hosted model), never start the Ollama app (one exception, owner 2026-11-16: the Jarvis page in the Control Center starts the server, `ollama serve`, hidden on 127.0.0.1, when the owner asks Jarvis a question and Ollama is not running, and the debate shadow (RULES_REVIEW section 12) when Jarvis runs it after the session; nothing else starts it), and never make decisions. Exception (owner 2026-11-16, docs/RULES_REVIEW.md section 12): a local LLM debate in the style of TradingAgents (bull/bear/risk) may write an *opinion* on the day's approved trades, in shadow, run by Jarvis after the session (owner 2026-11-18: never during it): measured like every shadow lesson, it changes no order; only if it passes the section 8 test may it become a veto through Koulis, and turning the veto on stays the owner's call. An LLM never opens, sizes or closes a position. `filing_llm_extractor.py` produces quote-verified *candidate* evidence for human review; it writes no Sector Context Packets and nothing downstream reads it until promotion is explicitly decided.

## Repo layout

- Root: agent modules (`<agent>_*.py`), pipeline/orchestration, provider adapters, JSON contracts/registries, PowerShell launchers.
- `tests/` deterministic unit & contract tests (fixtures in `tests/fixtures`).
- `strategy_library/`, `research_registry/`, `education/`, `intel/`, `dashboard_assets/`.
- Generated, git-ignored runtime state: `data/`, `cache/`, `logs/`, `models/`, `output/`. Read them for diagnosis; don't commit them.
