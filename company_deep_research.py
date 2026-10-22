"""Evidence-first deep company research for Petros candidates.

The agent is deterministic and local.  It never fetches data and never invents a
missing KPI.  It turns the evidence already captured by the research pipeline
into seven review modules and an explicit follow-up queue.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any


SCHEMA = "ecosystem.company_deep_research.v1"
ELIGIBLE_VERDICTS = {"candidate", "manual_review"}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected an object in {path}")
    return payload


def _index(payload: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("ticker") or "").upper(): row
        for row in payload.get(key) or []
        if isinstance(row, dict) and row.get("ticker")
    }


def _source(row: dict[str, Any], field: str) -> dict[str, Any]:
    base = row.get("source_base") or {}
    return {
        "field": field,
        "source_id": base.get("primary_data_feed") or "captured_pipeline_evidence",
        "source_as_of": base.get("snapshot_time"),
        "provider": base.get("fundamentals_provider"),
    }


def reverse_dcf_module(row: dict[str, Any]) -> dict[str, Any]:
    valuation = row.get("deterministic_valuation") or {}
    reverse = valuation.get("reverse_dcf") or {}
    result = {
        "status": reverse.get("status") or "not_assessed",
        "current_price": _number(row.get("share_price")),
        "implied_fcf_growth": reverse.get("implied_fcf_growth"),
        "discount_rate": reverse.get("discount_rate"),
        "terminal_growth": reverse.get("terminal_growth"),
        "growth_bounds": reverse.get("growth_bounds"),
        "interpretation": None,
        "source": _source(row, "share_price_and_dcf_inputs"),
    }
    growth = _number(result["implied_fcf_growth"])
    observed = _number(row.get("revenue_growth"))
    if growth is not None:
        comparison = "unavailable"
        if observed is not None:
            comparison = "above" if growth > observed else "at_or_below"
        result["interpretation"] = (
            f"The market price implies {growth:.1%} annual FCF growth under the recorded "
            f"discount assumptions; this is {comparison} the observed revenue growth."
        )
    elif result["status"] == "outside_solvable_range":
        result["interpretation"] = "The current deterministic bounds cannot reconcile the market price; assumptions require review."
    else:
        result["interpretation"] = "Required DCF inputs are unavailable."
    return result


def growth_driver_module(row: dict[str, Any]) -> dict[str, Any]:
    observed = {
        "revenue_growth": _number(row.get("revenue_growth")),
        "earnings_growth": _number(row.get("earnings_growth")),
    }
    driver_fields = {
        "customers": ("customer_count", "customers"),
        "units": ("managed_units", "units_managed"),
        "retention": ("retention_rate", "net_revenue_retention"),
        "pricing": ("pricing_growth", "average_revenue_per_customer_growth"),
    }
    drivers: dict[str, float] = {}
    for name, aliases in driver_fields.items():
        for alias in aliases:
            value = _number(row.get(alias))
            if value is not None:
                drivers[name] = value
                break
    missing = [name for name in driver_fields if name not in drivers]
    return {
        "status": "assessed" if not missing else "evidence_required",
        "observed_growth": observed,
        "observed_drivers": drivers,
        "missing_drivers": missing,
        "decomposition": "revenue_growth ~= customer_growth + expansion + pricing - churn",
        "next_action": "Extract missing operating drivers from dated 10-K, 10-Q, earnings releases, and investor presentations." if missing else None,
        "source": _source(row, "growth_inputs"),
    }


def margin_module(row: dict[str, Any]) -> dict[str, Any]:
    metrics = {
        "gross_margin": _number(row.get("gross_margin")),
        "operating_margin": _number(row.get("operating_margin")),
        "profit_margin": _number(row.get("profit_margin")),
        "free_cash_flow_ttm": _number(row.get("free_cash_flow_ttm") or row.get("cash_burn_proxy_fcf")),
        "operating_cash_flow_ttm": _number(row.get("operating_cashflow") or row.get("cash_burn_proxy_ocf")),
        "stock_based_compensation": _number(row.get("stock_based_compensation")),
    }
    available = {key: value for key, value in metrics.items() if value is not None}
    required_history = row.get("quarterly_margin_history")
    return {
        "status": "assessed_snapshot" if available else "not_assessed",
        "snapshot": available,
        "quarterly_history": required_history if isinstance(required_history, list) else [],
        "gaap_non_gaap_reconciled": bool(row.get("gaap_non_gaap_reconciled")),
        "limitations": [
            "A point-in-time margin does not establish durability.",
            "GAAP and adjusted metrics must be reconciled before a durability conclusion.",
        ],
        "next_action": "Build a 12-20 quarter margin series and reconcile stock compensation and adjusted metrics.",
        "source": _source(row, "margin_inputs"),
    }


def kpi_module(row: dict[str, Any]) -> dict[str, Any]:
    candidates = (
        "customer_count", "customers", "managed_units", "units_managed",
        "retention_rate", "net_revenue_retention", "revenue_per_customer",
        "subscription_revenue", "transaction_volume",
    )
    observations = []
    for field in candidates:
        value = row.get(field)
        if value not in (None, ""):
            observations.append({"kpi": field, "value": value, **_source(row, field)})
    return {
        "status": "observed" if observations else "evidence_required",
        "observations": observations,
        "required_kpis": ["customers", "managed_units", "retention", "adoption", "revenue_per_unit"],
        "missing_policy": "UNKNOWN; never infer undisclosed KPIs from revenue.",
        "next_action": "Create dated KPI time series from primary company disclosures.",
    }


def form4_module(row: dict[str, Any]) -> dict[str, Any]:
    evidence = row.get("catalyst_evidence") or []
    forms = [item for item in evidence if isinstance(item, dict) and "form_4" in str(item).lower()]
    transactions = []
    for item in forms:
        transaction_type = item.get("transaction_type") or item.get("code")
        transactions.append({
            "filed": item.get("filed") or item.get("date"),
            "owner": item.get("owner") or item.get("reporting_owner"),
            "transaction_type": transaction_type,
            "shares": item.get("shares"),
            "price": item.get("price"),
            "ten_b5_1": item.get("ten_b5_1"),
            "classification": item.get("classification") or "unresolved",
            "source": item.get("source") or "SEC Form 4",
        })
    resolved = all(item.get("classification") != "unresolved" for item in transactions)
    return {
        "status": "assessed" if transactions and resolved else ("classification_required" if transactions else "no_form4_detail"),
        "transactions": transactions,
        "required_classifications": ["open_market_purchase", "open_market_sale", "10b5-1", "option_exercise", "grant", "tax_withholding"],
        "direction": row.get("catalyst_direction") if transactions else None,
        "next_action": "Parse transaction codes, ownership after transaction, 10b5-1 flag, and percent of holdings from the filing." if not resolved else None,
    }


def thesis_module(row: dict[str, Any]) -> dict[str, Any]:
    revenue_growth = _number(row.get("revenue_growth"))
    operating_margin = _number(row.get("operating_margin"))
    fcf = _number(row.get("free_cash_flow_ttm") or row.get("cash_burn_proxy_fcf"))
    prove = []
    kill = []
    if revenue_growth is not None:
        prove.append({"metric": "revenue_growth", "operator": ">=", "threshold": round(max(revenue_growth * .8, 0), 4), "basis": "80% of observed run-rate"})
        kill.append({"metric": "revenue_growth", "operator": "<", "threshold": round(max(revenue_growth * .5, 0), 4), "basis": "material run-rate deterioration"})
    if operating_margin is not None:
        prove.append({"metric": "operating_margin", "operator": ">=", "threshold": round(operating_margin - .02, 4), "basis": "margin stability band"})
        kill.append({"metric": "operating_margin", "operator": "<", "threshold": round(operating_margin - .05, 4), "basis": "material margin compression"})
    if fcf is not None and fcf > 0:
        prove.append({"metric": "free_cash_flow_ttm", "operator": ">", "threshold": 0, "basis": "positive cash conversion"})
        kill.append({"metric": "free_cash_flow_ttm", "operator": "<=", "threshold": 0, "basis": "cash conversion failure"})
    kill.extend([
        {"metric": "primary_evidence_fresh", "operator": "==", "threshold": False, "basis": "stale or contradicted evidence"},
        {"metric": "material_dilution", "operator": "==", "threshold": True, "basis": "capital structure deterioration"},
    ])
    return {"status": "monitor", "prove_criteria": prove, "kill_criteria": kill,
            "review_cadence": "after each 10-Q/10-K and material 8-K", "execution_authority": False}


def valuation_zones_module(row: dict[str, Any]) -> dict[str, Any]:
    valuation = row.get("deterministic_valuation") or {}
    range_ = valuation.get("valuation_range") or row.get("valuation_range") or {}
    low, base, high = (_number(range_.get(key)) for key in ("low", "base", "high"))
    if any(value is None or value <= 0 for value in (low, base, high)):
        return {"status": "not_assessed", "reason": "validated_valuation_range_unavailable", "execution_authority": False}
    margin_of_safety = round(float(base) * .75, 4)
    return {
        "status": "assessed",
        "currency": row.get("currency") or "USD_unverified",
        "zones": {
            "margin_of_safety": {"max_price": margin_of_safety},
            "research_entry": {"min_price": margin_of_safety, "max_price": round(float(base), 4)},
            "watch": {"min_price": round(float(base), 4), "max_price": round(float(high), 4)},
            "above_model_range": {"min_price": round(float(high), 4)},
        },
        "limitations": ["Zones inherit all DCF assumptions.", "Currency and share-class consistency must be verified."],
        "execution_authority": False,
    }


def analyze(row: dict[str, Any]) -> dict[str, Any]:
    modules = {
        "reverse_dcf": reverse_dcf_module(row),
        "growth_drivers": growth_driver_module(row),
        "margin_durability": margin_module(row),
        "kpi_registry": kpi_module(row),
        "insider_form4": form4_module(row),
        "thesis_tracker": thesis_module(row),
        "valuation_zones": valuation_zones_module(row),
    }
    pending = [name for name, module in modules.items() if module.get("status") in {
        "not_assessed", "evidence_required", "classification_required", "no_form4_detail"
    }]
    return {
        "ticker": str(row.get("ticker") or "").upper(),
        "company": row.get("company"),
        "petros_verdict": row.get("petros_verdict"),
        "research_status": "deep_review_complete_with_gaps" if pending else "deep_review_complete",
        "modules": modules,
        "pending_research": pending,
        "source_snapshot": row.get("source_base"),
        "execution_authority": False,
    }


def build(petros: dict[str, Any], *, limit: int = 0) -> dict[str, Any]:
    eligible = [row for row in petros.get("results") or [] if isinstance(row, dict)
                and row.get("petros_verdict") in ELIGIBLE_VERDICTS]
    if limit > 0:
        eligible = eligible[:limit]
    results = [analyze(row) for row in eligible]
    return {
        "schema": SCHEMA,
        "run_id": petros.get("run_id"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selection_policy": {"verdicts": sorted(ELIGIBLE_VERDICTS), "limit": limit},
        "count": len(results),
        "results": results,
        "safety": {"research_only": True, "execution_authority": False, "missing_data_invented": False},
    }


def markdown(payload: dict[str, Any]) -> str:
    lines = ["# Company Deep Research", "", f"- Run ID: {payload.get('run_id')}",
             f"- Companies: {payload.get('count')}", "- Research only: yes", ""]
    for row in payload.get("results") or []:
        lines.extend([f"## {row['ticker']} — {row.get('company') or row['ticker']}", "",
                      f"- Petros verdict: {row.get('petros_verdict')}",
                      f"- Status: {row.get('research_status')}",
                      f"- Pending research: {', '.join(row.get('pending_research') or []) or 'none'}", ""])
        reverse = row["modules"]["reverse_dcf"]
        lines.append(f"- Reverse DCF: {reverse.get('interpretation')}")
        zones = row["modules"]["valuation_zones"]
        lines.append(f"- Valuation zones: {json.dumps(zones.get('zones'), ensure_ascii=False) if zones.get('zones') else zones.get('reason')}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--petros", default="output/petros_review_latest.json")
    parser.add_argument("--output", default="output/company_deep_research_latest.json")
    parser.add_argument("--markdown", default="output/company_deep_research_latest.md")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    payload = build(_load(Path(args.petros)), limit=max(args.limit, 0))
    output, md = Path(args.output), Path(args.markdown)
    output.parent.mkdir(parents=True, exist_ok=True)
    md.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md.write_text(markdown(payload), encoding="utf-8")
    print(json.dumps({"status": "ok", "run_id": payload.get("run_id"), "count": payload["count"],
                      "output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
