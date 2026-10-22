# Learning from paper trades

Owner's decision 2026-10-15: every phase, read-only Alpaca reads for missing fill prices, a weekly report on
Saturday, and monthly, quarterly and yearly reports.

| Phase | Module | When |
|---|---|---|
| 1. Ledger: one immutable record per trade (entry, exit, exit reason, result, context) and per decision that did not trade (approved but not entered, taken down by the pre-open check, left out by Koulis) | `paper_trade_ledger.py` -> `data/paper_learning.db` | after each afternoon session |
| 2. Evaluation per strategy, holding class, market state and exit reason; live against the five-year lab; the control team judged from what the non-trades would have done (open to close of their session, from the price store) | `paper_learning.py evaluate` -> `output/paper_learning/evaluation_latest.{json,md}` | after each afternoon session |
| 3. Teaching: live track record per strategy (shadow, nothing reads it to decide) and Sokratis's trade review (best and worst trades with their context) | same | same |
| 4. Proposals: lesson candidates in the learning control plane, only with at least 30 trades from 10 days and, for the control team, a difference of at least 0.5% per trade. A candidate changes nothing; the owner promotes it | same | same |
| 5. Reports: week (Saturday), month, quarter and year | `paper_learning.py report --auto` | nightly start, when due |

Prices come from the ecosystem's own files; only a price missing there is read from the paper account's closed
orders (read-only). An exit read only from the broker gets its reason from the stop and take-profit limits
(`exit_reason_inferred`). A complete record is never rewritten. Unknown stays unknown: a counterfactual whose
session is not in the price store yet is filled on a later run.

## Shadow trades and categories (owner's decision 2026-10-16)

"Data should be used and shared between the agents; better organisation gives clearer results."

- Every candidate the traders kept after the pre-open check (~400 a day) is a `shadow_candidate` record: what it
  would have done from open to close in its direction. Trades stay the truth about execution (entry delay, stop,
  take-profit); shadow trades measure signal quality, and reach the minimum sample in days instead of weeks.
  They are recorded also on days without orders.
- Every record carries 14 tags: sector, market-cap bucket, Giannis tier, Petros verdict, Piotroski bucket,
  catalyst, volatility (ATR) bucket, score, standing inside the strategy, pre-market gap relative to the trade,
  risk tier, technical timing, market state, weekday. A missing value is the tag "unknown", never acted on.
- `category_findings`: for each strategy and tag value, the share beating SPY against the rest of the strategy;
  both sides need 30 records (the value from 10 days), all tests together pass a Benjamini-Hochberg
  false-discovery control (q <= 0.10), and the difference must point the same way in at least two weeks. Findings
  become Koulis lesson candidates (prefer / avoid), never automatic changes.
- Shared: `output/paper_learning/category_findings_latest.json` for every agent; Sokratis teaches each trader his
  own strategies' signal quality and findings (`paper_trade_lesson`); the daily report has a summary.

## "Had we opened this position" (owner's decision 2026-10-16)

The counterfactual of every non-trade is the trade our rules would have made, replayed on the session's
15-minute bars (`paper_simulation.py`, Alpaca market data, read-only, IEX feed): entry when Miltos's trigger
confirms (a bar closing beyond the previous bar's high/low with the right body colour, bars starting before 15:00
New York), then stop-loss, take-profit (a bar touching both counts as the stop) or the session's last bar. No
confirmation = the position would not have been opened (`not_entered`, not a zero result). Approvals Miltos did not
enter also get a forced entry at the first bar, to judge his filter. The same replay runs on the real trades
(`simulation_fidelity`: mean gap and same-sign share between replay and reality). Open to close remains only as a
flagged fallback when no bars exist. First day (2026-10-15): fidelity 0.5% mean gap, 75% same sign; IEX bars are a
single exchange, so a stop hit on the consolidated tape can be missed (ASND).

## Shadow multi-day holds (owner's decision 2026-10-16, ADR-001 step 3 evidence)

The exit lab (`docs/EXIT_LAB_FINDINGS_2026-10-16.md`) found the edge in the holding period, not in the exit rule,
while every paper position closes the same day. `holding_shadow.py` replays every ledger record (trades, the
decisions that did not trade, the shadow candidates) from the open of its session for a day, week, month and
quarter (1 / 5 / 20 / 60 sessions) on the price store's daily bars, with `fixed` (out at the last close) and
`atr_bracket` (the record's own stop / take-profit). Stored in `paper_holding_shadows`; a class stays pending
(unknown) until its last session is stored. Filled by `paper_learning.py evaluate` and shown in every learning
report ("Σκιώδη κρατήματα πολλών ημερών"). No order, nothing reads it to decide; after 4-6 weeks it is the live
evidence for the owner's decision on ADR-001 step 3.

### Thesis exit on the position's own clock (owner's decision 2026-10-17)

`thesis_exit.py` adds a third shadow rule, `thesis`, for the week, month and quarter classes: the position is held
while the reason it was opened still holds, judged every night against the traders' boards of the archived runs
with what matters for its clock - week: opposite candidate or the state playbook now rejecting; month: also risk
High, Giannis's score down by 1.0, the setup gone two nights in a row; quarter: fundamentals only (risk High,
Giannis down 1.0). A stock missing from a night's board is unknown and held. The disaster stop widens with time
(1.1 x ATR x sqrt(sessions), 1.5%-20%). `thesis_exit.py` alone backtests the rule on the archived runs
(`output/strategy_lab/thesis_exit_latest.{json,md}`); only the week class has complete windows so far.

## Recheck at the open (owner's decision 2026-10-22, research only)

`preopen_recheck.py` looks again, with the official opening price, at every idea the pre-open check took down:
a gap reason comes back unless the opening gap is still against the trade by more than 1 x ATR(14); a material
8-K (results, agreements, acquisitions, officer changes ...) comes back only if the market did not open against
the trade; a supporting item (Reg FD, other events) unless the open is against it by more than 1 x ATR; unknown
prices keep it on watch. Outcomes are the shadow-trade replay already in the ledger. Its own report
(`output/paper_learning/preopen_recheck_latest.{json,md}`), written by `paper_learning.py evaluate`, so the
what-if never mixes with what happened; the learning report carries only its verdict. Also shown: "all", no idea
taken down and Miltos's confirmation alone deciding. A lesson candidate needs judged evidence on both sides.

## Results per period and the results journal (owner's decisions 2026-10-22)

Two nightly shadow steps after the macro environment. `periodic_results.py`: the last eight quarters (half-years or
years for foreign filers) of every shortlisted company from the stored SEC companyfacts, with trend tags (revenue
accelerating, margins, dilution, losses, cash runway). `earnings_journal.py` (`data/earnings_journal.db`): every
10-Q / 10-K / 20-F / 40-F of the last five years and every new one, the results known on the filing day, the
announcement reaction and the 5 / 20-session drift against SPY, and the traders' view the night before (since the
archived boards start). Every tag is tested against the rest by itself (FDR q <= 0.10, >= 50 events a side, same
sign in most years); findings become lesson candidates for Giannis. Candidates only; promotion stays manual.
The company's own numbers arrive first in its 8-K press release (exhibit 99.1, text without XBRL): reading them
the same day is the next step.
