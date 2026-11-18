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
| F4 | Fundamental thesis checks for held positions (risk High, Giannis down 1.0) are starting values from `thesis_exit.py`. | Open, **measured since 2026-11-15**: `stop_shadow.py` sets every `thesis_fundamentals_weaker` (and `thesis_setup_gone`, `thesis_direction_flip`, `market_shock`, `results_ahead`) exit against the same position held on under the live stop, on whole windows only (≥30 positions from ≥10 entry days); `output/paper_learning/stop_shadow_latest.md`. First read: 6 `thesis_fundamentals_weaker` exits, none with a whole window yet. Decide once the verdict is judged. |

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

**Every lesson goes to shadow** (replaced on 2026-11-18, section 14: new lessons wait as candidates until the owner
puts them in shadow). New lessons moved candidate -> shadow on creation, and the
ones still waiting moved with `agent_learning_control_plane.py shadow-candidates` (removed 2026-11-18). Each paper-trading lesson is
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

## 7. Owner's decisions, 2026-11-06 (after SPY -4.4% on 2026-11-05)

Research behind them: five years of lab signals around the 14 SPY down shocks. Shocks up to -3% out of a calm market were followed by a rise after 5 sessions in all 8 cases; of the 4 of -3.4% or more, 3 were still falling after 5 sessions. The -4.4% came out of a calm market with the size of the second group, so the history does not settle the direction.

- **Three shorts closed for one session.** EFX, TW, ARQT (support_bounce_swing short: -6.0% over 5 sessions after a shock, 0 of 10 positive, and losing on ordinary days too). The owner chose these three over the 10 and the 13 offered; the momentum and gap shorts stay. Mechanism: `output/owner_close_requests.json`, valid only for the session it names, read by `alpaca_multi_day_monitor.py` (exit rule `owner_close`).
- **Support bounce long leaves before results.** MNTN lost 45.59% through its results on 2026-11-05. It was no fault: the calendar knew the date, and the position stayed by the F5 decision of 2026-11-03. No measure known beforehand flags it (its past reactions were 3-5%; below -25% happens in 0.6%). `results_calendar.STEP_ASIDE` now holds `support_bounce_swing long`: closed 3 sessions before the results window, not opened near it (Koulis reads the calendar for longs). Cost, accepted on purpose: the average +0.83% through results, and holds of 60 sessions now usually end at the next report.
- **Entries weigh the market at that moment.** Koulis uses the night's reading, which cannot see the day: on 2026-11-05 SPY was -2.5% (2 x ATR) at 10:30 New York and 8 longs still opened until 12:07. Miltos now waits while SPY stands 1.5 x ATR or more against the position (`MOOD_ATR`), and may still enter if the market recovers. Unknown market data blocks nothing. The 1.5 rests on the owner's decision more than on proof: last year's long entries beyond it lost most to the close (-0.60%), but on 3 days only, with a wide interval. Those left out stay measured as approved, not entered.

Still open: `failed_breakdown_reclaim` (the review says close the same day) and the other three swing longs that now sit through results with no proven gain.


## 8. Owner's decision, 2026-11-07: the opposite position after a direction flip, in shadow

The direction-flip exit closed 9 of 9 shorts at a gain in the week of 2026-11-02 (+7.08% on average). The owner asks whether the short should become a long. A flip is one stock's reading (a candidate in the opposite direction on tonight's board), not the market's direction. First look at 12 flips, long from that day's open: -1.94% the same day (3 of 12 positive); to 2026-11-06 -0.14% against SPY -2.16%; too small a sample and one market.

Decision: the short still closes as it does; the long is not opened automatically. It would be an ordinary entry through Koulis and Miltos, and `flip_shadow.py` measures it first at 1, 3, 5, 10 and 20 sessions, with no order and no change to Koulis, Miltos or the monitor. Report: `output/paper_learning/flip_shadow_latest.md`, a section in the session report, run with the nightly learning evaluation of each session. The holding time is decided later from this evidence.

