# Rules review: technical analysis first, then the fundamental side

Owner's note, 2026-10-24 (after the AMSF short of 10/23): **the ecosystem's rules have to be fixed - first the
technical analysis, then the fundamental analysis.** This file keeps the list; each item is closed with evidence
(labs, the paper ledger, shadow records) and a focused test, and threshold changes only with the owner's approval.

## 1. Technical analysis (first)

| # | Finding | State |
|---|---|---|
| T1 | Miltos confirmed entries on the IEX feed alone (2-3% of the volume): AMSF's "break" of 2 cents on 413 shares did not exist on the consolidated tape. | **Done 2026-10-24**: completed SIP 15-minute bars (`entry_confirmation_rules.py`). |
| T2 | The confirmation was only "close beyond the previous bar in the trade's colour": no volume, no momentum, no look at the candle being entered (AMSF filled inside a green candle with the hour's largest volume, RSI(14) 15m at 51). | **Done 2026-10-24** with starting values: volume >= 1.0x the 20-bar average, RSI(14) 15m below/above 50, real-time price not reclaimed and no surge against the trade (IEX minutes). To measure on the ledger. |
| T3 | The volume average of the last 20 bars mixes the opening and closing spikes with midday: a midday break rarely reaches 1.0x. | Open: compare with the same time of day on previous sessions. |
| T4 | The daily strategies of the lab (rsi2, gap_fade, gap_continuation, momentum_12_1, high_52w_breakout, volatility_compression_breakout) are not in the playbook catalog: no trade plan (generic 3%/6% stop/target instead of ATR), no agent horizon, and their shorts skip the global SHORT contract (shortability, borrow carry, squeeze risk) that blocks every catalog short. | Waiting for the owner: adding them closes the gap but blocks their shorts until borrow and squeeze data exist. Draft ready. |
| T5 | rsi2_mean_reversion short has no stable edge at any horizon (horizon curve 2026-10-23); several daily strategies the same. | Open: decide whether they keep trading paper only to be measured. |
| T6 | Confirmation sees bars 16-31 minutes late (free SIP is 15 minutes delayed). | Open: real-time check covers the gap; a paid real-time feed is the alternative. |
| T7 | The session report shows only Miltos's last cycle ("entered 0" while AMSF entered). | Open (report only). |
| T8 | Intraday evidence for horizons shorter than a session: the horizon curve is built on daily bars. | Open: lab on stored 15-minute bars. |

## 2. Fundamental analysis (after)

| # | Finding | State |
|---|---|---|
| F1 | Negative forward P/E rule (LAES report, 2026-10-22). | Partly done (`valuation_gaps.py`: "failed" plus a gap that keeps being looked for); review the rule itself. |
| F2 | Foreign issuers: 6-K reading and coverage beyond the shortlist. | Partly done (`press_release_reader.py`); unanchored reads stay `pr_verified=no`. |
| F3 | Giannis's sensitivity to filing recency; bank revenue definition; empty sector layer (analysis 2026-09-29). | Open. |
| F4 | Fundamental thesis checks for held positions (risk High, Giannis down 1.0) are starting values from `thesis_exit.py`. | Open: measure on the first held positions. |
