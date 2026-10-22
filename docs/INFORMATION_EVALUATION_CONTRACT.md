# Information Evaluation Contract

Information quality and trade performance are independent evaluations.

## Information evaluation

This future evaluator reads only locked `information_observations` and source
verification records. Its dimensions are source reliability, freshness,
completeness, traceability, classification correctness, contradiction handling,
and eventual claim confirmation. Its output must never contain trade P&L,
win rate, payoff ratio, or execution metrics.

There are two time perspectives:

- Ex ante quality: what could be verified at collection time.
- Ex post validity: whether the recorded factual claim or catalyst was later
  confirmed, rejected, superseded, or remained unresolved.

## Trade evaluation

This evaluator reads only locked `trader_experiments` joined with market outcomes
and, later, execution fills. Its dimensions include direction-aware win rate,
expectancy, payoff ratio, profit factor, drawdown, slippage, and sample sufficiency.
Information-quality fields may be used as grouping dimensions, but they cannot be
counted as trade wins.

## Non-substitution rule

A profitable trade does not prove that its source information was accurate, and a
high-quality information item does not prove that a trader executed it well. Both
evaluations retain their own identifiers, denominators, and scoreboards.
