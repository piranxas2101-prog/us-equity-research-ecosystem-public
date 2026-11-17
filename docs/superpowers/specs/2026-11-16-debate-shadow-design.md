# Debate shadow: a TradingAgents-style opinion on every approved trade - design

Date: 2026-11-16. Owner: the project owner. Status: approved by the owner 2026-11-16 ("Yes, all three": the launcher line, the debate may start Ollama, the pass test). Rule: docs/RULES_REVIEW.md section 12.

## 1. Intent

After reading TradingAgents (TauricResearch, Apache-2.0) the owner wants its way of judging a trade - a bull and a
bear argue, a judge rates - attached to the ecosystem, under the shadow-first discipline: **shadow, then possibly a
veto** (owner 2026-11-16). Decisions taken in the conversation:

- It judges **all trades Koulis approves** each session (150-200 a day; 13-30 become positions).
- It runs **during the session**, so that one day a veto could block an entry not yet made.
- It is a **compact debate**: one model call per trade that writes the bull case, the bear case and a rating, so
  that all approvals fit in a session on the owner's CPU.

Why: test whether a debate over the ecosystem's own evidence tells the approvals that will do badly from the rest -
the part of TradingAgents that might add something the deterministic gates do not.

## 2. Goals, non-goals, anti-goals

Goals
- An opinion (rating + one-line bull case + one-line bear case + the strongest risk) for every approved trade,
  recorded immutably with what it was given and when it was written.
- Measured against outcomes like every shadow lesson; a verdict reaches the owner through Jarvis.
- Know how many opinions came **before** the entry (how much a veto could have acted on).

Non-goals (this version)
- The veto itself. It is only proposed once the test passes, and turned on by the owner.
- TradingAgents' four analysts, multi-round debates, trader and risk debaters, data vendors, reflection memory.
- Intraday prices in the debate (the opinion reads the evidence as it stood at the approval).

Anti-goals: it fails, even if it works, if
- it changes any order, score, limit, approval, Miltos decision or position;
- it slows the live session: Miltos's or the monitors' cycles run late because of it;
- an opinion is treated as known when the model did not answer: no answer is `unknown`, never "hold";
- it reads the day's outcome (look-ahead) - inputs are frozen at the approval.

## 3. Constraints

- Ollama only, local; model `qwen2.5:3b` (the only one installed). Owner's CPU (i7-1355U, no GPU): ~28 prompt
  tokens/s, ~8 written tokens/s, Greek ~0.8 tokens per character - the prompt is English (fewer tokens) and short.
- The session launcher `start_trader_paper_trading_today.ps1` is in CLAUDE.md's Alpaca list: the one line that
  starts the debate needs the owner's explicit yes (asked with this design).
- CLAUDE.md lets only the Jarvis page start Ollama; the debate needs Ollama during the session, so it must be allowed
  to start the server too (asked with this design).
- No new dependency; open source only; imports no broker module (a test asserts it).

## 4. Approaches considered (owner chose 1)

1. **Compact debate, all approvals, highest-priority first** (chosen): one call per trade, ~60-90 s; 150-200 trades
   in ~3-4 h; Koulis's own order, so the likeliest entries are judged first.
2. Full three-step debate (bull, bear, judge), highest-priority first: richer, but only 50-70 trades a day; the
   rest "not judged"; the 30-case test takes longer.
3. Only the trades Miltos enters: fits easily, but the opinion always comes after the entry - never a veto.

## 5. The design

### 5.1 The debater (`debate_shadow.py run`)
- Started by the session launcher right after Koulis writes `output/koulis_order_intents_latest.json`, as a separate,
  hidden process; ends at 16:00 New York or when every approval is judged.
- Takes the approved intents in Koulis's order. For each: builds a fixed English prompt from the intent (agent,
  playbook, direction, signal score, the trader's `initial_rationale` and `invalidators`, the holding horizon), the
  company's packet lines (Giannis tier, Petros verdict, valuation gaps), last night's market reading, and any news
  headline already in `news_watch` for the ticker. Inputs are snapshotted and hashed.
