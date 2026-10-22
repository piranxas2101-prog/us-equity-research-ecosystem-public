from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import agent_assimilation
import agent_methodology
import agent_personality
import fundamental_research_packet
import priced_in_assessment
import unknown_data


ARTIFACTS = {
    "babis": "output/babis_shortlist_latest.json",
    "giannis": "output/giannis_rule_based_latest.json",
    "petros": "output/petros_review_latest.json",
    "yankos": "output/yankos_strategy_latest.json",
    "periklis": "output/periklis_strategy_latest.json",
    "sokratis": "output/sokratis_strategy_teacher_latest.json",
    "miltos": "output/miltos_entry_stalker_latest.json",
    "koulis": "output/koulis_order_intents_latest.json",
    "thymios": "output/thymios_execution_validation_latest.json"
}

PHASE_AGENTS = {
    "fundamental": ["babis", "giannis"],
    "research": ["babis", "giannis", "petros"],
    # Research-only runs still produce trader analysis and Sokratis lessons, but
    # deliberately do not execute Miltos, Koulis or Thymios broker handoffs.
    "research_only": ["babis", "giannis", "petros", "yankos", "periklis", "sokratis"],
    "full": list(agent_assimilation.EXPECTED_AGENTS),
}


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def row_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("results", "shortlist", "decisions", "openings"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def grouped(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get(field) or "unknown") for row in rows).items()))


def observed_field_value(row: dict[str, Any], field: str, route: list[str]) -> Any:
    """Read trader-plan concepts from their concrete artifact fields."""

    if field in row:
        return row[field]
    if route == ["trader_plan"]:
        plan = row.get("paper_trade_plan")
        if isinstance(plan, dict):
            plan_field = {"trigger": "entry_confirmation", "invalidator": "invalidators"}.get(field)
            if plan_field:
                return plan.get(plan_field)
    return None


