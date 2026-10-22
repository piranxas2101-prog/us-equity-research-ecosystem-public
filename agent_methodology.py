from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA = "ecosystem.agent_methodology_registry.v1"
EXPECTED_AGENTS = (
    "babis", "giannis", "petros", "yankos", "periklis",
    "sokratis", "miltos", "koulis", "thymios",
)
REQUIRED_FIELDS = (
    "methodology_id", "lead_exemplar", "counterweight", "public_method_summary",
    "source_urls", "required_evidence", "decision_steps", "forbidden_inferences",
    "assimilation_checks",
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_registry(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"Unsupported methodology registry: {payload.get('schema')!r}")
    return payload


def validate_registry(registry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    agents = registry.get("agents") or {}
    missing = sorted(set(EXPECTED_AGENTS) - set(agents))
    extra = sorted(set(agents) - set(EXPECTED_AGENTS))
    if missing:
        errors.append(f"missing methodology agents: {', '.join(missing)}")
    if extra:
        errors.append(f"unexpected methodology agents: {', '.join(extra)}")
    if len(registry.get("shared_rules") or []) < 5:
        errors.append("shared methodology rules are incomplete")

    seen_ids: set[str] = set()
    for agent in EXPECTED_AGENTS:
        profile = agents.get(agent) or {}
        for field in REQUIRED_FIELDS:
            value = profile.get(field)
            if value is None or value == "" or value == []:
                errors.append(f"{agent}: missing methodology field {field}")
        methodology_id = str(profile.get("methodology_id") or "")
        if methodology_id in seen_ids:
            errors.append(f"{agent}: duplicate methodology_id {methodology_id}")
        seen_ids.add(methodology_id)
        if not methodology_id.startswith(f"{agent}."):
            errors.append(f"{agent}: methodology_id must begin with '{agent}.'")
        if len(profile.get("source_urls") or []) < 2:
            errors.append(f"{agent}: at least two public sources are required")
        if len(profile.get("decision_steps") or []) < 4:
            errors.append(f"{agent}: at least four decision steps are required")
        if len(profile.get("forbidden_inferences") or []) < 3:
            errors.append(f"{agent}: forbidden inferences are incomplete")
        if len(profile.get("assimilation_checks") or []) < 3:
            errors.append(f"{agent}: assimilation checks are incomplete")
    return errors


def get_profile(registry: dict[str, Any], agent: str) -> dict[str, Any]:
    key = agent.strip().lower()
    if key not in EXPECTED_AGENTS:
        raise ValueError(f"Unknown ecosystem agent: {agent!r}")
    profile = (registry.get("agents") or {}).get(key)
    if not isinstance(profile, dict):
        raise ValueError(f"Missing methodology for {key}")
    return profile


def methodology_identity(agent: str, registry_path: Path | None = None) -> dict[str, Any]:
    path = registry_path or (Path(__file__).resolve().parent / "agent_methodology_registry.json")
    registry = load_registry(path)
    errors = validate_registry(registry)
    if errors:
        raise ValueError("; ".join(errors))
    profile = get_profile(registry, agent)
    return {
        "registry_schema": registry["schema"],
        "registry_version": registry["version"],
        "registry_sha256": digest(registry),
        "methodology_id": profile["methodology_id"],
        "methodology_sha256": digest(profile),
        "lead_exemplar": profile["lead_exemplar"],
        "counterweight": profile["counterweight"],
        "authority_expanded": False,
    }


def render_prompt_appendix(agent: str, registry: dict[str, Any]) -> str:
    profile = get_profile(registry, agent)
    lines = [
        "",
        "====================",
        "VERSIONED METHODOLOGY ADAPTER",
        "====================",
        f"Agent: {agent.lower()}",
        f"Methodology ID: {profile['methodology_id']}",
        f"Methodology SHA-256: {digest(profile)}",
        f"Lead public exemplar: {profile['lead_exemplar']}",
        f"Counterweight: {profile['counterweight']}",
        "",
        "This adapter transfers only public, sourceable methods. Do not impersonate either person,",
        "invent quotations, or treat reputation as evidence. The ecosystem contract remains authoritative.",
        "",
        "PUBLIC METHOD SUMMARY",
        profile["public_method_summary"],
        "",
        "REQUIRED EVIDENCE",
        *[f"- {item}" for item in profile["required_evidence"]],
        "",
        "MANDATORY DECISION STEPS",
        *[f"{index}. {item}" for index, item in enumerate(profile["decision_steps"], 1)],
        "",
        "FORBIDDEN INFERENCES",
        *[f"- {item}" for item in profile["forbidden_inferences"]],
        "",
        "SELF-CHECK BEFORE OUTPUT",
        *[f"- {item}" for item in profile["assimilation_checks"]],
        "",
        "SOURCE URLS",
        *[f"- {url}" for url in profile["source_urls"]],
    ]
    return "\n".join(lines)


def attach_methodology(base_prompt: str, agent: str, registry_path: Path) -> str:
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
        expected = str(get_profile(registry, agent).get("methodology_id") or "")
        actual = str((agents.get(agent) or {}).get("methodology_id") or "")
        if actual != expected:
            errors.append(f"{agent}: contract methodology_id {actual!r} != registry {expected!r}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or render versioned agent methodologies.")
    parser.add_argument("command", choices=("verify", "render"))
    parser.add_argument("--registry", default="agent_methodology_registry.json")
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
            "schema": "ecosystem.agent_methodology_verification.v1",
            "status": "pass" if not errors else "fail",
            "registry_version": registry.get("version"),
            "registry_sha256": digest(registry),
            "agent_count": len(EXPECTED_AGENTS),
            "errors": errors,
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
