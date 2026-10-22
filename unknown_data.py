from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any


SCHEMA = "ecosystem.unknown_data_contract.v1"
EVENT_SCHEMA = "ecosystem.unknown_event.v1"
EXPECTED_AGENTS = (
    "babis", "giannis", "petros", "yankos", "periklis",
    "sokratis", "miltos", "koulis", "thymios",
)
VALID_REQUIREMENTS = {"required", "conditional", "optional", "diagnostic_only"}
UNKNOWN_STRINGS = {
    "unknown", "n/a", "na", "not available", "unclassified / insufficient evidence",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_contract(path: Path | None = None) -> dict[str, Any]:
    source = path or (Path(__file__).resolve().parent / "unknown_data_contract.json")
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"Unsupported UNKNOWN contract: {payload.get('schema')!r}")
    return payload


def validate_contract(contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    reasons = contract.get("reason_codes") or {}
    agents = contract.get("agents") or {}
    missing = sorted(set(EXPECTED_AGENTS) - set(agents))
    extra = sorted(set(agents) - set(EXPECTED_AGENTS))
    if missing:
        errors.append("missing agents: " + ", ".join(missing))
    if extra:
        errors.append("unexpected agents: " + ", ".join(extra))
    if "not_applicable" not in reasons or "provider_no_coverage" not in reasons:
        errors.append("reason taxonomy is incomplete")
    for code, rule in reasons.items():
        if not isinstance(rule.get("retryable"), bool):
            errors.append(f"{code}: retryable must be boolean")
        if not isinstance(rule.get("coverage_included"), bool):
            errors.append(f"{code}: coverage_included must be boolean")
        if not rule.get("default_action"):
            errors.append(f"{code}: missing default_action")
    for agent in EXPECTED_AGENTS:
        fields = (agents.get(agent) or {}).get("fields") or {}
        if not fields:
            errors.append(f"{agent}: no fields declared")
        for field, spec in fields.items():
            if spec.get("requirement") not in VALID_REQUIREMENTS:
                errors.append(f"{agent}.{field}: invalid requirement")
            if not spec.get("provider_route"):
                errors.append(f"{agent}.{field}: no provider route")
            if not spec.get("decision_impact") or not spec.get("on_missing"):
                errors.append(f"{agent}.{field}: incomplete decision contract")
    return errors


def is_missing(value: Any, *, must_be_true: bool = False) -> bool:
    if must_be_true:
        return value is not True
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str):
        return not value.strip() or value.strip().lower() in UNKNOWN_STRINGS
    return False


