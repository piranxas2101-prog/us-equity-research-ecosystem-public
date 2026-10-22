# Personality and observable reasoning system

The ecosystem has nine main professional personalities and twelve sector sub-agent personalities. A personality controls question order, skepticism, communication, revision behavior and handoff style. It cannot change a fact, formula, score, risk limit, state transition, role authority or execution eligibility.

## Applied design

Each profile defines an observation order, hypothesis style, falsification method, ambiguity behavior, revision triggers, handoff focus and reflection questions. Sector specialists inherit a parent research discipline while keeping a distinct sector lens. Their output packet carries the exact versioned personality identity and the invariant that personality cannot alter decisions or authority.

The system stores an **observable reasoning note**, not private chain-of-thought. It contains sourced observations, unknowns, competing hypotheses, the strongest countercase, evidence that would change the view and the safe next action. This makes work reviewable without storing unverifiable internal narration.

## Research translated into code

- The CIA Structured Analytic Techniques primer supplies competing hypotheses, explicit disconfirmation, key assumptions and devil's advocacy.
- NIST AI RMF guidance supplies realistic test conditions, documented feedback, role boundaries and repeatable measurement.
- Published persona-interaction studies motivate context-shift tests because a prompt alone does not establish behavioral consistency.

The exact source URLs and their code application live in `agent_personality_registry.json` under `research_basis`.

## Practical verification

`agent_personality_practical_exam.py` runs six frozen stress situations for all 21 identities: missing primary evidence, credible counterevidence, consensus pressure, upstream error, authority pressure and context shift. It verifies identity, alternatives, revision conditions, sourced notes and decision/authority invariants. This proves the mechanism on controlled cases; it does not claim investment skill, profitability or unconstrained free-form consistency.

Run:

```powershell
.\run_py.cmd agent_personality.py verify
.\run_py.cmd agent_personality_practical_exam.py
.\run_py.cmd -m unittest tests.test_agent_cognition tests.test_agent_personality
```
