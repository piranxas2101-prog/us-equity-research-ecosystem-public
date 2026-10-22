# Local agent learning architecture

The ecosystem learns from immutable decisions and delayed evidence. It does not
allow an agent to rewrite its original decision, modify active scoring rules or
promote its own model.

## Fundamental evaluation boundary

`fundamental_learning_evaluator.py` independently grades evidence completeness
for Babis, Giannis and Petros. The evaluator is deterministic, has no broker or
LLM access and records only:

- a rubric verdict (`pass`, `partial`, `fail`),
- the criteria and evidence used,
- a versioned memory record,
- candidate lessons when the same gap has enough support.

Candidate lessons do not change active decisions. They require separate shadow
validation and manual promotion.

## Sokratis boundary

Sokratis remains the teacher of the trader agents. His inputs, lesson workflow
and promotion path are unchanged. The fundamental evaluator does not read or
write Sokratis episodes and cannot modify trader playbooks.

## Persistence and safety

Learning state is stored in the local SQLite control plane:

- `agent_learning_episodes`: immutable decision-time records;
- `agent_episode_evaluations`: immutable rubric evaluations;
- `agent_memory_items` and `agent_memory_versions`: current memory plus complete
  version history;
- `agent_learning_outcomes`: delayed, evidence-backed labels;
- `agent_lesson_candidates`: proposals that remain candidates by default;
- `agent_model_registry`: JSON-only model artifacts and manual state changes.

Memory must contain provenance, never credentials. Model promotion follows
`candidate -> shadow -> champion`; the control plane forbids direct candidate
promotion and never performs promotion automatically.

## Design sources

The design adapts the following public Anthropic patterns to a local,
provider-independent implementation:

- persistent memory written by a tool and loaded as context;
- immutable memory versions and rollback history;
- a fresh-context evaluator that cannot edit the builder's work;
- default-fail, evidence-backed completion criteria;
- outcome/rubric loops with bounded revisions.

No Anthropic service or API is required by this implementation.
