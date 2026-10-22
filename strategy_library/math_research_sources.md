# Mathematical Research Sources

- Version: 1
- Updated: 2026-04-30
- Purpose: Define the approved mathematical and academic source base for improving technical analysis without copying untested trading ideas directly into execution.

## Policy

- Mathematical sources can teach structure, validation, statistics, and risk control.
- They do not automatically create trading signals.
- Every idea must be converted into a local rule, tested, and reviewed before it can influence `yankos`, `periklis`, or `miltos`.
- Prefer official, academic, institutional, or primary documentation sources.
- Avoid social media, anonymous forums, newsletters, and unverifiable "guru" strategies.

## Core Mathematical Pillars

### Probability and Inference

Use for confidence scoring, false-positive control, Bayesian updates, and uncertainty-aware decisions.

- MIT OpenCourseWare / MIT Open Learning Library - Introduction to Probability and Statistics:
  - https://ocw.mit.edu/courses/18-05-introduction-to-probability-and-statistics-spring-2022/
  - https://openlearninglibrary.mit.edu/courses/course-v1:MITx+18.05r_10+2022_Summer/about
- NIST/SEMATECH Engineering Statistics Handbook:
  - https://www.nist.gov/programs-projects/nistsematech-engineering-statistics-handbook
- SciPy hypothesis testing documentation:
  - https://docs.scipy.org/doc/scipy/tutorial/stats/hypothesis_tests.html

Pipeline use:

- Replace vague confidence language with explicit evidence fields.
- Track whether a signal was validated against a null expectation.
- Avoid promoting a pattern because it "looked right" on a few charts.

### Technical Pattern Validation

Use for converting chart patterns into objective rules.

- Lo, Mamaysky, Wang - Foundations of Technical Analysis:
  - NBER: https://www.nber.org/papers/w7613
  - MIT author page: https://web.mit.edu/Alo/www/Papers/techanal.html
  - Columbia Business School summary: https://business.columbia.edu/faculty/research/foundations-technical-analysis-computational-algorithms-statistical-inference-and
- Brock, Lakonishok, LeBaron - Simple Technical Trading Rules and the Stochastic Properties of Stock Returns:
  - Santa Fe Institute: https://web-prod.santafe.edu/research/results/working-papers/simple-technical-trading-rules-and-the-stochastic-
  - Brandeis record: https://scholarworks.brandeis.edu/esploro/outputs/journalArticle/Simple-Technical-Trading-Rules-and-the/9924036588601921

Pipeline use:

- Treat support, resistance, moving averages, breakouts, and candle patterns as testable event definitions.
- Compare post-signal returns to unconditional returns.
- Require objective entry/exit/invalidation definitions before a strategy can leave `training_only`.

### Backtest Hygiene and Overfitting Control

Use for deciding whether a strategy result is real enough to test in paper trading.

- Bailey, Borwein, Lopez de Prado, Zhu - The Probability of Backtest Overfitting:
  - SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253
  - Mathematical appendices: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2568435
- White - Reality Check for Data Snooping:
  - Econometric Society: https://www.econometricsociety.org/publications/econometrica/2000/09/01/reality-check-data-snooping
  - DOI: https://doi.org/10.1111/1468-0262.00152

Pipeline use:

- Penalize strategies discovered after many parameter trials.
- Require out-of-sample or forward paper evidence before promotion.
- Record how many variants were tested so we do not mistake search luck for edge.

### Market Regime and Adaptation

Use for deciding when a strategy should be active, reduced, paused, or retired.

- Andrew Lo - Adaptive Markets Hypothesis:
  - SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=602222
  - SSRN related publication: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1702447

Pipeline use:

- Do not assume a strategy works forever.
- Track regime tags such as trend, range, high volatility, low liquidity, gap day, and news-driven day.
- Retire or pause playbooks that stop working in current market conditions.

### Risk, Position Sizing, and Drawdown Control

Use for guardrails, not for aggressive automated sizing.

- Kelly - A New Interpretation of Information Rate:
  - Nokia Bell Labs: https://www.nokia.com/bell-labs/publications-and-media/publications/a-new-interpretation-of-information-rate
  - DOI metadata: https://doi.org/10.1002/j.1538-7305.1956.tb03809.x
- CFA Institute - Introduction to Risk Management:
  - https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/introduction-risk-management
- FINRA day-trading risk references:
  - https://www.finra.org/investors/investing/investment-products/stocks/day-trading
  - https://www.finra.org/rules-guidance/rulebooks/finra-rules/2270

Pipeline use:

- Position sizing remains user-configurable.
- Kelly-style ideas may be used only as educational/risk-analysis context, not as default sizing.
- Risk/reward must be visible in reports, with take-profit larger than stop-loss unless explicitly overridden by the user.

### Implementation and Research Tooling

Use for reproducible calculations and fast experiments.

- NumPy random sampling / simulation:
  - https://numpy.org/doc/stable/reference/random/index.html
- pandas rolling windows:
  - https://pandas.pydata.org/pandas-docs/stable/reference/api/pandas.DataFrame.rolling.html
- statsmodels time-series tools:
  - https://www.statsmodels.org/stable/tsa.html
- vectorbt:
  - https://vectorbt.dev/

Pipeline use:

- Use rolling calculations without lookahead.
- Use simulation and resampling to stress-test fragile rules.
- Keep research notebooks/scripts separate from live paper-trading execution.

## First Mathematical Modules To Build

1. `confidence_score_math`: converts evidence into probability-aware confidence labels.
2. `overfit_guard`: records sample size, variant count, in-sample/out-of-sample split, and promotion status.
3. `regime_detector`: tags market context before a strategy is judged.
4. `risk_reward_validator`: checks whether proposed TP/SL/holding period is coherent and user-configured.
5. `pattern_event_tester`: compares post-signal returns to baseline returns for a clearly defined event.

## Promotion Rule

A mathematical idea becomes useful only after this path:

1. `research_note`
2. `training_only`
3. `paper_candidate`
4. `active_paper_playbook`
5. `retired`, `paused`, or `kept`

No source, however reputable, skips this path.