To pass (30 flips on 10 sessions, as every learning judgement; judged on the entries that pass Koulis and Miltos, with the stop): mean excess over SPY at least 0.5% and the Wilson lower bound of "beat SPY" above 50% at some horizon, because five horizons are looked at. Then a lesson candidate is proposed and going into the main code stays the owner's decision. That step needs Koulis to let the candidate through its own `existing_account_exposure` block (on 2026-11-06 it blocked AMSC, NFLX, VICR for it) and the monitor to wait for the close to fill.

**Real exit prices (owner 2026-11-07).** A position held over several sessions kept only its % at the close decision and the order's answer at sending (`pending_new`, no price). `paper_trade_ledger.py` now reads each exit order by its id once it is final, read-only (`paper_exit_fills`, `output/paper_learning/exit_fills_latest.json`, a section in the session report), and compares the result at the decision with the result at the fill. The ledger's results are not rewritten: whether the reports should move to the fill is decided from that difference.

## 9. Owner's decision, 2026-11-09: a gap already in the trade's direction, and one trader on both sides, in shadow

On 2026-11-09 Yankos had a support-bounce long and a gap-fade short on NVDA from the same night. The pre-open check took the long down (pre-market gap -5.85% against it, ATR 4.28%) and kept the short, since it only looks at gaps against a trade; the short entered at 11:28 New York near the day's low and was -2.15% at the close while the sector rallied (SOXX +7.5%). `gap_conflict_shadow.py` measures, with the nightly learning evaluation and without orders: candidates whose pre-market gap is in their own direction by at least one ATR against the rest, and stocks on which one trader proposed both directions the same night against the rest. A clear gap (control margin, minimum trades and days) only proposes a lesson; changing the pre-open check or Koulis stays the owner's decision.

First reading on 18 sessions (2026-11-09): favourable gap >= 1 ATR 201 records, mean +0.43% against -0.14% for the rest (beat SPY 55.7% against 49.3%); its shorts beat SPY only 44.7%. Both directions: 362 stocks, 705 records, mean +0.06% against -0.08%, no clear difference; inside them the long side beat SPY 57.1%, the short side 44.3%.

## 10. Fundamental factors lab, 2026-11-15 (owner: research, plan and phases 1-3)

Plan and reasons: `docs/FUNDAMENTAL_FACTORS_PLAN.md`; literature: `docs/research/Θεμελιώδεις παράγοντες απόδοσης μετοχών.md`.
`fundamental_factors.py` computes six factors from the stored companyfacts point in time (cash profitability, equity
and external financing, ROA change, asset growth) and two composites (`long_quality`, `avoid`, equal-weight
percentiles). `fundamental_lab.py` replays them: 5,278 companies, 285,119 month-start observations, 2021-11 to 2026-11.
Rank IC at 60 sessions in the literature's direction (t from month blocks), best minus worst quintile:

| factor | IC (t) | best − worst | large caps | within rules score ≥ 60 | verdict |
|---|---|---|---|---|---|
| cfo_to_assets | +0.153 (9.1) | +7.5% | +10.5% | +11.2% | shadow |
| cash_op_profit | +0.155 (8.8) | +6.9% | +10.2% | +7.3% | shadow |
| net_equity_finance | +0.132 (7.6) | +6.0% | +6.6% | +3.5% | shadow |
| net_external_finance | +0.115 (6.7) | +4.9% | +5.3% | +1.3% | shadow |
| d_roa | +0.025 (2.9) | +1.4% | +2.7% | +1.8% | shadow (weak) |
| asset_growth | −0.013 (−0.8) | +0.9% | −2.4% | −1.3% | no edge (short side / micro only) |
| long_quality | +0.133 (8.1) | +6.4% | +9.0% | +7.0% | shadow |
| avoid | +0.106 (6.3) | +5.8% | +6.3% | +3.7% | shadow |

* All years positive (6/6) for the cash and financing factors. They add to today's rules: the spread holds among the
  companies the rules already score ≥ 60.
