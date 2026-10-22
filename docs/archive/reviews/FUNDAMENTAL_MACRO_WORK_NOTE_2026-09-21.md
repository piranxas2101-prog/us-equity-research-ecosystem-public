# Fundamental and macro research work note

Status: agreed direction for future implementation. This note does not change agent behavior or authorize trading.

## Scope and ownership

- Keep trader, paper-order and broker workflows outside this workstream. Sokratis's role remains unchanged.
- Babis owns issuer identity, classification and the evidence-backed shortlist. He identifies possible macro exposures but does not infer their financial effect.
- A run-level macro evidence layer records a small set of relevant rates, inflation, growth, labor and energy observations. Each observation needs its series ID, unit/transformation, observation period, release or vintage date, source, retrieval time and frequency-aware freshness status. Unknown or revised data stays visible.
- The 11 sector agents connect a macro driver to an issuer only with company-specific evidence, such as segment revenue, variable-rate debt, hedging, input costs or pricing power. The unresolved-classification agent proposes labels to Babis; it does not write the taxonomy.
- Giannis owns deterministic calculations: historical financial quality, revenue and margin drivers, interest expense, cash flow, reinvestment, cost of capital, valuation ranges and sensitivity to explicit macro assumptions. No LLM-generated number becomes a financial fact.
- Petros owns the readable fundamental synthesis: observed facts, modeled assumptions, base/upside/downside cases, opposing evidence, decisive sensitivities and next diligence. He does not use trader output to rewrite the fundamental thesis.

## Mathematical controls

- Match nominal cash flows with nominal discount rates and real cash flows with real rates, in the same currency.
- Make scenario equations and assumptions explicit. For example, a rate shock may affect interest expense only through debt that actually reprices, considering maturity and hedging evidence.
- Keep FRED observations point-in-time for historical analyses; later revisions must not silently alter a previous run.
- Missing issuer exposure, tax, debt, reinvestment or growth evidence yields an explicit incomplete model. Do not fill it with guessed values or a neutral zero.
- Macro context informs company cash flows and discount-rate assumptions; it is not a standalone trade signal or automatic score adjustment.

## First implementation sequence

1. Preserve the existing FRED snapshot through Babis's handoff and publish a versioned macro evidence packet. The scan currently collects it but the shortlist handoff drops it.
2. Review the previous run's 15 blocked fundamental requests by cause (seven stale income periods, seven missing comparable revenue facts, one unavailable SEC source). Extend SEC fact selection with filing-date and period checks; preserve unresolved cases.
3. Enrich the nine unresolved sector routes and the 59 conditional sector packets with documented issuer exposures and explicit unknowns.
4. Add Giannis scenario and valuation operators with source-linked inputs and consistency checks, then Petros's concise review of assumptions and sensitivities.
5. Validate on a bounded, one-shot cross-sector pilot before another full-universe run. Compare coverage, provenance, point-in-time integrity and model completeness with the 20 September baseline. Do not add monitoring loops.

## Patterns reviewed

- [TradingAgents](https://github.com/TauricResearch/TradingAgents): specialist boundaries, as-filed SEC data and point-in-time macro handling.
- [FinRobot](https://github.com/AI4Finance-Foundation/FinRobot): deterministic financial computation separated from narrative synthesis.
- [EdgarTools](https://github.com/dgunning/edgartools): structured SEC XBRL and filing-date-aware fact queries.
- [OpenBB standardization](https://docs.openbb.co/odp/python/developer/standardization): provider-neutral input/output contracts. OpenBB code is AGPLv3; use its design ideas without copying code until license obligations are reviewed.
- [Dagster asset checks](https://docs.dagster.io/guides/test/asset-checks): explicit quality and freshness checks on data handoffs.
- [FRED observations and vintages](https://fred.stlouisfed.org/docs/api/fred/series_observations.html): observation transformations and historical data revisions.
- [Damodaran's DCF inputs](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/lectures/dcfinput.html): cash-flow and discount-rate consistency.

These are local design references, not dependencies or account connections.
