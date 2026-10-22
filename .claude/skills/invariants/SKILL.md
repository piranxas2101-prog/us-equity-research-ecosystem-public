---
name: invariants
description: Pre-commit guard for this repo. Run before every commit/push or any change to an agent, risk gate, threshold or launcher. Checks the diff against the CLAUDE.md invariants (no Alpaca/execution changes, no long-run launchers, no hosted LLMs, unknown stays unknown, no secrets or runtime state) and reports STOP / WARN / REVIEW.
---

# Invariants guard

Run from the repository root, with the locked runtime on the owner's machine
(`.\.venv-next\Scripts\python.exe`) or any Python 3 elsewhere — it uses only the standard library
and never touches the network, Alpaca or the pipeline:

```
python .claude/skills/invariants/check_invariants.py            # working tree vs HEAD (+ untracked)
python .claude/skills/invariants/check_invariants.py --base origin/main
```

## What to do with the result

- **STOP** (exit code 1): protected execution/risk files, long-run launchers called from code, hosted
  LLM providers, Ollama app start, `.env` or generated runtime state in the change.
  Do not commit or push. Tell the owner (in Greek) what was found and ask.
- **WARN**: silent zero defaults, threshold/gate edits, personality touching scoring terms,
  mutation near frozen artifacts, direct `.env` reads, code without tests. Investigate each one.
  Fix it, or state in the commit message why it is fine. Threshold/gate/promotion changes need a documented
  rationale and a focused test (CLAUDE.md).
- **REVIEW**: always printed. Judgment items a script cannot prove; answer each against the diff
  before committing.

The patterns are heuristics: expect false positives and misses. A clean run is not proof; it only
catches the obvious violations early. It never replaces the unit tests or reading the diff.