* Caution: the spreads are far larger than the literature's ~0.5%/month. The period holds the 2022 fall of the
  unprofitable growth stocks, the baseline is the day's median (no beta), and survivorship removes the delisted losers
  (that makes the short side *smaller*, not larger). Read them as direction and rank, not as expected returns.
* The system's own 181 closed paper positions (10/26-11/12, `fundamental_lab.py --paper-positions`): shorts in the
  top 20% of `long_quality` lost −2.10% (36% winners, n 14) against +2.27% (55%, n 22) in the middle. Longs lost in
  every bucket (≈ −2.5%): their problem is not fundamental. A few weeks: description, not a test.
* Phase 2 is live in shadow: Giannis writes `fundamental_factors` (values + run composites) next to his score and
  `outcome_evaluator.py` judges `giannis_long_quality` / `giannis_avoid` like the Piotroski buckets. No score, tier,
  threshold or order changes. Candidate filter for the owner to decide after shadow: no short in the top 20% of
  `long_quality`, no long in the top 20% of `avoid`.

**Shadow filter replay, 2026-11-15** (`shadow_replay.py --variant shadow_variants/fundamental_filter_<trader>_v1.json`;
the composites on the day before each run, ranked across that run's Giannis rows; 59 archived runs, 2026-09..11).
Filter: no short in the top 20% of `long_quality`, no long in the top 20% of `avoid`. Net expectancy of the decisions
the filter *drops* against those it keeps:

| trader / side | 1d | 3d | 5d | 10d | 20d |
|---|---|---|---|---|---|
| Yankos short: dropped (n 278-74) | −0.45% | −0.51% | −0.15% | +0.08% | +3.02% |
| Yankos short: kept | 0.00% | +0.29% | +0.60% | +1.70% | +5.02% |
| Periklis short: dropped (n 295-88) | −0.44% | −0.49% | −0.17% | +0.05% | +2.62% |
| Periklis short: kept | −0.02% | +0.25% | +0.56% | +1.69% | +4.82% |
| Yankos long: dropped (n 570-278) | −0.22% | −0.20% | −0.40% | −0.64% | +0.18% |
| Yankos long: kept | +0.15% | +0.35% | +0.11% | +0.02% | +0.56% |
| Periklis long: dropped (n 327-141) | 0.00% | +0.38% | +0.33% | −0.28% | +0.32% |
| Periklis long: kept | +0.12% | +0.27% | +0.07% | +0.11% | −0.19% |

* **The short filter helps both traders at every horizon** (dropped shorts are worse than kept ones each time), as
  the lab and the paper trades said. **The long filter helps Yankos** (short horizons) but **not Periklis** (mixed).
* Caution: the same companies repeat every night, so the decisions are not independent; ~2.5 months of runs. No
  decision changed. Candidate for the owner: the short filter for both traders and the long filter for Yankos, first
  as a live shadow tag, then a manual decision.

## 11. Owner's decision, 2026-11-16: the fundamental filter goes live, and the rest of the plan

The owner: "activate the filter, go ahead with everything".

**Filter, live** (`fundamental_filter.py`, config `fundamental_filter` in `yankos_playbooks.json` /
`periklis_playbooks.json`, `mode` active / shadow / off). Yankos: no short in the top 20% of `long_quality`, no long
in the top 20% of `avoid`. Periklis: the short rule only (the long rule did not help him in the replay above). The
filter runs after the readiness gate and only takes the entry away (`execution_eligible` false, reason
`fundamental_filter_short_on_quality` / `fundamental_filter_long_on_avoid`), which Koulis, Thymios and the paper
trader already honour. The trader's verdict, direction and score stay as they were, so learning, reports and the
exits of held positions (`thesis_exit.py` reads the verdicts) are unchanged. An unknown composite blocks nothing. The
replay and the live traders call the same rule. Dry run on the 2026-11-16 run (composites added in memory): Yankos
would have lost 3 eligible entries (APP short, PGNY short, PKE long), Periklis 2 (APP, PGNY short).

**Measured out of sample** (`paper_trade_ledger.py`, `paper_learning.py`): every record carries the tags
`long_quality`, `avoid` (quintile or "unknown") and `fundamental_filter` (pass / would_block / blocked / unknown); the
candidates the filter blocked are kept as `filter_blocked` records, their open-to-close filled like the shadow
candidates, and the control team compares them with the candidates the filter let through. If the blocked ones do
better by the usual margin, a lesson candidate to loosen the filter goes to the owner.

**Giannis's tiers against beta** (`tier_beta_check.py`, 202,780 outcomes, Dimson beta on 250 sessions). Return minus
beta × SPY, mean per decision date (t across dates):

| tier | median beta | 10 sessions vs SPY | 10 sessions vs beta | 20 sessions vs beta |
|---|---|---|---|---|
| Core Watchlist | 0.81 | −0.23% | −0.16% (t −0.5) | +0.03% (t 0.1) |
| Selective Watchlist | 0.75 | +0.73% | +0.60% (t 4.1) | +1.73% (t 8.9) |
| Speculative Monitor | 0.59 | +2.00% | +1.56% (t 6.8) | +2.80% (t 5.7) |
| Avoid For Now | 0.58 | +0.10% | −0.14% (t −0.8) | +0.16% (t 1.2) |

* **Beta does not explain the inversion.** The speculative tier has the *lower* beta; taking beta out shrinks its lead
  by a fifth, not more. The tiers' order is inverted on every horizon.
* What remains is the second explanation of the plan: the score measures attention and liquidity, not cash or capital
  discipline, and in these weeks attention paid. ~2.5 months, 16-33 dates, overlapping windows (t overstated at long
  horizons): a regime, not yet a rule. No score or tier changed; re-ranking the tiers stays the owner's call.

**Form 4 insiders** (`insider_transactions.py`): the SEC's quarterly Insider Transactions Data Sets (public domain;
one zip a quarter, standard library only, instead of edgartools' one request per filing). Open-market purchases (P)
and sales (S) of Forms 4 / 4/A, point in time by filing date: `insider_buyers_90d` (distinct buyers) and
`insider_net_buy_180d` ((bought − sold) / (bought + sold)). Inside the covered quarters no filing is a known zero;
outside them unknown. Both enter `fundamental_lab.py` in the literature's direction.

Lab, 2026-11-16 (19 quarters 2021q3..2026q1, 527,681 open-market trades, 6,481 issuers; the SEC has not published
2026q2-q3 yet, so the factors are unknown after 2026-03-31). The net ratio counts filings, not dollars: a few hundred
lines carry impossible prices (up to 10^15 dollars). Rank IC (t) at 20 / 60 / 120 sessions:

| factor | coverage | 20 | 60 | 120 | verdict |
|---|---|---|---|---|---|
| insider_buyers_90d | 82% | +0.011 (2.0) | +0.019 (2.8) | +0.022 (3.3) | no edge (weak) |
| insider_net_buy_180d | 47% | −0.023 (−3.4) | −0.033 (−4.5) | −0.051 (−7.3) | reversed |

* Buying points the literature's way but is weak: most companies have no buyer, so the quintiles do not form and the
  gate cannot pass; the long side (+1.6%, 5/5 years) is the only cell. Far below cash profitability (IC +0.15).
* The net ratio is reversed: companies whose insiders mostly *sold* did better. Selling follows price run-ups
  (planned 10b5-1 sales, option-funded sales in rising growth stocks), so it carries momentum, not bad news.
* Decision: neither goes live or into the composites.

**Opportunistic insiders, 2026-11-16** (owner: "make a better version"; Cohen-Malloy-Pomorski 2012). History from
2018q1 (33 quarters, 964,358 trades, 7,860 issuers). An insider who traded in each of the three years before is
*routine* if one calendar month holds a trade in all three, otherwise *opportunistic* (2025: 2,171 routine, 3,447
opportunistic). Sparse events are now tested as "has one" against "has none". 60 sessions:

| factor | rank IC (t) | has one − has none | large caps | micro | rules score ≥ 60 | verdict |
|---|---|---|---|---|---|---|
| insider_buyers_90d (all) | +0.019 (2.9) | +1.55% [0.73, 2.36] 6/6 | +0.47% [−0.25, 1.25] | +2.63% | +0.74% | passes, weak |
| **insider_opp_buyers_90d** | +0.019 (**5.4**) | **+2.71%** [1.56, 3.70] 6/6 | **+1.31%** [0.18, 2.38] 6/6 | +3.13% | +1.80% | **passes (to shadow)** |
| insider_opp_sellers_90d | −0.044 (−9.4) | −2.48% (sellers did *better*) | −3.04% | −2.05% | −0.68% | reversed |

* **Opportunistic buying is the better version**: the edge almost doubles against all buyers and, unlike them, holds
  in large caps every year. The paper's result replicates here.
* Opportunistic *selling* is reversed, against the paper: companies whose insiders sold outside their routine did
  better by ~2.5%. In 2021-2026 such sales follow price run-ups and the run-ups continued (momentum); it is not
  usable as a warning.
* **Live in shadow, 2026-11-16** (owner: "build it"). The SEC moved the newer quarters to a second folder
  (2026q2-q3 now download). After the last quarter, `insider_transactions.py daily` reads each day's form index and
  every listed company's Form 4 (cache/sec_insider/daily/; a day is written only when complete, a gap stops the
  coverage there). The universe preparation runs it every night within a budget of 4,000 filings. Giannis writes
  `insider_factors` next to his score (as of the last covered day, unknown if older than 5 days), and
  `outcome_evaluator.py` judges `giannis_insider_opp_buy` (opportunistic buyer / none). No score, tier or order
  changes.

