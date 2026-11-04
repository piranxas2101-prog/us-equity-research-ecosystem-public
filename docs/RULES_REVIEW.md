# Rules review: technical analysis first, then the fundamental side

Owner's note, 2026-10-24 (after the AMSF short of 10/23): **the ecosystem's rules have to be fixed - first the
technical analysis, then the fundamental analysis.** This file keeps the list; each item is closed with evidence
(labs, the paper ledger, shadow records) and a focused test, and threshold changes only with the owner's approval.

## 1. Technical analysis (first)

| # | Finding | State |
|---|---|---|
| T1 | Miltos confirmed entries on the IEX feed alone (2-3% of the volume): AMSF's "break" of 2 cents on 413 shares did not exist on the consolidated tape. | **Done 2026-10-24**: completed SIP 15-minute bars (`entry_confirmation_rules.py`). |
| T2 | The confirmation was only "close beyond the previous bar in the trade's colour": no volume, no momentum, no look at the candle being entered (AMSF filled inside a green candle with the hour's largest volume, RSI(14) 15m at 51). | **Done 2026-10-24** with starting values: volume >= 1.0x the 20-bar average, RSI(14) 15m below/above 50, real-time price not reclaimed and no surge against the trade (IEX minutes). To measure on the ledger. |
| T3 | The volume average of the last 20 bars mixes the opening and closing spikes with midday: a midday break rarely reaches 1.0x. | **Measured 2026-10-24, basis unchanged**: replay of 361 records of the 7 paper sessions - last 20 bars >= 1.0x: 87 entered, +0.30%, 60% won; same 15-minute slot of 20 sessions >= 1.0x: 114, +0.08%, 50%; no volume check: 192, +0.06%, 48%; all 95% day-bootstrap intervals span zero and each other. The live basis stays "last 20 bars" (now regular-session bars only); both ratios are recorded on every confirmation. Re-judge with about 20 sessions. |
| T4 | The daily strategies of the lab (rsi2, gap_fade, gap_continuation, momentum_12_1, high_52w_breakout, volatility_compression_breakout) were not in the playbook catalog: no trade plan, no agent horizon, and their shorts skipped the global SHORT contract that blocked every catalog short (shortability, borrow carry and squeeze risk were never known). | **Done 2026-10-24 (owner's choice B)**: in the catalog and both portfolios; the gates are resolved before each session from the broker (shortable, easy to borrow) and short interest/float (Giannis, yfinance cache, yfinance for the day's short candidates): `alpaca_short_gate_data.py`, resolution checked by Koulis and Thymios. On the 10/23 candidates 70 of 99 shorts pass, 29 stay blocked (25 squeeze risk or unknown, 9 not shortable or hard to borrow). Thresholds to measure: short interest < 20% of float, float >= 20M shares. |
| T5 | Strategies with no stable edge at any horizon (rsi2 short and others) kept trading. | **Done 2026-10-26 (owner's choice 3)**: three tiers (`strategy_tiers.py`, docs/T5_STRATEGY_EVIDENCE_2026-10-24.md). Horizon: stable edge, held for the agent's horizon. Same-day measured: no edge, judged after 30 trades in 20 sessions against the same strategy's candidates without an order (proposal only). Shadow only: gap_continuation long, mean_reversion_overextension long, gap_fade long (worse than a random entry within days) - recorded, never placed. |
| T6 | Confirmation sees bars 16-31 minutes late (free SIP is 15 minutes delayed). Measured on the 48 Miltos entries of 10/26: median 22 minutes after the trigger candle closed, fill on average 0.45% worse than that close (worse in 43). | **Done 2026-11-03** (owner, after the month-end review below): the trigger passes when either feed shows the break (`entry_confirmation_rules.TRIGGER_SOURCE = "iex_or_sip"`; "still holding" then runs from the IEX candle; a thin IEX candle leaves SIP alone deciding). Shadow from 2026-10-27 (owner's option 2): every confirmation also judges the trigger on 15-minute candles built from the free plan's real-time IEX minutes (`entry_confirmation_rules.iex_trigger`, candles with fewer than 5 traded minutes stay unknown) and writes it beside the SIP decision to `output/paper_session/iex_trigger_shadow_<date>.jsonl`; participation and RSI stay on SIP; nothing decides on it. A replay of 12 entries of 10/26: on liquid names IEX fired 27-50 minutes earlier at 0.4-2.3% better prices; on thin names it could not judge. A paid real-time SIP feed stays the alternative. |
| T7 | The session report showed only Miltos's last cycle ("entered 0" on 10/23 and 10/26, when Miltos had entered AMSF and all 48 entries of 10/26), no strategy per position, no held positions, and one line per Koulis approval. | **Done 2026-10-26**: the whole day - every entry with strategy, tier, New York time and who placed it; what kept the waiting entries out; results per strategy and tier; the held positions with result, sessions held, stop and expiry; Koulis as a summary (`paper_session_report.py`, schema v2). |
| T8 | Intraday evidence for horizons shorter than a session: the horizon curve is built on daily bars. | **Done 2026-10-27** (`intraday_lab.py`, 4333 signals of the last year, 1311 tests, FDR q 0.10, paired with a random day of the same stock): (A) no strategy has an edge under one session, so no sub-session horizon (owner's choice A); support_bounce_swing long's edge is strongest from the open (+0.62% to the close from 09:30, +0.51% from 10:00). Three shorts (early_accumulation, controlled_breakout, pre_run_breakout) are worse than random when entered 10:00-11:00 for hours, but all three have a 60-session edge on the horizon curve (+3.7 to +3.9%), so they were **not** made shadow-only (owner chose B1 on 2026-10-27: they stay as they are, C1 measures their wait). (C1) the session report measures Miltos's wait: price at 09:35 New York against every Miltos fill, and the 09:35-to-close move of the entries that never entered (`paper_session_report.py`, report only). The hour of entry for all strategies together differs from random by -0.02 to +0.07%: no time-of-day rule. |

## 2. Fundamental analysis (after)

| # | Finding | State |
|---|---|---|
| F1 | Negative forward P/E rule (LAES report, 2026-10-22). | **Done 2026-11-03**: the lab (section 4) confirms negative fails (-4.86% against P/E 0-35, 0/6 years) and supports the 35 line; no change. |
| F2 | Foreign issuers: 6-K reading and coverage beyond the shortlist. | **Done** (`press_release_reader.py` reads 8-K 2.02 and 6-K of every listed company); unanchored reads stay `pr_verified=no` by design. |
| F3 | Giannis's sensitivity to filing recency; bank revenue definition; empty sector layer (analysis 2026-09-29). | **Done**: periodic-report freshness (2026-10-01, supported by the lab: 101-200 days -0.73%), bank revenue proxy (docs/BANK_REVENUE_PROXY.md), sector evidence tiers from SEC text (2026-10-06). Owner 2026-11-03 after the lab: Babis judges the rules SEC-first (`pipeline.babis_sec_first`, prefetch coverage 100%; Giannis already did) and debt/equity <= 150 keeps being measured with weight 0 (no edge). |
| F5 | Held positions are not protected from results gaps: the pre-open check judges new entries only, so GOOGL (10/29) gapped 10.5% through its stop on earnings. | **Done 2026-11-03** (owner agreed to all three after the results lab, section 5): `results_calendar.py` every night; the three legacy shorts that lose through results close 3 sessions before the expected report (multi-day monitor, `results_ahead`) and Koulis does not open them inside that window; the expected results date resolves `catalyst_timing` for the shorts that gain through results (`RESULTS_CATALYST`: momentum_12_1, gap_fade, mean_reversion_overextension - of these only mean_reversion_overextension declares the gate). Every other position sits through results. |
| F4 | Fundamental thesis checks for held positions (risk High, Giannis down 1.0) are starting values from `thesis_exit.py`. | Open: measure on the first held positions. |

## 3. Month-end review, 2026-11-03 (sessions 10/27-11/02)

Five sessions, the first with positions held for days, in a falling market (SPY -1.77% on 10/30, -0.48% on 11/02
close to close). Evidence, not proof.

* **Miltos's wait (C1).** 106 Miltos entries paid on average 1.52% against the 09:35 price; from 09:35 to the
  close they moved +1.34% in their direction, so entry to close -0.19%. The 275 that never entered moved -0.71%.
  Entering all 381 at 09:35 would have given -0.14%: the wait gives the same result with 3.5 times fewer positions.
  The selection is real; the late entry eats it - hence T6.
* **T6 IEX shadow.** See T6 above: decided 2026-11-03.
* **Held positions.** 26 closed on the widening stop (-5.8%; after the exit they moved a further -0.45% by 11/02, so
  the stops did not cost), 24 on the market shock (-1.17%; by 11/02 they rose +2.28%, 19 of 24 better held), 7 on a
  broken thesis (+3.3%). The shock of 10/30 was a borderline one: SPY was -1.96% at 15:45 New York against a 1.85%
  limit (2 x ATR(14)) and closed -1.77% - the lab, judging the close, would not have counted it; Yankos bought six of
  the names again on 11/02. **Owner's decision: no change** - one event against five years (+0.39 points per quarter
  hold); keep measuring. Stops exit close to their level except results gaps (GOOGL 10.5% beyond) - F5.
* **Same-day strategies.** 67 closed, -0.30%, 30 won: in line with T8 (no edge under one session).
* **"possibly delisted" in the pre-open log.** Yahoo's request limit on 1-minute pre-market bars (the code asks
  Alpaca IEX first, then Yahoo, and retries); 154 of 238 symbols had a pre-market price on 11/02, the rest stay
  unknown. No change.

## 4. Fundamental rules lab, 2026-11-03 (F1, F3)

`fundamental_lab.py` (research only): the eight rules of `scanner_config.json` replayed point in time on the
stored SEC companyfacts (only facts filed by each month's first session) and five years of daily prices; 5,259
companies, 280,595 observations, 2021-11 to 2026-10; excess = forward return minus that day's median of all
companies, clipped to the day's 1st-99th percentile; spreads averaged over dates, 95% month-block bootstrap.
Report: `output/strategy_lab/fundamental_rules_latest.md`. Forward P/E has no history: trailing P/E stands in.
Survivorship: today's companies only.

Pass against fail, 60 sessions (spread %, 95% interval, years better):

| rule | spread | 95% | years |
|---|---|---|---|
| forward P/E <= 35 (trailing) | +4.27 | 1.60 / 6.18 | 6/6 |
| roe >= 12% | +2.42 | 0.68 / 3.71 | 6/6 |
| current ratio >= 1 | +2.22 | 0.52 / 4.11 | 4/6 |
| earnings growth >= 5% | +2.15 | 1.27 / 2.82 | 6/6 |
| profit margin >= 10% | +1.84 | -0.01 / 3.22 | 6/6 |
| revenue growth >= 5% | +1.22 | 0.53 / 1.98 | 4/6 |
| gross margin >= 30% | +0.84 | -0.42 / 2.32 | 4/6 (64% unknown on SEC) |
| debt/equity <= 150 | **-0.91** | -1.75 / 0.04 | 2/6 |

* The score works: weights passed >= 70% against < 40% gives +3.88% over 60 sessions (2.15 / 5.34, 6/6 years),
  rising through the five score buckets.
* **F1:** a negative P/E (losses) does worse than a P/E of 0-35 (-4.86%, 0/6 years): the owner's rule of
  2026-10-22 (negative fails) is confirmed. Above 35 the excess falls gradually (35-50 +2.9%, 50-100 +2.0%) and
  turns negative above 100 (-2.0%): 35 is a reasonable line.
* **Debt/equity <= 150 has no edge** (SEC gives long-term debt only, 47% unknown).
* Revenue growth above ~60% and current ratio above ~8.6 do worse than the middle (deciles).
* **F3:** a company whose last 10-Q/10-K is 101-200 days old does worse (-0.73% at 60 sessions against +1.4 to
  +1.6% for 0-100 days): the stale-report penalty of 2026-10-01 (135 days) has support.

## 5. Results lab, 2026-11-03 (F5)

`results_lab.py` (research only), on the results journal (`data/earnings_journal.db`: 77,098 periodic reports of
5,095 companies, five years) and the strategy lab's signals. Report: `output/strategy_lab/results_lab_latest.md`.

* **Calendar:** "the same report a year earlier + 52 weeks" lands within 7 days of the real filing in 89% of
  60,788 cases (95% within 14, 10%-90% error -5 to +7 days; "last report + 13 weeks" only 38% within 7). The
  next results date can be estimated from SEC data alone.
* **Risk of a report:** the results window (close two sessions before the filing day -> close of the session
  after it) moves a stock 7.1% on average against SPY; 25% of reports cost a long more than 5%, 12% more than 10%.
* **Sitting through results, 60-session hold, in the trade's direction minus SPY** (what stepping aside saves or
  gives up):

| strategy, side | mean % | 95% | below -10% |
|---|---|---|---|
| support_bounce_swing long (Yankos, held 60) | **+0.96** | 0.51 / 1.39 | 5.5% |
| momentum_12_1 short (Periklis, held 10) | **+1.09** | 0.47 / 1.67 | 12.6% |
| gap_continuation short (held 20) | +0.48 | -0.08 / 1.12 | 11.5% |
| controlled_breakout_swing short (held 60) | **-1.30** | -2.25 / -0.48 | 18.5% |
| early_accumulation_swing short | **-1.25** | -2.16 / -0.42 | 18.1% |
| pre_run_breakout_confirmation short | **-1.25** | -2.16 / -0.42 | 18.1% |
| momentum_12_1 long, rsi2, failed breakdown, 52w high | about 0 | spans 0 | 7-12% |

  The legacy shorts share the same signal days (identical rows), so the last three are one finding, not three.
  No blanket "out before results": the main held long (support_bounce) and the momentum short gain through
  results on average - GOOGL's -13% was the 5.5% tail. The legacy shorts lose through results.
* **Owner's decisions 2026-11-03 (all three):** the calendar runs every night after the results journal
  (`results_calendar.py` -> `output/results_calendar_latest.json`, archived with the run); `STEP_ASIDE`
  (controlled_breakout_swing, early_accumulation_swing, pre_run_breakout_confirmation shorts) closes 3 sessions
  before the expected report or while it is up to 14 days overdue, and Koulis blocks a new one inside that window
  (`results_ahead`) so it is not opened only to be closed; `RESULTS_CATALYST` dates the catalyst gate. An unknown,
  stale (> 4 days) or long-overdue estimate decides nothing.
* **Correction the same day:** results are announced before the periodic report is filed - for 10-Qs about a day
  (93% of 216 checked against the 8-K 2.02 of October 2026), for 10-Ks up to weeks (PTC: announced in early
  November, 10-K on 2025-11-21). The local 8-K index starts in May 2026, too late to date last year's announcements,
  so the calendar gives a results window: it opens 5 days before the expected filing for a quarterly report and
  28 days before it for an annual one, and the step-aside works from the window's opening. Share classes share
  their company's calendar (GOOGL had none: the journal files Alphabet under GOOG). The lab's results window
  (2 sessions before the filing) misses part of the annual-report reactions; its quarterly findings stand.
* **SEC results releases (owner 2026-11-03, "do the dates from the SEC too"):** `sec_results_releases.py` reads
  every company's SEC submissions file (8-K items) through the shared SEC rate limiter - 800 companies a night,
  each asked again after 30 days - and stores every 8-K with item 2.02 (`data/sec_results_releases.sqlite3`).
  The calendar dates each periodic report by the release filed in the 45 days before it; an estimate resting on
  an exact release a year earlier opens its window 5 days before (not 28 for a 10-K). PTC: expected 2026-11-04
  from its 8-K of 2025-11-05, instead of the 10-K filing date 2025-11-21. Foreign 6-K filers keep the
  filing-date estimate.

## 6. Owner's decisions, 2026-11-05

**Swing longs hold for time.** "A swing long needs time; it cannot open and close the same day." The longs of the
playbooks in `paper_session.swing_long_playbooks` (scanner_config.json: support_bounce_swing,
controlled_breakout_swing, early_accumulation_swing, pre_run_breakout_confirmation, failed_breakdown_reclaim) are
never same-day. A real edge in the horizon curve keeps its horizon (support_bounce_swing: 60 sessions). Without one,
the horizon is the one inside the playbook's own catalog range with the best positive edge per sqrt(session) in the
curve's point estimates, or the range's lower bound when none is positive; the plan marks it `stable_edge: false`.
With the curve of 2026-10-22: controlled_breakout_swing 5, early_accumulation_swing 5, pre_run_breakout_confirmation
5, failed_breakdown_reclaim 3 sessions. The monitors still close earlier on the stop, a broken thesis or a change of
the market's mood. Shorts and momentum_12_1 / high_52w_breakout / volatility_compression_breakout longs are
unchanged (their curve is negative at every longer horizon; the owner decides separately). Tests:
`tests/test_swing_long_hold.py`.

**Every lesson goes to shadow.** New lessons move candidate -> shadow on creation (`AUTO_SHADOW_REASON`), and the
ones still waiting move with `agent_learning_control_plane.py shadow-candidates`. Each paper-trading lesson is
followed only on the sessions after it entered shadow (`paper_learning.shadow_follow`: confirmed / not confirmed /
collecting, from 10 sessions), and Sokratis teaches the traders the lessons about them and their strategies.
Shadow changes nothing; acceptance stays manual. Tests: `tests/test_shadow_lessons.py`,
`tests/test_agent_learning_control_plane.py`.


**The ecosystem says whether a swing hold should change.** `paper_learning.swing_hold_review`, every evaluation:
each swing long's planned hold is compared, on the same records (trades and every candidate, from the shadow
holds), with closing the same day and with the next longer class. Verdict: collecting (fewer than 30 paired records
from 10 sessions), close_same_day or hold_longer (difference of 0.5%+ per record, most records on that side, sign
test p < 0.05; it also becomes a lesson, straight to shadow), keep_confirmed (the hold clearly better) or keep (no
clear difference). Shown in the learning report ("Χρειάζεται αλλαγή η διάρκεια των swing long;") and taught by
Sokratis. First reading (2026-11-05, 10 sessions): early_accumulation_swing +0.8% vs the same day (p 0.013),
pre_run_breakout_confirmation +2.2% (p 0.019), controlled_breakout_swing +1.2% (p 0.17): keep;
failed_breakdown_reclaim −1.6% (p 0.001) on 9 sessions: collecting, a change verdict likely after one more session.