- One `generate` with a JSON schema: `{"bull": str, "bear": str, "main_risk": str, "rating": "support" |
  "neutral" | "oppose", "confidence": 1-3}`. Malformed or empty -> `unknown` (never neutral).
- Writes one immutable row per (session, intent) to `data/debate_shadow.sqlite3`: inputs hash, prompt version,
  model, rating, texts, written_at, seconds, and whether the trade had already entered at that moment.

### 5.2 Not slowing the session
- `num_thread` 4 of 12; the Ollama runner process set to below-normal priority while the debate runs; one call at a
  time.
- Guard: every cycle it reads Miltos's `run_timestamp` (output/miltos_entry_stalker_latest.json) and the monitors'
  timestamps; if a cycle is late by more than 60 s beyond its poll interval twice in a row, the debate stops for the
  day and writes why. The session report and Jarvis show it.

### 5.3 Measuring (`debate_shadow.py evaluate`, nightly with the learning evaluation)
- Outcome of each judged approval (Claude's call): the ledger record of the same session, agent, ticker, playbook
  and direction. A trade: its result at the fill, mature only (owner's rule 2026-11-15); an approval never entered:
  its counterfactual open-to-close (`paper_counterfactuals`). Both as excess over the session's SPY open-to-close.
  The packet's own `long_case` / `short_case` / `fundamental_summary` / `kill_criteria` feed the bull and bear.
- Verdict (section 12, Claude's proposal to confirm): after 30 judged outcomes over 10 sessions, `oppose` must do
  worse than `support` by at least 0.5% of excess over SPY, with the 95% interval of the difference (bootstrap by
  day) clear of zero -> `veto_would_help`; else `no_clear_difference` / `insufficient_sample`.
- Also reported: share of opinions written before the entry, minutes per opinion, stops by the guard.
- Output `output/paper_learning/debate_shadow_latest.json|md`; a Jarvis check (`debate_shadow_s12`, ready only on
  `veto_would_help`) and a fact answer ("τι λέει η συζήτηση για ..."); a lesson candidate in shadow on a pass.

## 6. Decided by the owner
- Shadow, then possibly a veto; all approved trades; during the session; compact debate (2026-11-16).

## 7. Approved with this design (owner 2026-11-16)
- The one research-only line in `start_trader_paper_trading_today.ps1` that starts the debate after Koulis.
- That the debate may start the Ollama server (`ollama serve`, hidden, 127.0.0.1) when it is not running.
- The pass test of 5.3.

## 8. Left to the builder
- The exact prompt and packet lines, the JSON schema handling, the guard's thresholds within 5.2, the report layout.

## 9. Prompt v2 (2026-11-17, owner: "make the prompt so this is not a problem; search GitHub for ways")
- v1 (support/neutral/oppose in JSON) gave `neutral` to every trade; asked for odds of beating SPY the 3b model gave
  8-9 to every trade (LLM forecasts are known to be optimistic).
- From the LLM-as-a-judge literature (score first, anchored scale, decomposed judgement) and stock-ranking work
  (cross-sectional ranks rather than bare scores): v2 asks for a 1-9 score **compared with a typical approved
  trade** (5 = typical, anchors per band), score first and the bear case before the bull case; Ollama's token
  probabilities (`logprobs`, Ollama 0.33.2 here) turn the digit into a continuous expected score.
- The rating is the score's place among the previous sessions' scores (terciles of up to 600, from 60 on; fixed
  bounds 4.5 / 5.5 before), so it uses only the past and could act in real time.
- Trial on 8 real approvals of 11/16: scores 3.46-5.08, 3 oppose, 5 neutral, 8-15 s each. The bear text can mention
  things not in the evidence; the measurement judges the rating, not the prose.

## 10. Moved after the close (owner 2026-11-18)
The owner moved the debate out of the session (it must not weigh on it). Jarvis's after-session pass runs it with a
50-minute budget and no session guard; the launcher line is gone. Opinions are written after the entries, so the
"before entry" share is no longer a measure of a possible veto; a veto would need agents asking for an opinion on
demand (not built).