## 12. Owner's decision, 2026-11-16: an LLM debate on the day's trades, in shadow, then possibly a veto

After reading TradingAgents (TauricResearch, Apache-2.0: four analysts, a bull/bear debate, a trader, a three-way
risk debate and a portfolio manager per symbol, a reflector that turns outcomes into lessons) the owner chose
"shadow, then a veto" (2026-11-16). The rule "LLMs never make decisions" gets one exception:

- A local LLM debate (Ollama only, never hosted) may write an **opinion** (rating and short reasons) on the trades
  Koulis approved that day. It is recorded in shadow and measured like every shadow lesson; it changes no order,
  score, limit or position.
- To pass (Claude's proposal, to confirm in the design): the section 8 test (30 cases on 10 sessions; trades the debate opposed must do worse than the ones it
  supported by at least 0.5% of excess over SPY, with the Wilson bound of the difference clear of zero). Then a
  lesson candidate proposes a **veto**: Koulis blocks an entry the debate opposes. Turning the veto on stays the
  owner's call; it can only block, never open, size or close a position.
- Measured on the owner's machine (i7-1355U, no GPU, ~28 prompt tokens/s): a full TradingAgents run is 12-20 model
  calls per symbol (30-60 minutes); the debate is built smaller, from the ecosystem's own evidence (packet, scores,
  market reading), not from TradingAgents' data vendors. Design: docs/superpowers/specs/ (to be written with the owner).