def _numeric(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _contains_field(values: Any, field: str) -> bool:
    if not isinstance(values, list):
        return False
    lowered = {str(value).lower() for value in values}
    return field.lower() in lowered or any(field.lower() in value for value in lowered)


def infer_reason(agent: str, field: str, row: dict[str, Any], spec: dict[str, Any]) -> str:
    explicit = row.get("unknown_reason_overrides") or {}
    if isinstance(explicit, dict) and explicit.get(field):
        return str(explicit[field])
    if _contains_field(row.get("not_applicable_fields"), field):
        return "not_applicable"
    if _contains_field(row.get("conflicting_fields"), field):
        return "conflicting_sources"
    if _contains_field(row.get("parse_error_fields"), field):
        return "parse_or_schema_error"
    if _contains_field(row.get("stale_fields"), field) or _contains_field(row.get("data_age_flags"), field):
        return "stale"

    sector = str(row.get("sector_id") or row.get("sector") or "").lower()
    net_income = _numeric(row.get("net_income_ttm"))
    if field == "forward_pe" and net_income is not None and net_income <= 0:
        return "not_applicable"
    if field in {"gross_margin", "roe", "debt_to_equity", "current_ratio"} and "financial" in sector:
        return "not_applicable"
    if field == "fresh_primary_data" and row.get(field) is not True:
        return "stale"

    source_base = row.get("source_base") or {}
    sec_error = str(source_base.get("sec_filings_error") or "").lower()
    if "sec_edgar" in (spec.get("provider_route") or []) and sec_error:
        if "symbol_mapping_error" in sec_error:
            return "symbol_mapping_error"
        if any(token in sec_error for token in ("daily_budget", "budget_exhausted")):
            return "provider_daily_budget_exhausted"
        if any(token in sec_error for token in ("429", "rate_limited", "shared_cooldown")):
            return "provider_rate_limited"
        if any(token in sec_error for token in ("timeout", "temporar", "connection")):
            return "temporarily_unavailable"
        return "parse_or_schema_error"
    provider_errors = source_base.get("market_data_provider_errors") or []
    if provider_errors and field in {"share_price", "current_session_15m_bars", "latest_15m_bar"}:
        codes = " ".join(str(item.get("error_code") or item.get("status") or "") for item in provider_errors if isinstance(item, dict)).lower()
        if any(token in codes for token in ("daily_budget", "budget_exhausted")):
            return "provider_daily_budget_exhausted"
        if any(token in codes for token in ("429", "rate_limited", "shared_cooldown")):
            return "provider_rate_limited"
        return "temporarily_unavailable" if any(token in codes for token in ("transport", "timeout")) else "provider_no_coverage"

    if field in {"sector_id", "industry_id"} and str(row.get("classification_status") or "").lower() == "unknown":
        return "provider_no_coverage"
    if field.startswith("catalyst_"):
        return "insufficient_source_detail" if row.get("catalyst_evidence") else "not_disclosed"
    if field in {"atm_shelf_warrants_convertibles", "concentration_risk"}:
        return "insufficient_source_detail"
    if field.endswith("_hash") or field in {"run_id", "intent_id", "approved_intent_id", "gate_status", "unknown_events"}:
        return "contract_mismatch"
    if "broker_account_snapshot" in (spec.get("provider_route") or []):
        return "temporarily_unavailable"
    return "provider_no_coverage"


def _attempt_metadata(row: dict[str, Any], field: str | None = None) -> tuple[int, list[str]]:
    source_base = row.get("source_base") or {}
    attempts = source_base.get("market_data_provider_attempts") or []
    if field == "priced_in_status":
        attempts = source_base.get("priced_in_analysis_attempts") or attempts
    if not isinstance(attempts, list):
        return 0, []
    providers = []
    for attempt in attempts:
        if isinstance(attempt, dict) and attempt.get("provider"):
            providers.append(str(attempt["provider"]))
    return len(attempts), providers


def make_event(
    agent: str,
    field: str,
    row: dict[str, Any],
    spec: dict[str, Any],
    reason_code: str,
    contract: dict[str, Any],
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    reasons = contract["reason_codes"]
    if reason_code not in reasons:
        raise ValueError(f"Unsupported UNKNOWN reason code: {reason_code}")
    reason = reasons[reason_code]
    requirement = str(spec["requirement"])
    coverage_included = bool(reason["coverage_included"]) and requirement in {"required", "conditional"}
    penalty_weight = {"required": 1.0, "conditional": 0.5, "optional": 0.0, "diagnostic_only": 0.0}[requirement]
    if not coverage_included:
        penalty_weight = 0.0
    attempts, fallbacks_tried = _attempt_metadata(row, field)
    route = list(spec.get("provider_route") or [])
    ticker = str(row.get("ticker") or row.get("symbol") or "").upper()
    body = {
        "schema": EVENT_SCHEMA,
        "run_id": run_id or row.get("run_id"),
        "agent": agent,
        "stage": agent,
        "ticker": ticker,
        "field": field,
        "value": "UNKNOWN",
        "reason_code": reason_code,
        "requirement": requirement,
        "applicability": spec.get("applicability", "general"),
        "provider": route[0] if route else None,
        "provider_route": route,
        "source_id": (row.get("source_base") or {}).get("primary_data_feed"),
        "source_as_of": (row.get("source_base") or {}).get("snapshot_time") or row.get("run_timestamp"),
        "freshness_limit": spec.get("freshness_limit"),
        "attempts": attempts,
        "fallbacks_tried": fallbacks_tried,
        "retryable": bool(reason["retryable"]),
        "default_action": reason["default_action"],
        "on_missing": spec["on_missing"],
        "decision_impact": spec["decision_impact"],
        "coverage_included": coverage_included,
        "penalty_weight": penalty_weight,
        "owner": agent,
        "resolution_status": "open" if reason_code != "not_applicable" else "resolved_not_applicable",
    }
    return {**body, "unknown_event_id": f"unknown_{digest(body)[:24]}"}


def events_for_row(
    agent: str,
    row: dict[str, Any],
    *,
    run_id: str | None = None,
    contract: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    registry = contract or load_contract()
    fields = registry["agents"][agent]["fields"]
    events: list[dict[str, Any]] = []
    for field, spec in fields.items():
        value = row.get(field)
        configured_missing = {
            str(item).strip().lower() for item in (spec.get("missing_values") or [])
        }
        missing = is_missing(value, must_be_true=bool(spec.get("must_be_true")))
        if isinstance(value, str) and value.strip().lower() in configured_missing:
            missing = True
        if missing:
            reason = infer_reason(agent, field, row, spec)
            events.append(make_event(agent, field, row, spec, reason, registry, run_id=run_id))
    observed_fields = {event["field"] for event in events}
    for field, value in row.items():
        if field in observed_fields or field in fields:
            continue
        if not isinstance(value, str) or value.strip().lower() not in UNKNOWN_STRINGS:
            continue
        spec = {
            "requirement": "diagnostic_only",
            "provider_route": ["upstream_contract"],
            "decision_impact": "diagnostic_visibility",
            "on_missing": "reason_code_required",
        }
        reason = infer_reason(agent, field, row, spec)
        events.append(make_event(agent, field, row, spec, reason, registry, run_id=run_id))
    return events


def summarize_events(events: list[dict[str, Any]], *, declared_fields: int) -> dict[str, Any]:
    by_reason: dict[str, int] = {}
    by_requirement: dict[str, int] = {}
    penalty_units = 0.0
    coverage_missing = 0
    not_applicable = 0
    for event in events:
        by_reason[event["reason_code"]] = by_reason.get(event["reason_code"], 0) + 1
        by_requirement[event["requirement"]] = by_requirement.get(event["requirement"], 0) + 1
        penalty_units += float(event.get("penalty_weight") or 0.0)
        coverage_missing += int(bool(event.get("coverage_included")))
        not_applicable += int(event.get("reason_code") == "not_applicable")
    denominator = max(0, declared_fields - not_applicable)
    coverage = 100.0 if denominator == 0 else max(0.0, (denominator - coverage_missing) / denominator * 100.0)
    return {
        "event_count": len(events),
        "coverage_missing_count": coverage_missing,
        "not_applicable_count": not_applicable,
        "penalty_units": round(penalty_units, 2),
        "effective_contract_coverage_pct": round(coverage, 2),
        "by_reason": dict(sorted(by_reason.items())),
        "by_requirement": dict(sorted(by_requirement.items())),
    }


def annotate_row(
    row: dict[str, Any],
    agent: str,
    *,
    run_id: str | None = None,
    contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    registry = contract or load_contract()
    annotated = dict(row)
    upstream = list(row.get("unknown_events") or [])
    events = events_for_row(agent, row, run_id=run_id, contract=registry)
    declared = len(registry["agents"][agent]["fields"])
    summary = summarize_events(events, declared_fields=declared)
    annotated["upstream_unknown_events"] = upstream
    annotated["unknown_events"] = events
    annotated["unknown_fields"] = [event["field"] for event in events if event["coverage_included"]]
    annotated["unknown_summary"] = summary
    annotated["unknown_contract_schema"] = SCHEMA
    annotated["unknown_contract_version"] = registry["version"]
    annotated["unknown_contract_sha256"] = digest(registry)
    return annotated


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the versioned UNKNOWN data contract.")
    parser.add_argument("command", choices=("verify",))
    parser.add_argument("--contract", default="unknown_data_contract.json")
    args = parser.parse_args()
    contract = load_contract(Path(args.contract))
    errors = validate_contract(contract)
    print(json.dumps({"schema": "ecosystem.unknown_data_contract_verification.v1", "status": "pass" if not errors else "fail", "version": contract.get("version"), "sha256": digest(contract), "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
