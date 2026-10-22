# Setup Guide

This guide is for running the project locally with your own API keys and paper-trading account.

## Quick local installation (no server)

On Windows, the supported bootstrap creates the isolated Python runtime, installs the
locked dependencies, creates a local `.env` when missing, initializes SQLite and runs
the installation smoke tests:

```powershell
.\setup_windows.ps1
```

Everything runs on this computer. The dashboards are standalone HTML files and require
no web server. A server is only an optional future deployment choice for remote or public
access; it is not part of the local research workflow.

After at least one immutable run exists, generate the local portal, symbol dashboard,
learning dashboard and analyst report together:

```powershell
.\build_demo.ps1
```

## 1. Python

Use Python 3.12 on Windows. Create the isolated runtime required by the full-research launcher:

```powershell
py -3.12 -m venv .venv-next
.\.venv-next\Scripts\python.exe -m pip install --require-hashes -r requirements-windows-py312.lock
```

The local launcher prefers `.venv-next` and can confirm which Python is available:

```powershell
.\run_py.cmd --version
```

The general `requirements.txt` supports development, but the full-research launcher deliberately requires `.venv-next` with the hashed Windows lock. `TA-Lib` may require a platform-specific wheel or system dependency.

Before a full-universe background run, refresh and reconcile the listed-symbol routes, then
execute the local fail-closed preflight:

```powershell
.\.venv-next\Scripts\python.exe .\prepare_full_research_universe.py --refresh-sources
.\.venv-next\Scripts\python.exe .\full_research_preflight.py
```

Preparation refreshes the three public listing/profile snapshots, rebuilds the complete
exchange catalog, refreshes the SEC company-eligibility receipt, and assigns every listed
symbol either to company fundamental research or to an instrument-specific observation lane.
The preflight verifies the locked Python 3.12 runtime and core package versions, source
freshness, catalog/eligibility/routing snapshot agreement, uncapped universe and price
settings, known-company classification smoke cases, and the research-only launcher flags. It writes
`output/full_research_preflight_latest.json`; it does not contact a provider or start an
agent. `start_full_research_control_plane_background.ps1` performs preparation and preflight
and blocks the launch on any failed reconciliation.

## 2. Environment

Copy `.env.example` to `.env` and fill in your own credentials.

```powershell
Copy-Item .env.example .env
```

Required for paper trading:

- `ALPACA_API_KEY_ID`
- `ALPACA_API_SECRET_KEY`

Recommended data providers:

- `POLYGON_API_KEY`
- `FMP_API_KEY`
- `FRED_API_KEY`
- `SEC_USER_AGENT`

Never commit `.env`.

## 3. Daily Research Pipeline

Run the research-only complete listed-symbol pipeline independently of the terminal:

```powershell
.\start_full_research_control_plane_background.ps1
```

The worker writes local state and a completion report under `output/`. It does not submit paper or live orders. For the separate daily pipeline (capped by `scanner_config.json` unless reconfigured), use:

```powershell
.\start_dual_watchlist_background.ps1 -FullMarket -MeetingDurationMinutes 0
```

Check the separate daily pipeline's status:

```powershell
.\check_dual_watchlist_status.ps1
```

Stop that daily pipeline if needed:

```powershell
.\stop_dual_watchlist_background.ps1
```

## 4. Local Symbol Dashboard

From the repository directory, run:

```powershell
.\open_symbol_research_dashboard.ps1
```

The command verifies the captured snapshots used by the dashboard, reads matching same-run packets from the local SQLite control plane when available, creates a standalone HTML file under `output/dashboards/`, and opens it in your browser. When the dated full-market catalog export is available, you can search every listed symbol, including ETFs and other instruments. Symbols without saved agent analysis are clearly marked; a catalog entry does not imply research was completed. For analyzed companies, expand Babis, sector, Giannis and Petros to see their results, source periods and evidence gaps. Existing trader artifacts are shown only in a collapsed, read-only section. The dashboard never calls a provider, LLM or broker and does not poll or refresh itself.

To open a specific immutable run:

```powershell
.\open_symbol_research_dashboard.ps1 -RunId full_research_20260920_170826
```

If the snapshots have been changed or a captured artifact belongs to another run, generation stops. The generated HTML is a local research snapshot, not a live feed or an order interface.

The full listing catalog and the saved company analyses have separate counts. A catalog entry means that the instrument was classified and routed; it does not mean that it passed the company-analysis gates. The current configuration has no price cap. Historical capped artifacts remain historical and are never relabeled as unrestricted research.

## 5. Trader Workflow

The daily workflow is research-only by default. Broker execution, when explicitly requested, is paper-only.

```powershell
.\run_daily_dual_watchlist.ps1
.\run_daily_dual_watchlist.ps1 -SubmitPaper
```

The first command creates research artifacts only. The second is an explicit request for the guarded Alpaca paper workflow.

Manual guarded paper start:

```powershell
.\start_trader_paper_trading_today.ps1
```

Before running the trader workflow, review and set your own risk parameters:

- `-NotionalPerTrade`: paper-trade size.
- `--stop-loss-pct` in monitor workflows: stop-loss threshold.
- `--take-profit-pct` in monitor workflows: take-profit threshold.
- Holding-period / max time-in-trade rules: same-day only by default in the current workflow.

These values are user settings. The defaults are paper-testing assumptions only and should not be treated as suitable for every account, symbol, liquidity profile, or strategy timeframe.

Main roles:

- `yankos` and `periklis` decide trader entries.
- `miltos` watches waiting setups for `15Min` confirmation.
- `koulis` owns the portfolio-level proposal and exposure gate.
- `thymios` validates approved intents before broker submission and does not originate trades.

Trading window:

- Entries: `16:30-22:00` Athens time.
- Position management/exits: `16:30-23:00` Athens time.

## 6. Generated Files

Generated outputs are intentionally ignored by Git:

- `output/`
- `logs/`
- `cache/`
- `data/*.db`

Keep these local unless you intentionally export sanitized examples.
