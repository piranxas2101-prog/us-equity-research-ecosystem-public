"""Read-only, point-in-time SEC Companyfacts pilot.

The module deliberately stays outside the daily pipeline.  It extracts a small,
auditable set of issuer facts, preserves the exact taxonomy fact used, and never
allows a supplementary provider to silently override SEC evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any


SCHEMA = "ecosystem.sec_xbrl_pilot.v1"
ALLOWED_FORMS = {"10-K", "10-Q", "20-F", "40-F", "8-K"}

# Ordered issuer-wide, standard-taxonomy concepts.  The first concept with a
# valid point-in-time fact wins; custom extensions are intentionally excluded.
FIELD_CONCEPTS: dict[str, tuple[str, ...]] = {
    "cash": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "CashAndCashEquivalents",
    ),
    "debt": (
        "LongTermDebtAndFinanceLeaseObligations",
        "LongTermDebtAndCapitalLeaseObligations",
        "LongTermDebt",
    ),
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "Revenues",
        "Revenue",
        "RevenueFromContractsWithCustomers",
    ),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "gross_profit": ("GrossProfit",),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "CashFlowsFromUsedInOperatingActivities",
    ),
    "stockholders_equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "EquityAttributableToOwnersOfParent",
        "Equity",
    ),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _as_of(value: str | datetime) -> tuple[datetime, bool]:
    if isinstance(value, datetime):
        result = value
        date_only = False
    else:
        text = str(value).strip().replace("Z", "+00:00")
        date_only = len(text) == 10
        result = datetime.combine(date.fromisoformat(text), time.max) if date_only else datetime.fromisoformat(text)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc), date_only


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _accepted(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fact_available(
    fact: dict[str, Any],
    cutoff: datetime,
    date_only: bool,
    accepted_by_accession: dict[str, str],
) -> tuple[bool, str, str | None]:
    accession = str(fact.get("accn") or "")
    accepted_at = _accepted(accepted_by_accession.get(accession))
    if accepted_at is not None:
        return accepted_at <= cutoff, "accepted_timestamp", accepted_at.isoformat()
    filed = _date(fact.get("filed"))
    if filed is None:
        return False, "missing_availability", None
    # Companyfacts normally exposes only the filing date.  For an intraday
    # cutoff, same-day facts are excluded unless an exact accepted timestamp is
    # supplied, preventing accidental look-ahead within that day.
    available = filed <= cutoff.date() if date_only else filed < cutoff.date()
    return available, "filed_date_conservative", filed.isoformat()


def _candidate_rows(payload: dict[str, Any], taxonomy: str, concept: str) -> list[tuple[str, dict[str, Any]]]:
    concept_payload = (((payload.get("facts") or {}).get(taxonomy) or {}).get(concept) or {})
    rows: list[tuple[str, dict[str, Any]]] = []
    for unit, facts in (concept_payload.get("units") or {}).items():
        # All selected financial fields are monetary. Preserve the filing's
        # reporting currency instead of silently dropping non-USD IFRS facts.
        if not re.fullmatch(r"[A-Z]{3}", str(unit)):
            continue
        for fact in facts or []:
            if isinstance(fact, dict):
                rows.append((unit, fact))
    return rows


def _collect_candidates(
    payload: dict[str, Any],
    concepts: tuple[str, ...],
    cutoff: datetime,
    date_only: bool,
    accepted_map: dict[str, str],
    currency: str | None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for taxonomy in ("us-gaap", "ifrs-full"):
        for priority, concept in enumerate(concepts):
            for unit, fact in _candidate_rows(payload, taxonomy, concept):
                if currency is not None and unit != currency:
                    continue
                if str(fact.get("form") or "") not in ALLOWED_FORMS:
                    continue
                end = _date(fact.get("end"))
                if end is None or end > cutoff.date():
                    continue
                available, basis, available_at = _fact_available(
                    fact, cutoff, date_only, accepted_map
                )
                if not available:
                    continue
                value = fact.get("val")
                if not isinstance(value, (int, float)):
                    continue
                candidates.append(
                    {
                        "value": value,
                        "taxonomy": taxonomy,
                        "concept": concept,
                        "unit": unit,
                        "period_start": fact.get("start"),
                        "period_end": fact.get("end"),
                        "filed": fact.get("filed"),
                        "available_at": available_at,
                        "availability_basis": basis,
                        "form": fact.get("form"),
                        "fiscal_year": fact.get("fy"),
                        "fiscal_period": fact.get("fp"),
                        "frame": fact.get("frame"),
                        "accession": fact.get("accn"),
                        "concept_priority": priority,
                    }
                )
    return candidates


def select_fact(
    payload: dict[str, Any],
    concepts: tuple[str, ...],
    as_of: str | datetime,
    *,
    accepted_by_accession: dict[str, str] | None = None,
    currency: str | None = None,
) -> dict[str, Any] | None:
    cutoff, date_only = _as_of(as_of)
    candidates = _collect_candidates(
        payload, concepts, cutoff, date_only, accepted_by_accession or {}, currency
    )
    if not candidates:
        return None
    candidates.sort(
        key=lambda row: (
            str(row.get("period_end") or ""),
            str(row.get("available_at") or ""),
            str(row.get("filed") or ""),
            -int(row["concept_priority"]),
            row["taxonomy"] == "us-gaap",
        ),
        reverse=True,
    )
    return candidates[0]


# Second-pass revenue concept, used only when none of FIELD_CONCEPTS["revenue"] yields a fact. Some issuers
# (AAON, Old Dominion, Valero, ...) report only this one. It is NOT in the main list because the main selection
# takes the most recent period among all listed concepts: adding it there would let a small segment-level fact
# beat the total under "Revenues" (Mercury General: 15.9M instead of 3.2B) and would change 46 companies that
# already have revenue. It includes assessed taxes (e.g. excise duties) and is marked in the result.
REVENUE_FALLBACK_CONCEPTS = ("RevenueFromContractWithCustomerIncludingAssessedTax",)
REVENUE_FALLBACK_METHOD = "revenue_including_assessed_tax_fallback"


BANK_REVENUE_PROXY_METHOD = "bank_net_interest_plus_noninterest_income"
BANK_REVENUE_PROXY_CONCEPTS = ("InterestIncomeExpenseNet", "NoninterestIncome")


def bank_revenue_proxy(
    payload: dict[str, Any],
    as_of: str | datetime,
    *,
    accepted_by_accession: dict[str, str] | None = None,
    currency: str | None = None,
) -> dict[str, Any] | None:
    """Net interest income + noninterest income of one filing period, for banks that tag no revenue concept.

    Banks usually report no ``Revenues`` fact. Their revenue by banking convention is net interest income plus
    noninterest income; both components must come from the same filing and period, otherwise the value stays
    unknown. The result is labelled as a proxy and keeps both components, so it is never read as reported revenue.
    """
    cutoff, date_only = _as_of(as_of)
    accepted_map = accepted_by_accession or {}
    by_concept = [
        {
            (row["unit"], row["period_start"], row["period_end"], row["accession"]): row
            for row in _collect_candidates(payload, (concept,), cutoff, date_only, accepted_map, currency)
        }
        for concept in BANK_REVENUE_PROXY_CONCEPTS
    ]
    shared = set(by_concept[0]) & set(by_concept[1])
    if not shared:
        return None
    key = max(shared, key=lambda k: (str(k[2] or ""), str(by_concept[0][k].get("available_at") or ""), str(k[3] or "")))
    net_interest, noninterest = by_concept[0][key], by_concept[1][key]
    return {
        **{name: net_interest[name] for name in (
            "taxonomy", "unit", "period_start", "period_end", "filed", "available_at", "availability_basis",
            "form", "fiscal_year", "fiscal_period", "accession")},
        "value": net_interest["value"] + noninterest["value"],
        "concept": "+".join(BANK_REVENUE_PROXY_CONCEPTS),
        "frame": None,
        "concept_priority": len(FIELD_CONCEPTS["revenue"]),
        "proxy": True,
        "proxy_method": BANK_REVENUE_PROXY_METHOD,
        "components": [
            {"concept": row["concept"], "value": row["value"]} for row in (net_interest, noninterest)
        ],
    }


def reporting_currency(payload: dict[str, Any], as_of: str | datetime,
                       accepted_by_accession: dict[str, str] | None = None) -> str | None:
    currencies = {
        unit
        for taxonomy in ("us-gaap", "ifrs-full")
        for concepts in FIELD_CONCEPTS.values()
        for concept in concepts
        for unit in (((payload.get("facts") or {}).get(taxonomy) or {}).get(concept) or {}).get("units", {})
        if re.fullmatch(r"[A-Z]{3}", str(unit))
    }
    coverage = {
        unit: sum(
            select_fact(payload, concepts, as_of,
                        accepted_by_accession=accepted_by_accession, currency=unit) is not None
            for concepts in FIELD_CONCEPTS.values()
        )
        for unit in currencies
    }
    eligible = [unit for unit, count in coverage.items() if count]
    if not eligible:
        return None
    def currency_rank(unit: str) -> tuple[str, int, bool, str]:
        income_facts = [
            select_fact(payload, FIELD_CONCEPTS[field], as_of,
                        accepted_by_accession=accepted_by_accession, currency=unit)
            for field in ("revenue", "net_income")
        ]
        latest_income_period = max(
            (str(fact.get("period_end") or "") for fact in income_facts if fact), default=""
        )
        return latest_income_period, coverage[unit], unit == "USD", unit
    return max(eligible, key=currency_rank)


def extract_companyfacts(
    payload: dict[str, Any],
    as_of: str | datetime,
    *,
    accepted_by_accession: dict[str, str] | None = None,
) -> dict[str, Any]:
    cutoff, _ = _as_of(as_of)
    currency = reporting_currency(payload, cutoff, accepted_by_accession)
    fields: dict[str, Any] = {}
    unknown: list[dict[str, Any]] = []
    for field, concepts in FIELD_CONCEPTS.items():
        selected = select_fact(
            payload, concepts, cutoff, accepted_by_accession=accepted_by_accession,
            currency=currency,
        )
        if selected is None and field == "revenue":
            selected = bank_revenue_proxy(
                payload, cutoff, accepted_by_accession=accepted_by_accession, currency=currency
            )
        if selected is None and field == "revenue":
            selected = select_fact(
                payload, REVENUE_FALLBACK_CONCEPTS, cutoff, accepted_by_accession=accepted_by_accession,
                currency=currency,
            )
            if selected is not None:
                selected = {**selected, "fallback": True, "fallback_method": REVENUE_FALLBACK_METHOD}
        if selected is None:
            unknown.append(
                {
                    "field": field,
                    "reason_code": "provider_no_coverage",
                    "provider": "sec_edgar",
                    "as_of": cutoff.isoformat(),
                    "concepts_tried": list(concepts) + (list(REVENUE_FALLBACK_CONCEPTS) if field == "revenue" else []),
                }
            )
        else:
            fields[field] = selected
    body = {
        "schema": SCHEMA,
        "cik": payload.get("cik"),
        "entity_name": payload.get("entityName"),
        "as_of": cutoff.isoformat(),
        "provider": "sec_edgar",
        "authority": "primary_company_disclosure",
        "reporting_currency": currency,
        "point_in_time": True,
        "fields": fields,
        "unknown_events": unknown,
        "coverage": {
            "resolved": len(fields),
            "requested": len(FIELD_CONCEPTS),
            "pct": round(len(fields) / len(FIELD_CONCEPTS) * 100.0, 2),
        },
    }
    return {**body, "payload_sha256": digest(body)}


def merge_sec_primary(
    sec_result: dict[str, Any],
    supplementary: dict[str, Any],
    *,
    license_approved_for_distribution: bool = False,
) -> dict[str, Any]:
    """Shadow-merge FMP-like values while preserving SEC precedence."""
    merged: dict[str, Any] = {}
    conflicts: list[dict[str, Any]] = []
    sec_fields = sec_result.get("fields") or {}
    extra_values = supplementary.get("values") or {}
    all_fields = sorted(set(sec_fields) | set(extra_values))
    for field in all_fields:
        sec_fact = sec_fields.get(field)
        extra = extra_values.get(field)
        if sec_fact is not None:
            merged[field] = {"provider": "sec_edgar", **sec_fact}
            if extra is not None and extra != sec_fact.get("value"):
                conflicts.append(
                    {
                        "field": field,
                        "sec_value": sec_fact.get("value"),
                        "supplementary_value": extra,
                        "winner": "sec_edgar",
                        "silent_override": False,
                    }
                )
        elif extra is not None:
            merged[field] = {
                "provider": supplementary.get("provider", "supplementary"),
                "value": extra,
                "as_of": supplementary.get("as_of"),
                "shadow_only": not license_approved_for_distribution,
            }
    body = {
        "schema": "ecosystem.fundamental_fallback_shadow.v1",
        "values": merged,
        "conflicts": conflicts,
        "sec_precedence_enforced": True,
        "license_approved_for_distribution": license_approved_for_distribution,
        "promotion_eligible": license_approved_for_distribution,
    }
    return {**body, "payload_sha256": digest(body)}


def fetch_companyfacts(cik: int, user_agent: str, timeout_seconds: int = 20) -> dict[str, Any]:
    if not user_agent.strip():
        raise ValueError("SEC_USER_AGENT is required")
    request = urllib.request.Request(
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json",
        headers={"User-Agent": user_agent, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"SEC Companyfacts HTTP {exc.code}") from exc


def load_sec_user_agent(env_file: str | None) -> str:
    configured = os.getenv("SEC_USER_AGENT", "").strip()
    if configured or not env_file:
        return configured
    path = Path(env_file)
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != "SEC_USER_AGENT":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value.strip()
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Point-in-time SEC Companyfacts pilot")
    parser.add_argument("--input", help="Frozen Companyfacts JSON")
    parser.add_argument("--cik", type=int, help="Read-only SEC Companyfacts probe")
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--env-file", help="Workspace-local .env; credentials are never emitted")
    args = parser.parse_args()
    if bool(args.input) == bool(args.cik):
        parser.error("choose exactly one of --input or --cik")
    payload = (
        json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
        if args.input
        else fetch_companyfacts(args.cik, load_sec_user_agent(args.env_file))
    )
    result = extract_companyfacts(payload, args.as_of)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["coverage"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
