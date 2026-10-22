# US Equity Research Ecosystem

Local, evidence-first research for the complete US-listed symbol catalog. The project collects market and filing data, routes each instrument to the correct research lane, coordinates specialized agents and preserves the evidence behind every conclusion.

> **Research only.** The default and full-universe workflows cannot submit orders. Paper execution is a separate, explicit workflow. This project is not financial advice and does not claim proven investment performance.

**For visitors.** This is a personal research project run on one Windows machine: a team of deterministic agents
(no hosted AI model; a local Ollama model only extracts candidate evidence for human review) studies every US-listed
company each night, and a separate paper-trading workflow (Alpaca paper account, no real money) tests the ideas.
Everything the system learns - from paper trades, shadow trades, five-year labs and company results - stays a
*candidate* until the owner promotes it by hand. Paper results and backtests here are evidence for that learning,
not a promise of returns. Credentials live in a local `.env` that is never committed (template: `.env.example`).

## Current verified state

The latest completed full run, `full_research_20260924_134838`, produced:

| Measure | Result |
| --- | ---: |
| US-listed symbols cataloged and processed | 13,241 |
| Company fundamental route | 5,494 |
| Instrument observation routes | 7,747 |
| Saved company analyses | 1,068 |
| Fundamental evaluations completed/reused | 898 |
| Explicitly blocked by evidence/readiness gates | 170 |
| Stage-1 technical errors | 0 |
| Agent artifact audit | Pass |

The receipt is `review_required` because the system exposes missing evidence and coverage gaps instead of converting them into false certainty.

## Architecture

```mermaid
flowchart LR
  Sources[Market, SEC and macro sources] --> Registry[Identity and evidence registry]
  Registry --> Babis[Babis: discovery and routing]
  Babis --> Sector[Sector context]
  Sector --> Giannis[Giannis: fundamental scoring]
  Giannis --> Petros[Petros: synthesis and valuation]
  Petros --> Packet[Frozen research packet]
  Packet --> Traders[Yankos and Periklis]
  Traders --> Sokratis[Sokratis: trader teacher]
  Traders --> Risk[Miltos, Koulis and Thymios]
  Risk --> Paper[Explicit paper-only execution]
  Babis --> Learning[Versioned learning control plane]
  Giannis --> Learning
  Petros --> Learning
  Learning --> Evaluator[Independent evidence evaluator]
```

Every packet carries run identity, timestamps, source lineage and explicit unknown fields. A missing value remains unknown; it is never silently treated as zero. Run artifacts are hashed and captured in immutable manifests.

## Agents

| Agent | Responsibility |
| --- | --- |
| Babis | Full-market discovery, instrument classification and research routing |
| Giannis | Deterministic fundamental scoring, evidence gaps and risk labels |
| Petros | Fundamental synthesis, expectations and deterministic valuation |
| Yankos | Short-horizon strategy playbooks |
| Periklis | Swing strategy playbooks |
| Sokratis | Teaching and evaluating the trader agents only |
| Miltos | Entry-trigger observation for already approved paper candidates |
| Koulis | Portfolio exposure and order-intent risk gate |
| Thymios | Final validation of approved paper intents |

The independent fundamental evaluator covers Babis, Giannis and Petros. It does not change their decisions and does not change Sokratis's role.

All nine agents and twelve sector sub-agents also carry a versioned [professional personality and observable reasoning protocol](docs/PERSONALITY_SYSTEM.md). Their observation order, skepticism, revision triggers and handoff style are distinct and practically stress-tested. Personality never changes evidence, scoring, risk limits, authority or execution eligibility.

## Learning without uncontrolled self-modification

The local learning control plane stores immutable decision episodes, delayed market outcomes, independent rubric evaluations and versioned memories. Repeated gaps may create a **candidate lesson**, but candidates cannot change active scores or policies. Models use the manual path:

```text
candidate -> shadow -> champion
```

Model files are JSON-only, promotion is manual and every state transition is auditable. See [Agent learning architecture](docs/AGENT_LEARNING_ARCHITECTURE.md).

## Quick start on Windows

Requirements: Windows, PowerShell and Python 3.12.

```powershell
git clone https://github.com/piranxas2101-prog/us-equity-research-ecosystem-public.git
Set-Location .\us-equity-research-ecosystem-public
.\setup_windows.ps1
Copy-Item .env.example .env
```

Add your own provider credentials to `.env`. Credentials, databases, caches, logs, trained runtime models and research outputs are ignored by Git.

Run the complete research-only pipeline in an independent background process:

```powershell
.\start_full_research_control_plane_background.ps1
```

Build the local portal and dashboards from saved artifacts:

```powershell
.\build_demo.ps1
```

No server is required. The dashboards are standalone local HTML files.

## Repository guide

| Path | Contents |
| --- | --- |
| [SETUP.md](SETUP.md) | Installation, credentials and first run |
| [PIPELINE_OVERVIEW.md](PIPELINE_OVERVIEW.md) | Agent flow and operating boundaries |
| [ADVANCED_USAGE.md](ADVANCED_USAGE.md) | Optional workflows and detailed commands |
| [docs/](docs/README.md) | Architecture, security and design documentation |
| [research_registry/](research_registry/README.md) | Issuer identity and taxonomy registry |
| [strategy_library/](strategy_library/README.md) | Trader playbooks and validation notes |
| [tests/](tests) | Deterministic unit and contract tests |

Technical strategies pass through a separate [point-in-time research lab](docs/TECHNICAL_RESEARCH_LAB.md) that audits indicator causality and models next-bar fills, costs, drawdown, MFE/MAE, regimes and walk-forward windows before any paper-candidate review.

Generated runtime state belongs under `data/`, `cache/`, `logs/`, `models/` and `output/`; these paths are not source code and are excluded from commits.

## Data providers

The ecosystem can use SEC EDGAR, Alpaca, Massive/Polygon, FMP, FRED, Nasdaq Trader and Yahoo Finance according to the configured route and available credentials. Provider failures, quotas and unsupported instruments remain visible in receipts and evidence-gap reports.

## Security and contribution

- Read [Security and OpenClaw readiness](docs/SECURITY_AND_OPENCLAW_READINESS.md).
- Report security issues using [SECURITY.md](SECURITY.md).
- Development and review rules are in [CONTRIBUTING.md](CONTRIBUTING.md).
- Source code and project documentation are available under the [MIT License](LICENSE).
- Optional project support is described in [DONATIONS.md](DONATIONS.md); no payment link is active.