Built 2026-11-17 (design docs/superpowers/specs/2026-11-16-debate-shadow-design.md, owner approved the launcher line,
the debate starting Ollama and the pass test): `debate_shadow.py run` starts in the background after Koulis in
`start_trader_paper_trading_today.ps1`, judges the approvals in Koulis's order with one call each (bull, bear, main
risk, support/neutral/oppose), stops at 16:00 New York or when Miltos runs late twice; `evaluate` runs through Jarvis
after the ledger (`debate_shadow_s12`, raised only on `veto_would_help`). Report:
`output/paper_learning/debate_shadow_latest.md`. No order, approval, score or position changes.

## 13. Owner's decision, 2026-11-17: Petros's model, fundamental-only, in shadow

Asked to promote Petros's trained model, Claude found that Petros does not use it (it is loaded only in the legacy
mixed mode; in fundamental_only every decision had `ml_adjustment` 0) and that it is built on trader signals and price
data (Yankos/Periklis signals, RSI, 20/60-day returns, volume), which Petros - deciding before the traders - may not
see. Not promoted. The owner chose a fundamental-only model in shadow: `petros_trainer.py train --feature-set
fundamental_only` (no price, technical, trader or market-microstructure field; unknown numbers keep a `_known` flag)
trains nightly beside the legacy one as task `fundamental_only_synthesis` (`models/petros_model_fundamental_only.json`);
Petros records `ml_shadow_probability` beside each decision and changes no score, verdict or tier. First training on
2026-11-17: 26,875 rows, 73 features, walk-forward ROC AUC 0.61 (legacy mixed 0.615). Jarvis raises it for promotion
only after 20 nights of shadow, and never the legacy model. Using it in the score is the owner's decision.

