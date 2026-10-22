from __future__ import annotations

from typing import Any

import priced_in_assessment


ASSESSED_PRICED_IN_STATES = {
    "assessed",
    "discounts_base_case",
    "discounts_bull_case",
    "discounts_bear_case",
    "underpricing_candidate",
    "overpricing_candidate",
    "balanced",
}

BLOCKER_METADATA = {
    "fresh_primary_evidence_missing": ("evidence", "Refresh dated primary-source evidence."),
    "valuation_missing": ("valuation", "Add at least one explicit security valuation input or range."),
    "priced_in_not_assessed": ("market_expectations", "Record what the current price already discounts."),
    "priced_in_evidence_invalid": (
        "market_expectations",
        "Attach a dated, evidence-backed priced-in assessment with a valid evidence hash.",
    ),
    "priced_in_confidence_too_low": (
        "market_expectations",
        "Add enough independent valuation and fundamental observations for Medium confidence.",
    ),
    "high_risk": ("risk", "Resolve or explicitly accept the high-risk condition before paper execution."),
    "unsupported_instrument": ("instrument", "Use a supported common-stock instrument or add an explicit instrument playbook."),
    "catalyst_not_classified": ("catalyst", "Classify the catalyst and its directional relevance."),
    "verdict_not_execution_candidate": ("decision", "Promote the thesis to candidate/manual_review only with new supporting evidence."),
}


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def fundamentals_current(row: dict[str, Any], fallback: bool, source_row: dict[str, Any] | None = None) -> bool:
    """Are the fundamentals current? The age of the last periodic report decides when it is known.

    ``fundamentals_fresh`` (Giannis) is True/False when the last 10-K/10-Q/20-F/40-F date is known and None when it
    is not; unknown falls back to ``fallback``, the any-filing-in-30-days flag. The any-filing flag itself is
    unchanged and still read by the risk gate and the paper trader.
    """
    known = row.get("fundamentals_fresh")
    if known is None and source_row:
        known = source_row.get("fundamentals_fresh")
    return bool(known) if known is not None else fallback


