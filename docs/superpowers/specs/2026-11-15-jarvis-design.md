# Jarvis: the ecosystem's own operator - design

Date: 2026-11-15. Owner: the project owner. Status: approved by the owner 2026-11-15 ("we will change it as we use it"); built 2026-11-15 (plan: `docs/superpowers/plans/2026-11-15-jarvis.md`).

## 1. Intent

The owner keeps two working conversations with Claude about the ecosystem: one on the technical rules, one on the
fundamental rules (`docs/RULES_REVIEW.md`). Both follow the same loop: find a rule problem, measure it with a lab,
put the finding in shadow, re-judge it after N sessions, bring the owner a proposal, and the owner decides. Today the
loop only moves when the owner asks Claude.

The owner wants a "Jarvis": an agent **inside the ecosystem**, running on his machine without Claude, that:

- runs and watches the ecosystem by itself, and works out what went wrong when something fails;
- does the recurring measuring and judging work (steps 2-4 of the loop);
- learns from the ecosystem's mistakes with a machine-learning model;
- gives him one **daily report**, so he is informed and does not do the work;
- lets him talk to it in plain language, the way he talks to Claude.

Claude builds it and checks on it only when the owner asks. It will be changed as it gets used (owner, 2026-11-15).

## 2. Goals, non-goals, anti-goals

Goals
- One daily report, written without anyone asking, with: what the nightly and the session did, what went wrong and
  why, what Jarvis measured or learned, and the proposals waiting for a yes/no.
- A calendar of due checks (re-judge T3 at ~20 sessions, the T6 IEX shadow, F4, the flip shadow verdict of section 8,
  the 11/05 shock longs five sessions later, ...) that Jarvis runs when they come due, instead of relying on memory.
- A learning model that finds conditions under which trades go wrong and turns them into proposals with evidence.
- A "Jarvis" page in the Control Center: chat, the report, the proposals with yes/no.