Same day (owner, "yes, stop it"): the legacy mixed model's nightly training is stopped in both runners; only the
fundamental-only model trains (report `output/petros_training_fundamental_only_latest.md`). The legacy model file and
its registry rows stay as they are (history).

**Section 7 follow-up built 2026-11-17** (`shock_exits_lab.py`, Jarvis check `shock_longs_1105`): every position closed
by `market_shock`, from its real exit price to the close 1/3/5/10 sessions later, against SPY. First read: 54 longs
(20 on 10/30, 34 on 11/05 - not 40). Five sessions after 11/05 the closed longs moved -0.006% while SPY rose, -3.30%
against SPY; after 10/30 +2.64% against SPY; one to three sessions later holding would have gained on both days.
Verdict on 54 cases from 2 shock days: no clear difference (mean -1.10% against SPY, 95% [-3.30, +2.64]). The
market_shock rule stays as it is; Jarvis raises it if a verdict appears.

**Owner's decision 2026-11-18: the debate runs after the session, not during it.** Worry that it weighs on the live
session and delays it. The line that started it in `start_trader_paper_trading_today.ps1` is removed; Jarvis's
after-session pass runs `debate_shadow.py run --after-close` on the day's approvals in Koulis's order, within 50
minutes (done before the 00:30 nightly run), with the inputs as they stood at the approval. It is now a measurement
only; a veto would need the agents to ask for an opinion when they need one, which is not built.

**Owner's decision 2026-11-18 (later the same day): the debate prepares the day, in the morning.** Jarvis's
after-nightly pass, on New York weekdays, runs `debate_shadow.py run --morning` on **every** trade the traders
proposed for that day (Yankos and Periklis, verdict `candidate`, about 370 a day), strongest signal first, with no
cap; the only stop is a safety one at 14:30 Athens, before the session's preparation at 15:00. No session runs then,
so it uses 8 threads and no session guard. The evening run is removed. The measurement now counts every kind of
outcome the ledger keeps for a judged idea, one per idea and the most real first: trade, approved_not_entered,
koulis_left_out, preopen_demoted, shadow_candidate. Because opinions now exist before Koulis decides, a veto through
Koulis is possible again if the section 8 test passes; turning it on stays the owner's call.


## 14. Owner's decision, 2026-11-18: five lessons for the first measurement period, the rest archived

ADR-002 (`docs/adr/ADR-002-operating-model.md`, proposed) found 178 paper-trading lessons in shadow with no verdict.
The owner chose five experiments and archived the rest (`docs/decisions/lesson_triage_2026-11-18.json`, applied with
`agent_learning_control_plane.py apply-lesson-decisions`; nothing is deleted, every decision keeps its reason).