def required_field_liveness(
    agent: str,
    payload: dict[str, Any] | None,
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Detect a required capability that produced no value and was never attempted."""

    rows = row_list(payload or {})
    field_specs = ((contract.get("agents") or {}).get(agent) or {}).get("fields") or {}
    issues: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    for field, spec in field_specs.items():
        if spec.get("requirement") != "required":
            continue
        route = [str(value) for value in (spec.get("provider_route") or [])]
        if not rows or route == ["upstream_contract"]:
            continue
        configured_missing = {
            str(value).strip().lower() for value in (spec.get("missing_values") or [])
        }
        missing_rows = []
        matching_events: list[dict[str, Any]] = []
        for row in rows:
            value = observed_field_value(row, field, route)
            missing = unknown_data.is_missing(
                value,
                must_be_true=bool(spec.get("must_be_true")),
            )
            if isinstance(value, (list, dict)) and not value:
                missing = True
            if isinstance(value, str) and value.strip().lower() in configured_missing:
                missing = True
            if not missing:
                continue
            missing_rows.append(row)
            matching_events.extend(
                event
                for event in (row.get("unknown_events") or [])
                if isinstance(event, dict) and str(event.get("field") or "") == field
            )
        attempts = sum(int(event.get("attempts") or 0) for event in matching_events)
        observation = {
            "field": field,
            "row_count": len(rows),
            "missing_count": len(missing_rows),
            "attempts": attempts,
            "provider_route": route,
        }
        observations.append(observation)
        if len(missing_rows) == len(rows) and attempts == 0:
            issues.append(
                {
                    **observation,
                    "code": "required_field_universally_missing_without_attempt",
                    "severity": "hard_failure",
                }
            )
    return {
        "ok": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "observations": observations,
    }


def methodology_conformance_checks(
    agent: str,
    payload: dict[str, Any] | None,
    registry: dict[str, Any],
) -> dict[str, bool]:
    expected = agent_methodology.get_profile(registry, agent)
    identity_key = "validation_methodology" if agent == "thymios" else "methodology"
    observed = (payload or {}).get(identity_key)
    return {
        "methodology_identity_present": isinstance(observed, dict),
        "methodology_id_matches": (
            isinstance(observed, dict)
            and observed.get("methodology_id") == expected["methodology_id"]
        ),
        "methodology_hash_matches": (
            isinstance(observed, dict)
            and observed.get("methodology_sha256") == agent_methodology.digest(expected)
        ),
        "methodology_does_not_expand_authority": (
            isinstance(observed, dict) and observed.get("authority_expanded") is False
        ),
    }


def personality_conformance_checks(
    agent: str,
    payload: dict[str, Any] | None,
    registry: dict[str, Any],
) -> dict[str, bool]:
    expected = agent_personality.get_profile(registry, agent)
    identity_key = "validation_personality" if agent == "thymios" else "personality"
    observed = (payload or {}).get(identity_key)
    return {
        "personality_identity_present": isinstance(observed, dict),
        "personality_id_matches": (
            isinstance(observed, dict)
            and observed.get("personality_id") == expected["personality_id"]
        ),
        "personality_hash_matches": (
            isinstance(observed, dict)
            and observed.get("personality_sha256") == agent_personality.digest(expected)
        ),
        "personality_does_not_influence_decisions": (
            isinstance(observed, dict) and observed.get("decision_influence_allowed") is False
        ),
        "personality_does_not_expand_authority": (
            isinstance(observed, dict) and observed.get("authority_expanded") is False
        ),
    }


def summarize(agent: str, payload: dict[str, Any]) -> dict[str, Any]:
    rows = row_list(payload)
    summary: dict[str, Any] = {
        "run_id": payload.get("run_id"),
        "run_timestamp": payload.get("run_timestamp") or payload.get("generated_at"),
        "row_count": len(rows),
    }
    if agent == "babis":
        shortlist = payload.get("shortlist") or []
        history_exclusions = list(payload.get("history_exclusions") or [])
        technical_errors = []
        for record in payload.get("errors") or []:
            if str(record.get("error_code") or "") in {
                "all_providers_insufficient_history",
                "provider_partial_history",
            }:
                history_exclusions.append(record)
            else:
                technical_errors.append(record)
        summary.update(
            universe_total=payload.get("universe_total"),
            shortlist_count=len(shortlist),
            trap_count=len(payload.get("traps") or []),
            error_count=len(technical_errors),
            history_exclusion_count=len(history_exclusions),
            sector_known_count=sum(
                1 for row in shortlist if row.get("sector_id") not in (None, "", "unknown")
            ),
        )
        summary["actions"] = [{
            "ticker": row.get("ticker"), "rank": row.get("rank"), "sector_id": row.get("sector_id"),
            "industry_id": row.get("industry_id"), "catalyst_category_id": row.get("catalyst_category_id"),
            "catalyst_direction": row.get("catalyst_direction"), "catalyst_attention_score": row.get("catalyst_attention_score"),
            "classification_reason": row.get("catalyst_classification_reason"), "risk_level": row.get("risk_level"),
            "verdict": row.get("verdict"), "evidence_count": len(row.get("catalyst_evidence") or []),
            "missing_fields": row.get("unknown_fields") or []
        } for row in shortlist]
    elif agent == "giannis":
        summary.update(tiers=grouped(rows, "giannis_tier"), risks=grouped(rows, "risk_level"), classification=grouped(rows, "classification_status"))
        summary["actions"] = [{
            "ticker": row.get("ticker"), "score_final": row.get("score_final"), "tier": row.get("giannis_tier"),
            "score_base": row.get("score_base"), "score_liquidity": row.get("score_liquidity"),
            "score_catalyst": row.get("score_catalyst"), "score_smart_money": row.get("score_smart_money"),
            "score_penalty": row.get("score_penalty"), "risk_level": row.get("risk_level"),
            "dilution_flags": row.get("dilution_flags") or [], "unknown_fields": row.get("unknown_fields") or [],
            "fresh_primary_data": row.get("fresh_primary_data")
        } for row in rows]
    elif agent == "petros":
        summary.update(verdicts=grouped(rows, "petros_verdict"))
        summary.update(priced_in_statuses=grouped(rows, "priced_in_status"))
        summary["actions"] = [{
            "ticker": row.get("ticker"), "score": row.get("petros_score"), "verdict": row.get("petros_verdict"),
            "confidence": row.get("confidence"), "rationale": row.get("rationale") or row.get("rationale_points") or [],
            "manual_review": row.get("petros_verdict") == "manual_review",
            "priced_in_status": row.get("priced_in_status"),
            "priced_in_confidence": row.get("priced_in_confidence"),
            "priced_in_source_as_of": row.get("priced_in_source_as_of"),
            "expectations_burden_score": row.get("expectations_burden_score"),
            "priced_in_evidence_sha256": (row.get("priced_in_assessment") or {}).get("evidence_sha256"),
        } for row in rows]
    elif agent in {"yankos", "periklis"}:
        summary.update(verdicts=grouped(rows, "signal_verdict"), directions=grouped(rows, "trade_direction"), playbooks=grouped(rows, "playbook_name"))
        summary["actions"] = [{
            "ticker": row.get("ticker"), "playbook": row.get("playbook_name"), "verdict": row.get("signal_verdict"),
            "direction": row.get("trade_direction"), "selected_score": row.get("signal_score"),
            "long_score": row.get("long_signal_score"), "short_score": row.get("short_signal_score"),
            "confidence": row.get("confidence"), "technical_timing": row.get("technical_timing"),
            "risk_level": row.get("risk_level"), "rationale": row.get("rationale") or []
        } for row in rows]
    elif agent == "sokratis":
        summary.update(skill_proposals=len(payload.get("skill_proposals") or []), change_proposals=len(payload.get("change_proposals") or []))
        summary["actions"] = [{
            "proposal_id": row.get("proposal_id") or row.get("skill_name"), "status": row.get("status"),
            "reason": row.get("reason"), "suggested_change": row.get("suggested_change"),
            "affected_agents": row.get("affected_agents") or row.get("students") or []
        } for row in (payload.get("change_proposals") or payload.get("skill_proposals") or []) if isinstance(row, dict)]
    elif agent == "miltos":
        summary.update(status=payload.get("status"), completion_reason=payload.get("completion_reason"))
        summary.update(**{key: len(payload.get(key) or []) for key in ("submitted", "still_waiting", "skipped", "failed")})
        summary["actions"] = {key: payload.get(key) or [] for key in ("submitted", "still_waiting", "skipped", "failed")}
    elif agent == "koulis":
        summary.update(gate_status=payload.get("gate_status"), completion_status=payload.get("completion_status"), approved_count=payload.get("approved_count"), blocked_count=payload.get("blocked_count"), prefiltered_count=payload.get("prefiltered_count"))
        summary["actions"] = {"approved": payload.get("approved_intents") or [], "blocked": payload.get("blocked_intents") or [], "prefiltered": payload.get("prefiltered_observations") or []}
    elif agent == "thymios":
        rows = payload.get("validated_intents") or []
        summary.update(contract_schema=payload.get("schema"), status=payload.get("status"), validated=payload.get("validated"), approved_intent_count=payload.get("approved_intent_count", len(rows)))
        summary["actions"] = [{
            "intent_id": row.get("intent_id"), "agent": row.get("agent"), "ticker": row.get("ticker"),
            "direction": row.get("trade_direction"), "playbook": row.get("playbook_name"),
            "status": row.get("status"), "notional_usd": row.get("notional_usd")
        } for row in rows if isinstance(row, dict)]
    return summary


def render(record: dict[str, Any]) -> str:
    profile = record["profile"]
    lines = [
        f"# {record['agent'].title()} — Run Report", "",
        f"- Run ID: `{record['expected_run_id']}`", f"- Artifact: `{record['artifact']}`",
        f"- Observable conformance: **{record['status']}**", "",
        "## Συμφωνημένη δουλειά", "", profile["mission"], "",
        "## Συμφωνημένος τρόπος επεξεργασίας", ""
    ]
    lines.extend(f"{index}. {step}" for index, step in enumerate(profile["processing_logic"], 1))
    lines.extend(["", "## Εργασία που καταγράφηκε", "", "```json", json.dumps(record["actual_work"], ensure_ascii=False, indent=2), "```", "", "## Έλεγχοι", ""])
    lines.extend(f"- {key}: {'PASS' if value else 'FAIL'}" for key, value in record["checks"].items())
    lines.extend(["", "## Έλεγχος liveness υποχρεωτικών πεδίων", "", "```json", json.dumps(record.get("liveness") or {}, ensure_ascii=False, indent=2), "```"])
    lines.extend(["", "Το report ελέγχει το observable artifact. Η πλήρης απόδειξη κάθε εσωτερικού βήματος απαιτεί logs και στοχευμένα tests.", ""])
    return "\n".join(lines)


def build(project_root: Path, agents: list[str], run_id: str, phase: str) -> dict[str, Any]:
    contract = agent_assimilation.load_contract(project_root / "ecosystem_theory_contract.json")
    methodology_registry = agent_methodology.load_registry(project_root / "agent_methodology_registry.json")
    personality_registry = agent_personality.load_registry(project_root / "agent_personality_registry.json")
    unknown_contract = unknown_data.load_contract(project_root / "unknown_data_contract.json")
    packet_path = project_root / "output" / "fundamental_research_packet_latest.json"
    packet = load_json(packet_path)
    packet_valid = False
    packet_error = None
    if packet is not None:
        try:
            fundamental_research_packet.validate_packet(packet, expected_run_id=run_id)
            packet_valid = True
        except fundamental_research_packet.FundamentalPacketError as exc:
            packet_error = str(exc)
    report_dir = project_root / "output" / "ecosystem_test_reports" / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for agent in agents:
        relative = ARTIFACTS[agent]
        payload = load_json(project_root / relative)
        actual_run = str((payload or {}).get("run_id") or "")
        checks = {
            "artifact_present": payload is not None,
            "run_id_present": bool(actual_run),
            "run_id_matches": bool(payload) and actual_run == run_id,
            "output_structured": isinstance(payload, dict),
        }
        checks.update(methodology_conformance_checks(agent, payload, methodology_registry))
        checks.update(personality_conformance_checks(agent, payload, personality_registry))
        liveness = required_field_liveness(agent, payload, unknown_contract)
        checks["required_field_liveness_ok"] = bool(liveness["ok"])
        if agent == "thymios":
            checks["validation_artifact_schema_valid"] = bool(payload) and payload.get("schema") == "ecosystem.thymios_validation.v1"
            checks["source_intent_schema_supported"] = bool(payload) and payload.get("source_schema") == "ecosystem.approved_order_intents.v1"
            checks["validation_completed_or_refused_safely"] = bool(payload) and payload.get("status") in {
                "validated", "validated_no_intents", "upstream_gate_blocked"
            }
        if agent == "petros":
            checks["fundamental_only"] = bool(payload) and payload.get("analysis_mode") == "fundamental_only"
            checks["technical_inputs_excluded"] = bool(payload) and payload.get("technical_inputs_used") is False
            checks["trader_inputs_excluded"] = bool(payload) and payload.get("trader_inputs_used") is False
            checks["fundamental_packet_valid"] = packet_valid
            petros_rows = row_list(payload or {})
            checks["priced_in_assessment_attempted_for_every_row"] = bool(petros_rows) and all(
                isinstance(row.get("priced_in_assessment"), dict)
                and bool(row["priced_in_assessment"].get("attempts"))
                for row in petros_rows
            )
            checks["priced_in_evidence_valid_for_assessed_rows"] = bool(petros_rows) and all(
                str(row.get("priced_in_status") or "not_assessed") == "not_assessed"
                or priced_in_assessment.is_evidence_backed(row.get("priced_in_assessment"))
                for row in petros_rows
            )
        if agent in {"yankos", "periklis"}:
            checks["fundamental_packet_valid"] = packet_valid
            checks["fundamental_packet_hash_matches"] = (
                bool(payload)
                and packet_valid
                and payload.get("fundamental_packet_hash") == packet.get("packet_hash")
            )
            checks["fundamental_gate_observable"] = bool(payload) and isinstance(payload.get("fundamental_gate"), dict)
            checks["every_row_has_fundamental_gate"] = bool(payload) and all(
                row.get("fundamental_gate_status") in {"pass", "technical_review_only"}
                for row in row_list(payload)
            )
        status = "pass" if all(checks.values()) else ("partial" if payload else "missing")
        record = {"agent": agent, "phase": phase, "expected_run_id": run_id, "artifact": relative, "profile": contract["agents"][agent], "actual_work": summarize(agent, payload) if payload else None, "checks": checks, "liveness": liveness, "status": status}
        (report_dir / f"{agent}_run_report.md").write_text(render(record), encoding="utf-8")
        (report_dir / f"{agent}_run_report.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        records.append(record)
    status_counts = Counter(record["status"] for record in records)
    overall = "pass" if status_counts.get("pass", 0) == len(records) else "partial"
    result = {
        "schema": "ecosystem.agent_run_audit.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        "run_id": run_id,
        "status": overall,
        "status_counts": dict(status_counts),
        "liveness_issue_count": sum(record["liveness"]["issue_count"] for record in records),
        "report_dir": str(report_dir),
        "fundamental_packet": {
            "artifact": "output/fundamental_research_packet_latest.json",
            "present": packet is not None,
            "valid": packet_valid,
            "hash": (packet or {}).get("packet_hash"),
            "error": packet_error,
        },
        "records": records,
    }
    (report_dir / f"{phase}_audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (project_root / "output" / f"{phase}_audit_latest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare actual agent work with the ecosystem theory contract.")
    parser.add_argument("--phase", choices=tuple(PHASE_AGENTS), required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    agents = PHASE_AGENTS[args.phase]
    result = build(Path(__file__).resolve().parent, agents, args.run_id, args.phase)
    print(json.dumps({key: result[key] for key in ("phase", "run_id", "status", "status_counts", "report_dir")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
