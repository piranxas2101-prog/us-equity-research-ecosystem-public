"""Canonical, non-LLM financial evidence packet and readiness gate."""
from __future__ import annotations
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any
from research_information_flow import digest

PACKET_SCHEMA = "ecosystem.financial_evidence_packet.v1"
READINESS_SCHEMA = "ecosystem.financial_readiness.v1"
CORE_FIELDS = {"revenue", "net_income", "cash", "operating_cash_flow", "stockholders_equity"}

def build_financial_evidence_packet(*, subject_id: str, ticker: str,
        sec_observation: dict[str, Any], supplementary_observations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    supplements = supplementary_observations or []
    sec_payload = dict(sec_observation.get("payload") or sec_observation)
    sec_fields = dict(sec_payload.get("fields") or {})
    values, periods, units, refs = {}, set(), Counter(), []
    for field, fact in sec_fields.items():
        if not isinstance(fact, dict): continue
        values[field] = {**fact, "provider": "sec_edgar", "authority": "primary_company_disclosure"}
        if fact.get("period_end"): periods.add(str(fact["period_end"]))
        if fact.get("unit"): units[str(fact["unit"])] += 1
        refs.append({"field": field, "provider": "sec_edgar", "accession": fact.get("accession"), "taxonomy": fact.get("taxonomy"), "concept": fact.get("concept"), "period_end": fact.get("period_end"), "filed": fact.get("filed")})
    conflicts = []
    unknown = [{"field": f, "reason_code": "no_data", "provider": "sec_edgar"} for f in sorted(CORE_FIELDS - set(values))]
    for observation in supplements:
        provider = str(observation.get("provider") or "supplementary")
        for field, item in dict((observation.get("payload") or {}).get("values") or {}).items():
            supplied = item.get("value") if isinstance(item, dict) else item
            if field in values:
                if supplied is not None and supplied != values[field].get("value"): conflicts.append({"field": field, "primary_provider": "sec_edgar", "supplementary_provider": provider, "winner": "sec_edgar", "silent_override": False})
            else: values[field] = {"value": supplied, "provider": provider, "authority": "supplementary", "as_of": observation.get("as_of")}
    currency = sec_payload.get("reporting_currency")
    body = {"schema": PACKET_SCHEMA, "subject_id": subject_id, "ticker": ticker.upper(), "created_at": datetime.now(timezone.utc).isoformat(), "as_of": sec_payload.get("as_of") or sec_observation.get("as_of"), "currency": currency, "units": sorted(units), "periods": sorted(periods), "values": values, "source_references": refs, "conflicts": conflicts, "unknown_fields": unknown, "sec_precedence_enforced": True, "llm_calls": 0}
    return {**body, "packet_id": "fin_" + digest(body)[:24], "payload_sha256": digest(body)}

def readiness_gate(packet: dict[str, Any]) -> dict[str, Any]:
    values, blockers, warnings = dict(packet.get("values") or {}), [], []
    if not packet.get("subject_id") or not packet.get("ticker"): blockers.append("identity_missing")
    missing = sorted(CORE_FIELDS - set(values))
    if "revenue" in missing: blockers.append("revenue_missing")
    if len(missing) >= 3: blockers.append("insufficient_core_financial_coverage")
    if packet.get("conflicts"): warnings.append("supplementary_source_conflicts")
    if not packet.get("periods"): blockers.append("period_missing")
    monetary_units = {unit for unit in packet.get("units") or [] if unit != "pure"}
    if len(monetary_units) > 1: blockers.append("mixed_reporting_currencies")
    # A valid historical filing is not necessarily current decision evidence.
    try:
        as_of = date.fromisoformat(str(packet.get("as_of"))[:10])
        income_periods = [
            date.fromisoformat(str(values[field]["period_end"])[:10])
            for field in ("revenue", "net_income")
            if isinstance(values.get(field), dict) and values[field].get("period_end")
        ]
        if income_periods and (as_of - min(income_periods)).days > 540:
            blockers.append("stale_income_statement_period")
    except ValueError:
        warnings.append("financial_period_age_unknown")
    if len({u for u in packet.get("units") or [] if u != "pure"}) > 2: warnings.append("multiple_units_require_review")
    ready = not blockers
    body = {"schema": READINESS_SCHEMA, "packet_id": packet.get("packet_id"), "ready_for_fundamental_synthesis": ready, "status": "ready" if ready else "evidence_gap", "blockers": blockers, "warnings": warnings, "missing_core_fields": missing, "evidence_gaps": list(packet.get("unknown_fields") or []), "fundamental_llm_budget": 1 if ready else 0, "reviewer_llm_budget": 0}
    return {**body, "readiness_sha256": digest(body)}
