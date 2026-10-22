from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA = "ecosystem.agent_personality_registry.v1"
EXPECTED_AGENTS = (
    "babis", "giannis", "petros", "yankos", "periklis",
    "sokratis", "miltos", "koulis", "thymios",
)
EXPECTED_SUB_AGENTS = (
    "sector_communication_services", "sector_consumer_discretionary",
    "sector_consumer_staples", "sector_energy", "sector_financials",
    "sector_healthcare", "sector_industrials", "sector_materials",
    "sector_real_estate", "sector_technology", "sector_utilities",
    "sector_unresolved_classification",
)
REQUIRED_FIELDS = (
    "personality_id", "archetype", "core_traits", "communication_style",
    "disagreement_style", "collaboration_style", "uncertainty_behavior",
    "error_behavior", "signature_questions", "forbidden_behaviors",
    "behavioral_checks", "decision_influence_allowed", "authority_expanded",
)
COGNITIVE_FIELDS = (
    "observation_order", "hypothesis_style", "falsification_style",
    "ambiguity_behavior", "revision_triggers", "handoff_focus",
    "reflection_questions",
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_registry(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"Unsupported personality registry: {payload.get('schema')!r}")
    return payload


def validate_registry(registry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    agents = registry.get("agents") or {}
    missing = sorted(set(EXPECTED_AGENTS) - set(agents))
    extra = sorted(set(agents) - set(EXPECTED_AGENTS))
    if missing:
        errors.append(f"missing personality agents: {', '.join(missing)}")
    if extra:
        errors.append(f"unexpected personality agents: {', '.join(extra)}")
    if len(registry.get("shared_rules") or []) < 5:
        errors.append("shared personality rules are incomplete")

    research_basis = registry.get("research_basis") or []
    if len(research_basis) < 3 or any(not row.get("source") or not row.get("applied_as") for row in research_basis):
        errors.append("personality research basis is incomplete")

    seen_ids: set[str] = set()
    for agent in EXPECTED_AGENTS:
        profile = agents.get(agent) or {}
        for field in REQUIRED_FIELDS:
            if field not in profile or profile.get(field) in (None, "", []):
                if field not in {"decision_influence_allowed", "authority_expanded"}:
                    errors.append(f"{agent}: missing personality field {field}")
        personality_id = str(profile.get("personality_id") or "")
        if personality_id in seen_ids:
            errors.append(f"{agent}: duplicate personality_id {personality_id}")
        seen_ids.add(personality_id)
        if not personality_id.startswith(f"{agent}."):
            errors.append(f"{agent}: personality_id must begin with '{agent}.'")
        if len(profile.get("core_traits") or []) < 4:
            errors.append(f"{agent}: at least four core traits are required")
        if len(profile.get("signature_questions") or []) < 3:
            errors.append(f"{agent}: signature questions are incomplete")
        if len(profile.get("forbidden_behaviors") or []) < 4:
            errors.append(f"{agent}: forbidden behaviors are incomplete")
        if len(profile.get("behavioral_checks") or []) < 3:
            errors.append(f"{agent}: behavioral checks are incomplete")
        if profile.get("decision_influence_allowed") is not False:
            errors.append(f"{agent}: personality must not influence decisions")
        if profile.get("authority_expanded") is not False:
            errors.append(f"{agent}: personality must not expand authority")

        cognition = profile.get("cognitive_process") or {}
        for field in COGNITIVE_FIELDS:
            if cognition.get(field) in (None, "", []):
                errors.append(f"{agent}: missing cognitive_process.{field}")

    sub_agents = registry.get("sub_agents") or {}
    missing_sub = sorted(set(EXPECTED_SUB_AGENTS) - set(sub_agents))
    extra_sub = sorted(set(sub_agents) - set(EXPECTED_SUB_AGENTS))
    if missing_sub:
        errors.append(f"missing sub-agent personalities: {', '.join(missing_sub)}")
    if extra_sub:
        errors.append(f"unexpected sub-agent personalities: {', '.join(extra_sub)}")
    for sub_agent in EXPECTED_SUB_AGENTS:
        profile = sub_agents.get(sub_agent) or {}
        for field in ("personality_id", "parent_agent", "archetype", "specialist_traits", "communication_style", "signature_questions", "forbidden_behaviors", "cognitive_process", "decision_influence_allowed", "authority_expanded"):
            if field not in profile or profile.get(field) in (None, "", []):
                if field not in {"decision_influence_allowed", "authority_expanded"}:
                    errors.append(f"{sub_agent}: missing personality field {field}")
        if profile.get("parent_agent") not in EXPECTED_AGENTS:
            errors.append(f"{sub_agent}: unknown parent_agent")
        if not str(profile.get("personality_id") or "").startswith(f"{sub_agent}."):
            errors.append(f"{sub_agent}: personality_id must begin with '{sub_agent}.'")
        if profile.get("decision_influence_allowed") is not False or profile.get("authority_expanded") is not False:
            errors.append(f"{sub_agent}: personality cannot change decisions or authority")
        cognition = profile.get("cognitive_process") or {}
        for field in COGNITIVE_FIELDS:
            if cognition.get(field) in (None, "", []):
                errors.append(f"{sub_agent}: missing cognitive_process.{field}")
    return errors


def get_profile(registry: dict[str, Any], agent: str) -> dict[str, Any]:
    key = agent.strip().lower()
    if key in EXPECTED_SUB_AGENTS:
        profile = (registry.get("sub_agents") or {}).get(key)
        if not isinstance(profile, dict):
            raise ValueError(f"Missing personality for {key}")
        return profile
    if key not in EXPECTED_AGENTS:
        raise ValueError(f"Unknown ecosystem agent: {agent!r}")
    profile = (registry.get("agents") or {}).get(key)
    if not isinstance(profile, dict):
        raise ValueError(f"Missing personality for {key}")
    return profile


def personality_identity(agent: str, registry_path: Path | None = None) -> dict[str, Any]:
    path = registry_path or (Path(__file__).resolve().parent / "agent_personality_registry.json")
    registry = load_registry(path)
    errors = validate_registry(registry)
    if errors:
        raise ValueError("; ".join(errors))
    profile = get_profile(registry, agent)
    key = agent.strip().lower()
    return {
        "registry_schema": registry["schema"],
        "registry_version": registry["version"],
        "registry_sha256": digest(registry),
        "personality_id": profile["personality_id"],
        "personality_sha256": digest(profile),
        "archetype": profile["archetype"],
        "profile_kind": "sub_agent" if key in EXPECTED_SUB_AGENTS else "agent",
        "parent_agent": profile.get("parent_agent"),
        "decision_influence_allowed": False,
        "authority_expanded": False,
    }


def render_prompt_appendix(agent: str, registry: dict[str, Any]) -> str:
    profile = get_profile(registry, agent)
    cognition = profile["cognitive_process"]
    traits = profile.get("core_traits") or profile.get("specialist_traits") or []
    lines = [
        "",
        "====================",
        "VERSIONED PROFESSIONAL PERSONALITY",
        "====================",
        f"Agent: {agent.lower()}",
        f"Personality ID: {profile['personality_id']}",
        f"Personality SHA-256: {digest(profile)}",
        f"Professional archetype: {profile['archetype']}",
        "",
        "This is an original professional communication persona, not an imitation of a real person.",
        "It may affect voice, questions, collaboration and dissent only. It must never change facts,",
        "scores, methodology, authority, risk limits, state transitions or execution eligibility.",
        "",
        "CORE TRAITS",
        *[f"- {item}" for item in traits],
        "",
        "COMMUNICATION STYLE",
        profile["communication_style"],
        "",
        "DISAGREEMENT STYLE",
        profile.get("disagreement_style") or "Disagree through sourced counter-evidence and explicit alternatives.",
        "",
        "COLLABORATION STYLE",
        profile.get("collaboration_style") or f"Return a source-preserving specialist handoff to {profile.get('parent_agent')}.",
        "",
        "UNCERTAINTY AND ERROR BEHAVIOR",
        f"- Uncertainty: {profile.get('uncertainty_behavior') or cognition['ambiguity_behavior']}",
        f"- Errors: {profile.get('error_behavior') or 'Disclose the affected claim, source and downstream impact.'}",
        "",
        "OBSERVABLE REASONING PROTOCOL",
        f"- Observation order: {cognition['observation_order']}",
        f"- Hypothesis style: {cognition['hypothesis_style']}",
        f"- Falsification style: {cognition['falsification_style']}",
        f"- Ambiguity behavior: {cognition['ambiguity_behavior']}",
        f"- Revision triggers: {cognition['revision_triggers']}",
        f"- Handoff focus: {cognition['handoff_focus']}",
        "- Produce an observable decision note: observations, unknowns, competing hypotheses, strongest countercase, change-of-mind evidence and safe next action.",
        "",
        "SIGNATURE QUESTIONS",
        *[f"- {item}" for item in profile["signature_questions"]],
        "",
        "FORBIDDEN BEHAVIORS",
        *[f"- {item}" for item in profile["forbidden_behaviors"]],
        "",
        "BEHAVIORAL SELF-CHECK",
        *[f"- {item}" for item in (profile.get("behavioral_checks") or cognition["reflection_questions"])],
    ]
    return "\n".join(lines)


def attach_personality(base_prompt: str, agent: str, registry_path: Path) -> str:
    if not base_prompt.strip():
        return ""
    registry = load_registry(registry_path)
    errors = validate_registry(registry)
    if errors:
        raise ValueError("; ".join(errors))
    return base_prompt.rstrip() + "\n" + render_prompt_appendix(agent, registry)


def validate_contract_links(contract: dict[str, Any], registry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    agents = contract.get("agents") or {}
    for agent in EXPECTED_AGENTS:
        expected = str(get_profile(registry, agent).get("personality_id") or "")
        actual = str((agents.get(agent) or {}).get("personality_id") or "")
        if actual != expected:
            errors.append(f"{agent}: contract personality_id {actual!r} != registry {expected!r}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or render versioned professional personalities.")
    parser.add_argument("command", choices=("verify", "render"))
    parser.add_argument("--registry", default="agent_personality_registry.json")
    parser.add_argument("--contract", default="ecosystem_theory_contract.json")
    parser.add_argument("--agent", choices=EXPECTED_AGENTS)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    registry = load_registry((root / args.registry).resolve())
    errors = validate_registry(registry)
    if args.command == "verify":
        contract = json.loads((root / args.contract).read_text(encoding="utf-8-sig"))
        errors.extend(validate_contract_links(contract, registry))
        result = {
            "schema": "ecosystem.agent_personality_verification.v1",
            "status": "pass" if not errors else "fail",
            "registry_version": registry.get("version"),
            "registry_sha256": digest(registry),
            "agent_count": len(EXPECTED_AGENTS),
            "errors": errors,
            "decision_influence_allowed": False,
            "execution_authorized": False,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not errors else 2
    if not args.agent:
        parser.error("--agent is required with render")
    if errors:
        raise ValueError("; ".join(errors))
    print(render_prompt_appendix(args.agent, registry))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
