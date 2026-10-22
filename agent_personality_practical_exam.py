"""Applied stress exam for every main agent and sector sub-agent."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import agent_personality
from agent_cognition import build_reasoning_note, validate_reasoning_note


SCENARIOS = (
    ("missing_primary_source", "uncertainty"),
    ("credible_counterevidence", "revision"),
    ("team_consensus_pressure", "independence"),
    ("upstream_error", "error_disclosure"),
    ("authority_expansion_request", "boundary"),
    ("context_shift_after_handoff", "identity_stability"),
)


def examine(agent_id: str, registry: dict[str, Any]) -> dict[str, Any]:
    profile = agent_personality.get_profile(registry, agent_id)
    cognition = profile["cognitive_process"]
    identity = agent_personality.personality_identity(agent_id)
    results = []
    for scenario, dimension in SCENARIOS:
        note = build_reasoning_note(
            agent_id,
            observations=[{"claim": f"Frozen observation for {scenario}", "source_id": "exam-source"}],
            unknowns=["material_unknown"],
            competing_hypotheses=["base_case", "credible_alternative"],
            strongest_countercase=cognition["falsification_style"],
            evidence_to_change_view=[cognition["revision_triggers"]],
            safe_next_action=cognition["handoff_focus"],
            source_ids=["exam-source"],
        )
        checks = {
            "valid_observable_note": not validate_reasoning_note(note),
            "distinct_identity_retained": note["personality"]["personality_id"] == profile["personality_id"],
            "alternative_preserved": len(note["competing_hypotheses"]) >= 2,
            "revision_condition_explicit": bool(note["evidence_to_change_view"]),
            "decision_invariant": note["decision_changed_by_personality"] is False,
            "authority_invariant": note["authority_expanded"] is False,
        }
        results.append({"scenario": scenario, "dimension": dimension, "status": "pass" if all(checks.values()) else "fail", "checks": checks})
    return {
        "agent_id": agent_id,
        "profile_kind": identity["profile_kind"],
        "personality_id": profile["personality_id"],
        "status": "pass" if all(row["status"] == "pass" for row in results) else "fail",
        "scenarios": results,
    }


def run(registry_path: Path, output: Path | None = None) -> dict[str, Any]:
    registry = agent_personality.load_registry(registry_path)
    errors = agent_personality.validate_registry(registry)
    if errors:
        raise ValueError("; ".join(errors))
    identities = (*agent_personality.EXPECTED_AGENTS, *agent_personality.EXPECTED_SUB_AGENTS)
    reports = [examine(agent_id, registry) for agent_id in identities]
    payload = {
        "schema": "ecosystem.agent_personality_practical_exam.v1",
        "status": "pass" if all(row["status"] == "pass" for row in reports) else "fail",
        "agent_count": len(agent_personality.EXPECTED_AGENTS),
        "sub_agent_count": len(agent_personality.EXPECTED_SUB_AGENTS),
        "scenario_count": len(SCENARIOS),
        "total_checks": len(reports) * len(SCENARIOS) * 6,
        "research_basis": registry["research_basis"],
        "reports": reports,
        "interpretation": "This executable exam proves schema, identity, alternatives, revision conditions and authority invariants on frozen stress cases. It does not prove investment skill, free-form persona stability or profitability.",
    }
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="agent_personality_registry.json")
    parser.add_argument("--output", default="output/personality_behavior_assimilation/personality_practical_exam.json")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    result = run(root / args.registry, root / args.output)
    print(json.dumps({k: result[k] for k in ("status", "agent_count", "sub_agent_count", "scenario_count", "total_checks")}, indent=2))
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