**What the triage found.** Simulated and real results do not agree: on the same day trades the real result was -0.27%
against SPY and the simulation +0.67%; across 268 pairs the mean gap is 1.44% and the sign agrees in 68%. Lessons that
compared real multi-day fills with simulated candidates were measuring that gap. On one yardstick (same-day
simulation for every group, one observation per session): the candidates Koulis left out did not do better than his
picks (-0.42 points a day, t = -1.4), and the approvals Miltos did not enter did worse than those he entered (-0.82
points a day, t = -3.3, traded better on 21 of 24 days). Both lessons are rejected. The losses sit in the quarter
holds of `support_bounce_swing` long (closed early -2.7%, still open -3.0%), not in the selection.

**Kept in shadow (period P1, `period_experiments.json`):**

| | Lesson | Measured |
|---|---|---|
| E1a | One slot a day for Yankos `support_bounce_swing` long | the cell against every other candidate of the day |
| E1b | One slot a day for Periklis `momentum_12_1` long | the cell against every other candidate of the day |
| E2 | Yankos `support_bounce_swing` long: avoid `market_cap = mega` | inside the cell, the value against the other known values |
| E3 | Yankos `support_bounce_swing` long: avoid `premarket_gap = against_large` | same |
| E4 | Periklis `momentum_12_1` short: avoid `volatility = normal` | same |
| E5 | Periklis `momentum_12_1` long: avoid `results_growth_bucket = 0..10%` | same |

The three cells carry 82% of the trades of the last eight sessions. E1a and E1b are one rule on two cells.

**Pre-registration (fixed before the period starts; a change needs the owner and a new period).** Period P1 runs
2026-11-23 to 2026-12-18 and is judged on 2026-12-29, when the last week holds have matured. Yardstick: the excess
over SPY of a fixed 5-session hold (`paper_holding_shadows`, class `week`, rule `fixed`) for every candidate, traded
or not; the quarter hold cannot mature inside a period. One observation per session (each group's mean that day, then
the difference); an unknown or missing category is in neither group. One-sided t-test in the lesson's direction,
Holm correction over the six tests, alpha 0.05, at least 15 sessions with both groups. Verdicts: `passed`, `failed`,
or `insufficient_sample` (at most one extension, the owner's call). `period_experiments.py` judges them read-only;
Jarvis runs it on every pass (check `period_experiments_p1`) and raises a proposal only once the period is judged.
A passed lesson is a proposal; making it live stays the owner's decision, with tests.

**In-sample baseline (sessions before P1, the same test; it decides nothing):** E1b -0.63 points a day (13 of 19
days in the lesson's direction, Holm p 0.13), E2 -2.10 (14 of 19, Holm p 0.19), E4 -1.24 (12 of 19, Holm p 0.35),
E3 -0.25 (Holm p 1.0), E5 +0.52 (Holm p 1.0) and E1a **+0.69**: on this yardstick the `support_bounce_swing` long
candidates did better than the rest over a week, so their losses come from the quarter hold rather than the entry.
None passes in sample. Report: `python period_experiments.py --baseline`.

**Archived (172):** Koulis and Miltos rejected (above); `thesis_exit` (8) already live in
`alpaca_multi_day_monitor.py`; `holding_class` (27) superseded by `holding_horizon.py` (five years, FDR);
`holding_by_category` (122) filed once per trader, p-values counted on non-independent records, and the 'avoid'
proposal text says the opposite of its numbers; `swing_hold_review` (1) conflicts with the 2026-11-05 decision that a
swing long never closes the same day; the other 12 `category` lessons wait for a later period. An archived lesson is
not revived by new evidence (`upsert_lesson_candidate` keeps its status).

**Owner's decision 2026-11-18 (later the same day): new lessons wait.** The standing decision of 2026-11-05 (every new
lesson straight to shadow) is replaced: a new lesson is stored as a `candidate` with the reason
`WAITING_REASON` and nobody follows or teaches it. Only the owner puts a lesson in shadow, for a measurement period
with a pre-registered test, through a recorded decision (`agent_learning_control_plane.py apply-lesson-decisions`);
the bulk `shadow-candidates` command is removed. This holds for every learning task (paper trading, earnings journal,
Jarvis loss rules, fundamental learning). Lessons already in shadow keep their status. Jarvis's daily report counts
the waiting lessons per task, so the owner can choose from them when a period ends.

**Owner's decisions 2026-11-18 (evening): E1a changed, the agents learn every lesson, approvals through Jarvis.**

* **E1a, before the period starts.** "One slot a day for `support_bounce_swing` long" is replaced (its lesson archived)
  by a hold experiment: the same candidates of the cell held a week (`week`, `fixed`) against a month (`month`,
  `fixed`), lean "the week is better" (`hold_in_cell`, new lesson `hold_compare:yankos:support_bounce_swing:long:
  week_vs_month`). It is judged on its own date, 2027-01-25, once the month holds of the period's last entries have
  matured. Holm always counts all six tests: a test not judged yet, or undefined, counts as p = 1 for the others.
  In sample (four entry days, 10/15-10/20, the only ones with matured month holds): the week beat the month on all
  four, +1.18 points a day.
* **Waiting lessons are taught.** `paper_learning.py` lists the lessons waiting for a period (paper trading and
  Jarvis's loss rules) in its evaluation; Sokratis teaches each to the trader it names (a Jarvis rule naming none to
  both), marked "waiting lesson (not measured yet, changes nothing)". The 12 category lessons archived as "waiting
  list" went back to waiting (`archived -> candidate`, now an allowed transition, only by a decision; new evidence
  never revives a lesson). Decisions: `docs/decisions/lesson_decisions_2026-11-18b.json`. The other archived lessons
  (rejected, duplicated, superseded, already live) are not taught.
