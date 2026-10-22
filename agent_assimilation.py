from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import agent_methodology
import agent_personality


EXPECTED_AGENTS = (
    "babis", "giannis", "petros", "yankos", "periklis",
    "sokratis", "miltos", "koulis", "thymios",
)
REQUIRED_PROFILE_FIELDS = (
    "team", "module", "methodology_id", "personality_id", "mission", "inputs", "processing_logic", "outputs",
    "architecture_role", "authority", "boundaries", "missing_conflicting_data",
    "handoffs", "failure_modes",
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if payload.get("schema") != "ecosystem.theory_contract.v1":
        raise ValueError(f"Unsupported theory contract: {payload.get('schema')!r}")
    return payload


def validate_contract(contract: dict[str, Any], project_root: Path) -> list[str]:
    errors: list[str] = []
    agents = contract.get("agents") or {}
    missing_agents = sorted(set(EXPECTED_AGENTS) - set(agents))
    extra_agents = sorted(set(agents) - set(EXPECTED_AGENTS))
    if missing_agents:
        errors.append(f"missing agents: {', '.join(missing_agents)}")
    if extra_agents:
        errors.append(f"unexpected agents: {', '.join(extra_agents)}")
    if len(contract.get("shared_principles") or []) < 6:
        errors.append("shared principles are incomplete")
    for name in EXPECTED_AGENTS:
        profile = agents.get(name) or {}
        for field in REQUIRED_PROFILE_FIELDS:
            value = profile.get(field)
            if value is None or value == "" or value == []:
                errors.append(f"{name}: missing {field}")
        module = str(profile.get("module") or "")
        if module and not (project_root / module).exists():
            errors.append(f"{name}: declared module does not exist: {module}")
        logic = profile.get("processing_logic") or []
        if len(logic) < 4:
            errors.append(f"{name}: processing_logic needs at least four explicit steps")
    methodology_path = project_root / "agent_methodology_registry.json"
    if not methodology_path.exists():
        errors.append("agent methodology registry is missing")
    else:
        registry = agent_methodology.load_registry(methodology_path)
        errors.extend(agent_methodology.validate_registry(registry))
        errors.extend(agent_methodology.validate_contract_links(contract, registry))
    personality_path = project_root / "agent_personality_registry.json"
    if not personality_path.exists():
        errors.append("agent personality registry is missing")
    else:
        personality_registry = agent_personality.load_registry(personality_path)
        errors.extend(agent_personality.validate_registry(personality_registry))
        errors.extend(agent_personality.validate_contract_links(contract, personality_registry))
    return errors


def bullets(items: list[Any]) -> list[str]:
    return [f"- {item}" for item in items]


def render_report(agent: str, contract: dict[str, Any]) -> str:
    profile = contract["agents"][agent]
    registry = agent_methodology.load_registry(Path(__file__).resolve().parent / "agent_methodology_registry.json")
    methodology = agent_methodology.get_profile(registry, agent)
    personality_registry = agent_personality.load_registry(
        Path(__file__).resolve().parent / "agent_personality_registry.json"
    )
    personality = agent_personality.get_profile(personality_registry, agent)
    contract_digest = digest(contract)
    profile_digest = digest(profile)
    methodology_digest = agent_methodology.digest(methodology)
    personality_digest = agent_personality.digest(personality)
    lines = [
        f"# {agent.title()} — Θεωρητική Αναφορά Αφομοίωσης",
        "",
        f"- Agent ID: `{agent}`",
        f"- Team: `{profile['team']}`",
        f"- Owned module: `{profile['module']}`",
        f"- Theory contract version: `{contract['version']}`",
        f"- Contract SHA-256: `{contract_digest}`",
        f"- Profile SHA-256: `{profile_digest}`",
        f"- Methodology ID: `{methodology['methodology_id']}`",
        f"- Methodology SHA-256: `{methodology_digest}`",
        f"- Personality ID: `{personality['personality_id']}`",
        f"- Personality SHA-256: `{personality_digest}`",
        "- Report type: declarative theoretical self-report; no market refresh, outcome evaluation, or order action.",
        "",
        "## Κατανόηση του οικοσυστήματος",
        "",
        contract["purpose"],
        "",
        "Αποδέχομαι τις κοινές αρχές:",
        "",
        *bullets(contract["shared_principles"]),
        "",
        "## Δημόσια μεθοδολογία που εφαρμόζω",
        "",
        f"Lead exemplar: **{methodology['lead_exemplar']}**",
        "",
        f"Counterweight: **{methodology['counterweight']}**",
        "",
        methodology["public_method_summary"],
        "",
        "Υποχρεωτικά βήματα μεθοδολογίας:",
        "",
        *[f"{index}. {step}" for index, step in enumerate(methodology["decision_steps"], 1)],
        "",
        "Απαγορευμένες υποθέσεις:",
        "",
        *bullets(methodology["forbidden_inferences"]),
        "",
        "## Επαγγελματική προσωπικότητα",
        "",
        f"Archetype: **{personality['archetype']}**",
        "",
        f"Communication: {personality['communication_style']}",
        "",
        f"Disagreement: {personality['disagreement_style']}",
        "",
        f"Collaboration: {personality['collaboration_style']}",
        "",
        "Η προσωπικότητα αυτή δεν αλλάζει δεδομένα, scores, methodology, authority ή execution gates.",
        "",
        "## Η αποστολή μου",
        "",
        profile["mission"],
        "",
        "## Δεδομένα που παραλαμβάνω",
        "",
        *bullets(profile["inputs"]),
        "",
        "## Λογική επεξεργασίας δεδομένων",
        "",
    ]
    lines.extend(f"{index}. {step}" for index, step in enumerate(profile["processing_logic"], start=1))
    lines.extend([
        "",
        "## Δεδομένα και artifacts που παράγω",
        "",
        *bullets(profile["outputs"]),
        "",
        "## Θέση μου στην αρχιτεκτονική",
        "",
        profile["architecture_role"],
        "",
        "## Αποφάσεις που μου ανήκουν",
        "",
        *bullets(profile["authority"]),
        "",
        "## Όρια αρμοδιότητας",
        "",
        *bullets(profile["boundaries"]),
        "",
        "## Ελλιπή ή αντικρουόμενα δεδομένα",
        "",
        *bullets(profile["missing_conflicting_data"]),
        "",
        "## Παραδόσεις προς άλλους agents",
        "",
        *bullets(profile["handoffs"]),
        "",
        "## Κύριοι κίνδυνοι λογικής",
        "",
        *bullets(profile["failure_modes"]),
        "",
        "## Δήλωση αφομοίωσης",
        "",
        "Κατανοώ ότι ο ρόλος μου είναι ένας περιορισμένος μετασχηματισμός μέσα σε κοινή αλυσίδα πληροφορίας. Διατηρώ provenance, run identity, unknown states και τα όρια εξουσίας μου. Η αναφορά αυτή δεν ενεργοποιεί daily run ή trading λειτουργία.",
        "",
    ])
    return "\n".join(lines)


def build_reports(contract_path: Path, reports_dir: Path, project_root: Path) -> dict[str, Any]:
    contract = load_contract(contract_path)
    errors = validate_contract(contract, project_root)
    if errors:
        raise ValueError("; ".join(errors))
    reports_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for agent in EXPECTED_AGENTS:
        path = reports_dir / f"{agent}_theory_report.md"
        path.write_text(render_report(agent, contract), encoding="utf-8")
        written.append(str(path))
    return {
        "schema": "ecosystem.agent_assimilation_status.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "contract_version": contract["version"],
        "contract_sha256": digest(contract),
        "status": "reports_built",
        "agent_count": len(written),
        "reports": written,
        "scope": "theoretical_only",
    }


def verify_reports(contract_path: Path, reports_dir: Path, project_root: Path) -> dict[str, Any]:
    contract = load_contract(contract_path)
    errors = validate_contract(contract, project_root)
    registry = agent_methodology.load_registry(project_root / "agent_methodology_registry.json")
    personality_registry = agent_personality.load_registry(project_root / "agent_personality_registry.json")
    contract_digest = digest(contract)
    for agent in EXPECTED_AGENTS:
        profile = contract.get("agents", {}).get(agent, {})
        path = reports_dir / f"{agent}_theory_report.md"
        if not path.exists():
            errors.append(f"{agent}: report missing")
            continue
        text = path.read_text(encoding="utf-8-sig")
        required_markers = [
            f"Agent ID: `{agent}`",
            f"Contract SHA-256: `{contract_digest}`",
            f"Profile SHA-256: `{digest(profile)}`",
            f"Methodology ID: `{agent_methodology.get_profile(registry, agent)['methodology_id']}`",
            f"Methodology SHA-256: `{agent_methodology.digest(agent_methodology.get_profile(registry, agent))}`",
            "## Δημόσια μεθοδολογία που εφαρμόζω",
            f"Personality ID: `{agent_personality.get_profile(personality_registry, agent)['personality_id']}`",
            f"Personality SHA-256: `{agent_personality.digest(agent_personality.get_profile(personality_registry, agent))}`",
            "## Επαγγελματική προσωπικότητα",
            "## Λογική επεξεργασίας δεδομένων",
            "## Όρια αρμοδιότητας",
            "## Ελλιπή ή αντικρουόμενα δεδομένα",
            "## Δήλωση αφομοίωσης",
        ]
        for marker in required_markers:
            if marker not in text:
                errors.append(f"{agent}: missing or stale marker {marker!r}")
    return {
        "schema": "ecosystem.agent_assimilation_status.v1",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "contract_version": contract["version"],
        "contract_sha256": contract_digest,
        "status": "pass" if not errors else "fail",
        "scope": "theoretical_only",
        "agent_count": len(EXPECTED_AGENTS),
        "errors": errors,
        "daily_run_authorized_by_this_gate": False,
        "note": "Passing proves declared contract alignment and report freshness, not human-like cognition or trading competence.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and verify theoretical agent-assimilation reports.")
    parser.add_argument("command", choices=("build", "verify"))
    parser.add_argument("--contract", default="ecosystem_theory_contract.json")
    parser.add_argument("--reports-dir", default="docs/agent_reports")
    parser.add_argument("--status-out")
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parent
    contract_path = (project_root / args.contract).resolve()
    reports_dir = (project_root / args.reports_dir).resolve()
    result = (
        build_reports(contract_path, reports_dir, project_root)
        if args.command == "build"
        else verify_reports(contract_path, reports_dir, project_root)
    )
    if args.status_out:
        out = (project_root / args.status_out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") != "fail" else 2


if __name__ == "__main__":
    raise SystemExit(main())
