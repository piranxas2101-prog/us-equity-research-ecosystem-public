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
| F1 | Negative forward P/E rule (LAES report, 2026-10-22). | Partly done (`valuation_gaps.py`: "failed" plus a gap that keeps being looked for); review the rule itself. |
| F2 | Foreign issuers: 6-K reading and coverage beyond the shortlist. | Partly done (`press_release_reader.py`); unanchored reads stay `pr_verified=no`. |
| F3 | Giannis's sensitivity to filing recency; bank revenue definition; empty sector layer (analysis 2026-09-29). | Open. |
| F5 | Held positions are not protected from results gaps: the pre-open check judges new entries only, so GOOGL (10/29) gapped 10.5% through its stop on earnings. | Open (owner 2026-11-03: belongs here, with the missing catalyst dates; same data gap as the `catalyst_timing` short gate). |
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