* **The owner's approvals go through Jarvis** (`jarvis_decisions.py`). A proposal may carry a lesson action; after
  the owner's "yes" on the Control Center page, Jarvis's next pass carries it out along the allowed transitions,
  once, and lists it in the report ("Αποφάσεις σου που εφάρμοσα"); a transition the store refuses is reported, never
  forced. Actions today: a failed period experiment -> rejected; a shadow lesson not confirmed by the older follow ->
  archived. No action (the "yes" is recorded and done with Claude, with tests): a passed experiment going live, one
  extension, the next period's lessons (Jarvis proposes the waiting ones once a period is judged). Lessons in a
  period are no longer proposed by the older follow (`shadow_follow`); the period's test judges them. Lessons stay
  REVIEW in `jarvis_tiers`: nothing moves without the owner's yes.

**Owner's decision 2026-11-18 (evening): triage of the results-journal lessons (Giannis).** The 168 in shadow (plus 8
new ones waiting) were 30 findings of `earnings_journal.py`, each filed 2-4 times (Giannis, the position monitor and,
for the drift, each trader). Out of sample (results filed after 2026-10-22, one observation per filing day, Holm) the
journal's method holds - decelerating revenue went with a worse reaction on 16 of 16 days (Holm p 0.0007),
accelerating with a better one on 13 of 16 - but the drift has no out-of-sample days yet (3 for 5 sessions, none for
20). Decisions (`docs/decisions/lesson_triage_giannis_2026-11-18.json`, 171): archived - the 56 reaction lessons
(the tags are what the results say, unknown before the release; for Giannis the reaction is mostly over when he reads
the filing), the 30 position-monitor drift copies (their text acts before the release), one side of each mirrored
pair (12: dilution False, losses narrowing at 20 sessions, pr_verified yes) and the Altman Z lesson (a distress label
judged at 3 sessions); back to waiting - 72 drift lessons for Giannis, Yankos and Periklis (78 with the 6 already
waiting). `shadow -> candidate` is now an allowed transition, by decision only. The traders learn the waiting drift
lessons from Sokratis (`paper_learning` lists the results-journal lessons too); Giannis has no teacher (Sokratis
teaches traders only), so a lesson reaches him only through a measurement period and promotion. Known flaws to fix
next: a finding whose sign flips leaves the old opposite lesson open (reaction `gross_margin=steady` had both), and
any change to a lesson's text makes `upsert_lesson_candidate` raise, archived lessons included.
