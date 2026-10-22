# Every datum has a job - which learning loop each data source feeds

Owner's principle (2026-10-22): every piece of data the ecosystem collects must feed learning; the ecosystem
finds and proposes by itself, and the owner promotes. New sources register their tags in `feature_registry.py`:
the paper-trade ledger stores them in every record and the category findings test them every night without new
code. Promotion stays manual (candidate -> shadow -> champion).

| data | produced by | feeds | lessons go to |
|---|---|---|---|
| paper trades, approvals not entered, pre-open take-downs, Koulis left-outs, all candidates | `paper_trade_ledger.py` | `paper_learning.py` evaluation, control team, category findings | Koulis, pre-open, Miltos, monitors, traders |
| 15-minute replays ("had we opened it") | `paper_simulation.py` | outcomes of every non-trade | as above |
| shadow multi-day holds (fixed, ATR bracket, thesis exit) | `holding_shadow.py`, `thesis_exit.py` | holding-class lessons | traders, position monitors |
| recheck at the open | `preopen_open_recheck.py` (09:32 NY), `preopen_recheck.py` (evening) | its own report and verdict | pre-open |
| macro environment, market state | `macro_environment.py` | ledger tag, outcomes by state, Sokratis backdrop | traders |
| results per period (8 quarters, trend tags) | `periodic_results.py` | feature registry -> ledger categories; results journal | traders, Giannis |
| periodic reports (10-Q/10-K/20-F/40-F), reaction and drift | `earnings_journal.py` | its own tests on every tag | Giannis, traders, position monitors |
| company press releases (8-K 2.02, EX-99.1) | `press_release_reader.py` | results journal events (`pr_*` tags); feature registry | Giannis, traders, position monitors |
| five-year labs (strategy, exit, gap reversal) | `strategy_lab.py`, `exit_lab.py`, `gap_reversal_lab.py` | live-vs-lab comparison, Sokratis | traders |

Coverage (owner's decision 2026-10-22, after the LAES report): results per period, the results journal and the
press-release reader cover every listed company routed to company research (`research_universe.py`), not only the
shortlist; a company whose stored companyfacts did not change is not read again. Foreign filers' 6-K releases are
read too: anchored on the prior-year half when the SEC holds it, otherwise from the column years and marked
`pr_verified = no`.