def assess(row: dict[str, Any], source_row: dict[str, Any] | None = None) -> dict[str, Any]:
    source_row = source_row or {}
    fresh = fundamentals_current(row, bool(row.get("fresh_primary_data")), source_row)
    risk = str(row.get("risk_level") or "Medium").strip()
    catalyst_status = str(row.get("catalyst_scoring_status") or "").strip().lower()
    valuation_fields = [
        row.get("forward_pe", source_row.get("forward_pe")),
        row.get("price_to_sales", source_row.get("price_to_sales")),
        row.get("price_to_book", source_row.get("price_to_book")),
        row.get("fair_value", source_row.get("fair_value")),
        row.get("valuation_range", source_row.get("valuation_range")),
    ]
    has_valuation = any(_present(value) for value in valuation_fields)
    priced_in_status = str(
        row.get("priced_in_status") or source_row.get("priced_in_status") or "not_assessed"
    ).strip().lower()
    priced_in_payload = row.get("priced_in_assessment") or source_row.get("priced_in_assessment")
    priced_in_state_known = priced_in_status in ASSESSED_PRICED_IN_STATES
    priced_in_evidence_backed = bool(
        priced_in_assessment.is_evidence_backed(priced_in_payload)
        and isinstance(priced_in_payload, dict)
        and str(priced_in_payload.get("priced_in_status") or "").strip().lower() == priced_in_status
    )
    priced_in_confidence = (
        str(priced_in_payload.get("confidence") or "Low").strip().title()
        if isinstance(priced_in_payload, dict)
        else "Low"
    )
    priced_in_confident = priced_in_confidence in {"Medium", "High"}
    priced_in_assessed = priced_in_state_known and priced_in_evidence_backed and priced_in_confident
    verdict = str(row.get("petros_verdict") or "").strip().lower()
    common_stock = str(row.get("instrument_type") or "common").strip().lower() == "common"
    catalyst_usable = catalyst_status not in {"unclassified", "missing"}
    company_thesis_status = "preliminary" if fresh else "untested"
    if fresh and has_valuation and priced_in_assessed:
        security_readiness = "ready"
    elif fresh and (has_valuation or priced_in_assessed):
        security_readiness = "conditional"
    else:
        security_readiness = "not_decision_grade"
    blockers: list[str] = []
    if not fresh:
        blockers.append("fresh_primary_evidence_missing")
    if not has_valuation:
        blockers.append("valuation_missing")
    if not priced_in_state_known:
        blockers.append("priced_in_not_assessed")
    elif not priced_in_evidence_backed:
        blockers.append("priced_in_evidence_invalid")
    elif not priced_in_confident:
        blockers.append("priced_in_confidence_too_low")
    if risk.lower() == "high":
        blockers.append("high_risk")
    if not common_stock:
        blockers.append("unsupported_instrument")
    if not catalyst_usable:
        blockers.append("catalyst_not_classified")
    if verdict not in {"candidate", "manual_review"}:
        blockers.append("verdict_not_execution_candidate")
    technical_review_eligible = bool(
        verdict in {"candidate", "manual_review", "watch"}
        and fresh
        and risk.lower() != "high"
        and common_stock
    )
    paper_test_eligible = bool(
        verdict in {"candidate", "manual_review"}
        and security_readiness == "ready"
        and catalyst_usable
        and risk.lower() != "high"
        and common_stock
    )
    blocker_details = [
        {
            "code": blocker,
            "category": BLOCKER_METADATA[blocker][0],
            "resolution_action": BLOCKER_METADATA[blocker][1],
        }
        for blocker in blockers
    ]
    missing_required_fields = []
    if not fresh:
        missing_required_fields.append("fresh_primary_data")
    if not has_valuation:
        missing_required_fields.append("valuation")
    if not priced_in_state_known:
        missing_required_fields.append("priced_in_status")
    elif not priced_in_evidence_backed:
        missing_required_fields.append("priced_in_assessment_evidence")
    elif not priced_in_confident:
        missing_required_fields.append("priced_in_assessment_confidence")
    if not catalyst_usable:
        missing_required_fields.append("catalyst_classification")
    return {
        "schema": "ecosystem.research_readiness.v1",
        "research_stage": "idea_triage",
        "company_thesis_status": company_thesis_status,
        "security_thesis_readiness": security_readiness,
        "valuation_status": "present_needs_review" if has_valuation else "missing",
        "priced_in_status": priced_in_status,
        "priced_in_assessed": priced_in_assessed,
        "priced_in_evidence_backed": priced_in_evidence_backed,
        "priced_in_confidence": priced_in_confidence,
        "priced_in_source_as_of": (priced_in_payload or {}).get("source_as_of") if isinstance(priced_in_payload, dict) else None,
        "technical_review_eligible": technical_review_eligible,
        "paper_test_eligible": paper_test_eligible,
        "execution_ready": paper_test_eligible,
        "live_capital_eligible": False,
        "decision_blockers": blockers,
        "primary_blocker": blockers[0] if blockers else None,
        "blocker_categories": sorted({item["category"] for item in blocker_details}),
        "blocker_details": blocker_details,
        "missing_required_fields": missing_required_fields,
        "resolution_actions": list(dict.fromkeys(item["resolution_action"] for item in blocker_details)),
        "readiness_status": "execution_ready" if paper_test_eligible else "blocked_with_diagnostics",
        "prove_criteria": [
            "Validate the catalyst against primary filings and dated company evidence.",
            "Confirm the independent trading setup without relying on upstream score repetition.",
        ],
        "kill_criteria": [
            "Primary evidence becomes stale, contradicted, or materially adverse.",
            "Risk becomes High or the instrument is not common stock.",
        ],
        "next_diligence": [
            "Build an explicit valuation range and identify what is already priced in.",
            "Record thesis-specific KPIs and a dated catalyst calendar.",
        ],
    }
