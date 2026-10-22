# Ecosystem status

This page records the latest verified state of the local US-equity research ecosystem. It covers research and paper-trading controls; it does not establish investment performance or authorize live trading.

## Latest verified full run

Run `full_research_20260924_134838` completed the full catalog without a stage-1 technical failure.

| Measure | Result |
| --- | ---: |
| US-listed symbols processed | 13,241 |
| Company fundamental route | 5,494 |
| Instrument observation routes | 7,747 |
| Saved company analyses | 1,068 |
| Fundamental evaluations completed or reused | 898 |
| Blocked by evidence/readiness gates | 170 |
| Stage-1 technical errors | 0 |
| Agent artifact audit | Pass |

The receipt is `review_required`: the run completed technically, while evidence gaps and incomplete provider coverage remain visible for review.

## Implemented architecture

- **Universe and routing:** Babis catalogs the full US-listed universe, distinguishes operating companies from funds, warrants and other instruments, and routes each symbol to the appropriate lane.
- **Fundamental research:** sector context feeds Giannis's deterministic evidence scoring and Petros's synthesis, expectations and valuation work. Unknown values remain explicitly unknown.
- **Information control:** typed observations, claims, evidence packets, theses, relationships, checkpoints and lineage preserve who produced each item, from which source and at what time.
- **Learning control:** Babis, Giannis and Petros can accumulate independent, versioned lessons from delayed outcomes. Candidate lessons cannot alter active scoring or policies without manual promotion.
- **Trader separation:** Yankos and Periklis produce separate strategy playbooks. Sokratis teaches and evaluates only the trader agents; the new fundamental-learning path does not change his role.
- **Paper controls:** Miltos observes approved entries, Koulis applies portfolio constraints and Thymios validates same-session order intents. Broker submission remains a separate, explicit paper-only action.
- **Reproducibility and security:** manifests, hashes, receipts, evidence gaps, isolated runtime setup and local security checks make each run reviewable.

See the [pipeline overview](../PIPELINE_OVERVIEW.md), [information architecture](../research_information_architecture.json), [learning architecture](AGENT_LEARNING_ARCHITECTURE.md) and [security readiness](SECURITY_AND_OPENCLAW_READINESS.md).

## Validation boundaries

- The 13,241 catalog entries include more than operating companies; 7,747 correctly use observation routes rather than fabricated company fundamentals.
- Of 1,068 saved company analyses, 170 were blocked because the available evidence did not satisfy readiness gates.
- Miltos, Koulis and Thymios were outside this research-only full run, so this result does not validate an end-to-end paper order.
- Provider quotas, unsupported instruments and unavailable facts remain explicit in receipts instead of being replaced with assumed values.
- Learning artifacts currently record evaluations and memories; they do not prove predictive improvement until sufficient matured outcomes support comparison.

Historical September 20 reviews are retained under [archive/runs](archive/runs/) for audit context. They describe older universes and should not be read as the current system state.

Runtime data under `data/`, `output/`, `logs/`, `cache/`, `models/`, virtual environments and `.env` stays local and is excluded from Git. The repository contains code, contracts and documentation rather than credentials, databases or generated research output.
