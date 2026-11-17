# Debate Shadow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A compact local-LLM debate (bull, bear, rating) on every Koulis approval during the session, in shadow,
measured nightly against outcomes; never touching orders.

**Architecture:** One module `debate_shadow.py` with `run` (started in the background by the session launcher after
Koulis) and `evaluate` (run by Jarvis as an allow-listed producer after the ledger). Opinions in
`data/debate_shadow.sqlite3` (immutable rows); verdict in `output/paper_learning/debate_shadow_latest.json|md`.

**Tech Stack:** Python 3.12 stdlib, urllib to local Ollama (`/api/generate` with a JSON schema), sqlite3.

**Spec:** `docs/superpowers/specs/2026-11-16-debate-shadow-design.md`

## Global Constraints
- Imports no broker module (`alpaca*`, `paper_trade_ledger`, `koulis_risk_gate`, `thymios_order_intent`); reads the
  ledger with sqlite3; reads Koulis's file, never writes it.
- Model `qwen2.5:3b`, `num_thread` 4, `temperature` 0; prompt in English; `PROMPT_VERSION = "debate.v1"`.
- Ratings: `support | neutral | oppose`; anything else or no answer -> `unknown` (never neutral).
- Stops at 16:00 New York; guard: Miltos's `run_timestamp` older than poll (300 s) + 60 s on two checks in a row ->
  stop for the day, reason written.
- Pass test (owner-approved): >= 30 outcomes over >= 10 sessions; mean excess(oppose) <= mean excess(support) - 0.5,
  and the 95% day-bootstrap interval of (support - oppose) above zero -> `veto_would_help`; else
  `no_clear_difference`; below the sample -> `insufficient_sample`.
- Outcomes: ledger record of the same session/agent/ticker/playbook/direction; `trade` -> result_pct (mature only,
  `jarvis_learning.mature`), `approved_not_entered` -> `paper_counterfactuals.open_close_pct`; excess = minus the
  session's `spy_open_close_pct`.

## Review Focus
1. Ollama down / model missing mid-run: the row is `unknown` with the reason, the run goes on or ends cleanly.
2. Koulis file of another day: nothing is debated.
3. A rerun the same day: already-judged intents are skipped (no duplicates).
4. The guard stops the run when Miltos is late; a missing Miltos file does not stop it before Miltos starts.
5. A Jarvis or debate failure never affects the session launcher (background process, exit code ignored).

### Task 1: Debater core (`debate_shadow.py`): inputs, prompt, parse, store, run loop
Tests `tests/test_debate_shadow.py`: inputs carry the packet's long/short case, summary, kill criteria and the
trader's rationale/invalidators; `parse_opinion` maps bad JSON / bad rating to unknown; `run` judges in Koulis order,
skips judged, ignores another day's file, stops at the deadline, writes `unknown` when the client raises.

### Task 2: Guard and Ollama handling
Tests: two late checks stop; one late check does not; no Miltos file -> not late; Ollama started once when down via
`jarvis_chat.start_ollama_server`; Ollama runner priority lowered best-effort (no error when nothing to lower).

### Task 3: Evaluate
Tests: join by key, maturity, counterfactual for non-entered, excess vs SPY, entered-before share, the three
verdicts at the thresholds, report written; no ledger -> insufficient_sample with unknowns.

### Task 4: Wiring
- `start_trader_paper_trading_today.ps1`: after Koulis succeeds, `Start-Process` hidden `debate_shadow.py run` with
  logs (owner-approved line); test that it is a background start after the Koulis step and never `-Wait`.
- `jarvis_checks.PRODUCERS["debate_shadow_evaluate"]`, registry entry `debate_shadow_s12` (every pass, ready on
  `veto_would_help`), knowledge map topic, CI py_compile, docs (CLAUDE.md command, RULES_REVIEW section 12 status).

### Task 5: Real-data trial, full suite, invariants, push, publish.
