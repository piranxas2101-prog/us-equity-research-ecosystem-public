# Symbol Classification and Trader Evaluation

## Three separate layers

The information ledger records information. It maps each common-stock symbol to the
stable hierarchy `sector -> industry -> symbol`, preserves the provider labels and
their provenance, and reports missing classification. It does not award points,
rank sectors, or infer that one sector is better than another.

Information evaluation is a separate future/output layer. It evaluates source
quality, freshness, completeness, traceability, classification correctness and,
when enough later evidence exists, whether the information claim was confirmed.
It never uses trade P&L as a substitute for information quality.

Trade evaluation starts only after a trader signal has been written to the
immutable experiment registry and a future price outcome exists. Scoreboards are
therefore properties of observed trader results, grouped by agent, playbook,
direction, horizon, catalyst, sector, and industry. They are not properties of the
classification metadata itself.

## Internal sector taxonomy

Provider labels are normalized into eleven broad internal sectors plus `unknown`.
Industry identifiers are namespaced below their sector, for example
`technology.software_infrastructure`. Missing labels remain unknown; the ecosystem
does not guess a sector from a company name or catalyst.

## Trade scoreboard evidence

Every scoreboard includes sample count, win rate, Wilson lower bound, directional
expectancy, payoff ratio, profit factor, dispersion, and an evidence status. Short
signals invert the underlying symbol return. Groups below 30 completed outcomes are
explicitly exploratory.

## Index-analysis gate

Index analysis is a later phase. It remains disabled until classification coverage,
sector breadth, distinct symbols, distinct run dates, completed outcome volume, and
benchmark-relative returns all pass their gates. Symbol information can then roll
up to sectors and sectors to an index without pretending that early sparse data is
index evidence.