Non-goals (first version)
- Slack. The daily report goes to Slack later (owner's option C); the chat stays in the Control Center.
- Voice, phone access, remote access.
- Applying an approved proposal. A yes is recorded; the change itself (code + tests) is made with Claude.

Anti-goals: it fails even if it works, if
- it changes any active score, policy, threshold, risk limit, playbook or order by itself (manual promotion, owner
  2026-11-05; agent thresholds only with a documented rationale and tests);
- it starts a long run, a trading session, or anything that talks to Alpaca;
- the chat model invents a number, a ticker or a result, or decides anything;
- it hides an unknown: a missing input is reported as unknown, never as zero or "fine";
- it wakes the computer more often than today, or keeps it awake more than 10 minutes longer per pass.

## 3. Constraints (from CLAUDE.md and the owner's decisions)

- LLMs only locally through Ollama, never hosted, never deciding, and Jarvis never starts the Ollama app.
  Machine: Intel Iris Xe (no discrete GPU), 16 GB RAM; today's model `qwen2.5:3b`.
- Libraries open source, embeddable, local; prefer what is in `requirements-windows-py312.lock` (scikit-learn 1.9.1,
  numpy, pandas, scipy are there). No new dependency without a license check.
- Learning: candidate -> shadow -> champion, manual promotion; new lessons go straight to shadow.
- Research packets and run manifests are immutable; Jarvis only reads them.
- Control Center security: 127.0.0.1 only, Host check, session token + Origin + JSON on actions, CSP (no inline
  styles), every action logged to `data/control_center/actions.jsonl`; no Alpaca, trader or shell endpoints.
- PowerShell launchers call native programs only through `Invoke-Logged`.
- Runtime state (`data/`, `output/`, `logs/`) is git-ignored.

## 4. Approaches considered

**How the owner talks to it** (owner chose A, 2026-11-15; C later)
- A. Control Center page only: chat + report + proposals, a Windows notification when the report is ready.
  Everything stays on the machine, builds on what exists; not reachable from the phone.
- B. Slack only (the gateway `slack_agent_gateway.py` exists): reachable from the phone, but answers only while the
  computer is awake (it hibernates when idle) and needs a daemon running all the time.
- C. Both with separate roles: the report to Slack, the chat in the Control Center. The target after A.

**What happens when Jarvis learns something** (owner chose a)
- a. It prepares the proposal with evidence and puts it in the report for the owner's yes/no. Keeps manual promotion.
- b. It applies it itself within limits the owner sets. Rejected: it breaks manual promotion.

**When it runs** (Claude's proposal, owner agreed)
- Chosen: as the last step of the two scheduled tasks that already exist (the nightly, the afternoon session), so it
  never wakes the computer by itself.
- Rejected: its own scheduled task (a third wake-up a day), or a daemon (keeps the computer awake).

## 5. The design

Three parts, plus the page. Name: `jarvis_*.py` modules at the root, like the other agents.

### 5.1 The core (`jarvis.py`, deterministic Python, no LLM)

Two entry points, called at the end of the existing tasks:

- `jarvis.py after-nightly` - after the nightly's work has really ended: inside `hibernate_after_run.ps1`, after the
  run (or its fallback) finished and before `hibernate_if_idle.ps1`; and on the branch of `nightly_research.ps1`
  where the run could not start, after the fallback and before `hibernate_if_idle.ps1`. (`nightly_research.ps1`
  only launches the run in the background, so a step at its end would see a run still going.)
- `jarvis.py after-session` - on all three endings of `start_trader_afternoon_session.ps1`, before each
  `Invoke-Rest`: the normal path (after the ledger and `paper_learning.py evaluate`), "no paper orders today", and
  the error `catch`, so the day of an error is reported too. **This launcher is in the Alpaca list of CLAUDE.md:
  these research-only lines need the owner's explicit approval (asked with this design).** Jarvis imports no broker
  module; a test asserts it. Fallback if not approved: the next nightly pass writes the previous day's report.
- Days without a session (weekends, US holidays): the nightly pass writes the day's report.
- Time budget (Claude's call): a pass may keep the computer awake up to 10 extra minutes; checks over the budget
  wait for the next pass. Long labs (e.g. `exit_lab.py --horizon-curve`, ~5 min) run once a week, on the Saturday
  nightly pass.

Each pass:
1. **Health**: reads what the run left (run state/manifests, `run_performance_history.csv`, the session report, the
   ledger and evaluation outputs, the launchers' logs) and writes one status per step: ok / failed / missing /
   unknown, with the reason when a log or exit code shows one. Failures are diagnosed by fixed rules (rate limit,
   missing input, traceback, step not reached) - the same patterns we diagnose by hand today.
2. **Due checks**: a registry `jarvis_checks.json` (committed) lists each recurring check: id (e.g. `T3`, `F4`,
   `flip_shadow_s8`), the RULES_REVIEW item it belongs to, the command (read-only labs and reports only, from an
   allow-list in code), the due condition (sessions since a date, a calendar date, a minimum sample), and how to read
   its verdict from the output file. Jarvis runs the due ones within the time budget. Version 1 uses only
   producers that already exist (`flip_shadow.py`, `exit_lab.py`, `paper_learning` evaluation and shadow follow,
   the gap/news shadows, ...). A check with no producer yet (e.g. the T3 volume-basis re-judge, F4, the 11/05 shock
   longs) is listed with state `needs_lab`: the report says it is due and has no measurement; each new lab is built
   with Claude and the owner, with its own method, and then added to the registry (Claude's call).
3. **Learning** (5.2).
4. **Proposals**: each verdict or learned finding that needs the owner becomes a proposal record (id, source,
   evidence with file paths and numbers, what would change, the RULES_REVIEW item) in `data/jarvis/proposals.sqlite3`.
5. **Report**: `output/jarvis/report_<date>.md` + `.json` and `report_latest.*`, written after the session pass
   (the nightly pass writes its part to state, the session pass writes the day's report). A Windows notification
   says it is ready.

Jarvis never starts a research run, resumes one, or touches trading. When a run failed, the report says why and
proposes "resume run <id>"; the owner starts it (Control Center already has a governed research-only start).

### 5.2 The learning model (`jarvis_learning.py`)

- Data: the paper trade ledger (trades with their context: trader, strategy, direction, holding class, market state,
  signal score, pre-open check, results at the real fill) and its shadow records.
- Model: an interpretable classical model from scikit-learn (shallow decision tree / logistic regression), trained
  to tell losing trades from winning ones, validated out of sample by time (train on older sessions, test on newer).
- Output: a rule only when it holds out of sample with a minimum sample, stated plainly ("short, strategy X, market
  state Y: 9 of 12 lost"). Each such rule goes into the existing learning pipeline as a **shadow lesson** (the same
  path `paper_learning` uses), so it is followed out of sample and taught by Sokratis, and appears as a proposal.
  It never changes active scores, policies, limits or orders.
- It complements `paper_learning.py` (group statistics), it does not replace it.
- Sokratis teaches these lessons too (owner 2026-11-16): `sokratis_strategy_teacher.build_paper_trade_lesson` reads
  the follow from `output/jarvis/report_latest.json`; a rule naming a trader goes to him, one naming none to both.
  Advisory only, like every Sokratis lesson.

### 5.3 The chat (`jarvis_chat.py`, local Ollama)

- Answers questions from the owner using only Jarvis's own files and the ecosystem's reports: the relevant files are
  selected by fixed rules (date, ticker, agent, check id), their text is given to the model, and the answer cites the
  file it comes from.
- Numbers in an answer must appear in the cited source; the page shows the source next to the answer.
- If Ollama is not running, the page says so; Jarvis does not start it. Model and context size in
  `jarvis_config.json`.
- It can only read. It has no tools to run anything.
- What it may read is the knowledge map `jarvis_knowledge.json` (owner 2026-11-16: Jarvis is told about everything
  in its work): per topic (session, learning, market, news, held positions, traders, Sokratis, fundamentals, results,
  labs, RULES_REVIEW, ...) its files and keywords, matched without accents. Only files under `output/` or `docs/`;
  at most three sources, sharing `max_total_chars` so the prompt fits `num_ctx`.
- The daily report also carries "Η εικόνα του οικοσυστήματος": the market reading (`market_state_latest.json`) and
  the paper-learning lessons in shadow by verdict, with the three closest to a verdict.

### 5.4 The Control Center page "Jarvis"

- Report: the latest daily report, with the previous ones by date.
- Proposals: open ones with evidence; Yes / No / Later buttons. A decision goes through the existing action path
  (token, Origin, JSON, `actions.jsonl`) into `proposals.sqlite3` only; the button never writes to the learning
  registry (`agent_lesson_candidates`, `agent_model_registry`). Claude's calls:
  - Yes: recorded as approved; the promotion or code change is made with Claude through the existing paths.
  - No: recorded as declined; the shadow lesson keeps being followed (shadow changes nothing), Jarvis does not
    raise the same proposal again unless its evidence changes materially.
  - Later: asked again after 5 sessions, with updated evidence.
  - Proposals come from Jarvis's own findings and also from `paper_learning`'s shadow lessons once their
    out-of-sample follow reaches a verdict, so the owner sees every lesson ready for judgment in one place
    (one proposal per lesson id, no duplicates).
- Chat: a box and the conversation, kept in `data/jarvis/chat.sqlite3`.
- Same black/blue/red look as the other pages. The open Control Center meeting (owner 2026-11-12, after both
  conversations end, date set by the owner) can reshape it.

## 6. Decided by the owner

- Jarvis is an agent inside the ecosystem, runs without Claude (2026-11-15).
- Learned things become proposals for yes/no; nothing changes by itself (option a).
- Control Center first (A), Slack later (C).
- One daily report after the afternoon session; Jarvis never starts long runs or trading; Yes records, applying is
  done with Claude; name "Jarvis"; it runs at the end of the existing tasks (Claude's proposals, owner agreed).
- The research-only `jarvis.py after-session` lines on the three endings of `start_trader_afternoon_session.ps1`:
  explicitly approved by the owner, 2026-11-15 ("Yes, allowed"). Nothing else in that launcher changes.

## 7. Left to the builder

- The exact health rules per step and the first contents of `jarvis_checks.json` (from the open RULES_REVIEW items).
- The learning model's features, minimum sample and validation split (documented with the first run's numbers).
- How the chat picks files, and the prompt.
- The page layout within the existing style.
