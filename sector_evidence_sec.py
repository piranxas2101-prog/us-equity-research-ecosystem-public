"""Sector evidence from SEC data the ecosystem already stores: XBRL sector KPIs and a same-SIC peer group.

The sector agents need sourced evidence before they can say anything; today every sector packet is empty. This
module fills the part the SEC answers deterministically, with no model and no network:

- ``sector_kpis``: a few KPIs that are reported XBRL facts (inventory, remaining performance obligations, insurance
  loss reserves, net investment income) or simple calculations on them (premium growth, stock-based compensation as a
  share of revenue, cash runway). Each value names its concept, period and filing.
- ``peer_group``: other operating companies with the same four-digit SEC SIC code (basis ``same_sec_sic_code``).
  Catch-all and blank-check codes give no peer group.

Unknown stays unknown: a fact older than the age limit, a missing concept or an ambiguous calculation leaves the KPI
out, and the sector agent keeps it in ``unknown_fields``. Nothing here is an interpretation of the business.
"""
from __future__ import annotations

from datetime import date
import gzip
import hashlib
import json
from pathlib import Path
import re
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_COMPANYFACTS_DIR = ROOT / "cache" / "sec_companyfacts"
PERIODIC_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A"}
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
MAX_INSTANT_AGE_DAYS = 456   # ~15 months: one missed quarterly report is tolerated, an old balance is not
MAX_ANNUAL_AGE_DAYS = 548    # ~18 months: the latest fiscal year
MAX_PEERS = 12
REVENUE_CONCEPTS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                    "RevenueFromContractWithCustomerIncludingAssessedTax")
CATCH_ALL_SIC = re.compile(r"\bNEC\b|\bmisc(ellaneous)?\b|non-?operating", re.IGNORECASE)
NO_PEER_SIC = {"6770", "9995"}

# (sector, subcategory, kpi) -> how to obtain it
INSTANT_KPIS = {
    ("technology", "software", "remaining_performance_obligations"): "RevenueRemainingPerformanceObligation",
    ("technology", "semiconductors", "inventory"): "InventoryNet",
    ("consumer_discretionary", "goods_retail", "inventory"): "InventoryNet",
    ("financials", "insurance", "loss_reserves"): "LiabilityForClaimsAndClaimsAdjustmentExpense",
}
ANNUAL_KPIS = {
    ("financials", "insurance", "investment_income"): "NetInvestmentIncome",
}
KPI_SUBCATEGORIES = {key[:2] for key in (*INSTANT_KPIS, *ANNUAL_KPIS)} | {
    ("financials", "insurance"), ("technology", "software"), ("healthcare", "therapeutics")}


def load_companyfacts(cik: int, directory: Path = DEFAULT_COMPANYFACTS_DIR) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The stored companyfacts document and its meta, only when the stored hash matches."""
    stem = f"CIK{int(cik):010d}"
    try:
        meta = json.loads((Path(directory) / f"{stem}.meta.json").read_text(encoding="utf-8"))
        body = gzip.decompress((Path(directory) / f"{stem}.json.gz").read_bytes())
    except (OSError, ValueError):
        return None
    if hashlib.sha256(body).hexdigest() != meta.get("sha256"):
        return None
    return json.loads(body), meta


def _facts(doc: dict[str, Any], concept: str) -> list[dict[str, Any]]:
    units = (((doc.get("facts") or {}).get("us-gaap") or {}).get(concept) or {}).get("units") or {}
    return [dict(row, concept=concept) for row in units.get("USD") or [] if isinstance(row, dict)
            and isinstance(row.get("val"), (int, float)) and row.get("end") and row.get("form") in PERIODIC_FORMS]


def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def latest_instant(doc: dict[str, Any], concept: str, today: date) -> dict[str, Any] | None:
    rows = [row for row in _facts(doc, concept) if not row.get("start")]
    if not rows:
        return None
    best = max(rows, key=lambda row: (row["end"], row.get("filed") or ""))
    return best if _days(best["end"], today.isoformat()) <= MAX_INSTANT_AGE_DAYS else None


def annual_values(doc: dict[str, Any], concept: str, today: date) -> list[dict[str, Any]]:
    """Fiscal-year values (330-400 day periods from annual reports), newest first, one per period end."""
    rows = [row for row in _facts(doc, concept) if row.get("start") and row.get("form") in ANNUAL_FORMS
            and 330 <= _days(row["start"], row["end"]) <= 400]
    by_end: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=lambda row: row.get("filed") or ""):
        by_end[row["end"]] = row  # the latest filing of a period wins (restatements)
    ordered = [by_end[end] for end in sorted(by_end, reverse=True)]
    if not ordered or _days(ordered[0]["end"], today.isoformat()) > MAX_ANNUAL_AGE_DAYS:
        return []
    return ordered


def _annual_revenue(doc: dict[str, Any], end: str, today: date) -> dict[str, Any] | None:
    for concept in REVENUE_CONCEPTS:
        for row in annual_values(doc, concept, today):
            if row["end"] == end:
                return row
    return None


def _kpi(value: Any, basis: str, facts: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"value": value, "unit": extra.pop("unit", "USD"), "basis": basis,
            "period_end": facts[0]["end"], "concepts": [fact["concept"] for fact in facts],
            "accessions": sorted({str(fact.get("accn")) for fact in facts if fact.get("accn")}), **extra}


def sector_kpis(doc: dict[str, Any], sector: str, subcategory: str, today: date) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """KPIs for the node's framework and the facts behind them."""
    kpis: dict[str, Any] = {}
    used: list[dict[str, Any]] = []
    for (kpi_sector, kpi_sub, kpi), concept in INSTANT_KPIS.items():
        if (kpi_sector, kpi_sub) == (sector, subcategory):
            fact = latest_instant(doc, concept, today)
            if fact:
                kpis[kpi] = _kpi(fact["val"], "fact_source_reported", [fact])
                used.append(fact)
    for (kpi_sector, kpi_sub, kpi), concept in ANNUAL_KPIS.items():
        if (kpi_sector, kpi_sub) == (sector, subcategory):
            years = annual_values(doc, concept, today)
            if years:
                kpis[kpi] = _kpi(years[0]["val"], "fact_source_reported", [years[0]], period_start=years[0]["start"])
                used.append(years[0])
    if (sector, subcategory) == ("financials", "insurance"):
        years = annual_values(doc, "PremiumsEarnedNet", today)
        if len(years) >= 2 and years[1]["val"] > 0 and 330 <= _days(years[1]["end"], years[0]["end"]) <= 400:
            growth = round((years[0]["val"] / years[1]["val"] - 1.0) * 100.0, 2)
            kpis["premium_growth"] = _kpi(growth, "derived_calculation", years[:2], unit="percent_yoy",
                                          formula="PremiumsEarnedNet latest fiscal year / prior fiscal year - 1")
            used += years[:2]
    if (sector, subcategory) == ("technology", "software"):
        sbc = annual_values(doc, "ShareBasedCompensation", today)
        revenue = _annual_revenue(doc, sbc[0]["end"], today) if sbc else None
        if sbc and revenue and revenue["val"] > 0:
            share = round(sbc[0]["val"] / revenue["val"] * 100.0, 2)
            kpis["stock_based_compensation_context"] = _kpi(
                {"stock_based_compensation_usd": sbc[0]["val"], "share_of_revenue_pct": share},
                "derived_calculation", [sbc[0], revenue], unit="mixed",
                formula="ShareBasedCompensation / revenue, same fiscal year")
            used += [sbc[0], revenue]
    if (sector, subcategory) == ("healthcare", "therapeutics"):
        cash = latest_instant(doc, "CashAndCashEquivalentsAtCarryingValue", today)
        flow = annual_values(doc, "NetCashProvidedByUsedInOperatingActivities", today)
        if cash and flow:
            burn = -flow[0]["val"]
            context = ({"cash_usd": cash["val"], "annual_operating_cash_burn_usd": burn,
                        "runway_years": round(cash["val"] / burn, 2)} if burn > 0 else
                       {"cash_usd": cash["val"], "operating_cash_flow_usd": flow[0]["val"], "runway_years": None,
                        "note": "operating cash flow is positive: no burn"})
            kpis["cash_runway_context"] = _kpi(context, "derived_calculation", [cash, flow[0]], unit="mixed",
                                               formula="cash and equivalents / last fiscal year operating cash burn")
            used += [cash, flow[0]]
    return kpis, used


def peer_group(ticker: str, sec_by_ticker: dict[str, dict[str, Any]],
               preferred: set[str] | None = None) -> tuple[list[str], int] | None:
    """Operating companies with the same four-digit SIC, one ticker per issuer, and how many there are.

    None when the code carries no industry meaning or fewer than two peers exist. Issuers in ``preferred`` (the
    shortlist being researched) are listed first, so the list names companies the ecosystem also studies.
    """
    own = sec_by_ticker.get(ticker.upper()) or {}
    sic = str(own.get("sic") or "")
    if not sic or sic in NO_PEER_SIC or CATCH_ALL_SIC.search(str(own.get("sic_description") or "")):
        return None
    by_issuer: dict[str, str] = {}
    for other, record in sec_by_ticker.items():
        cik = str(record.get("cik") or other)
        if (str(record.get("sic") or "") != sic or cik == str(own.get("cik"))
                or "operating" not in str(record.get("entity_type") or "operating").lower()):
            continue
        current = by_issuer.get(cik)
        # one ticker per issuer: the shortest (the common share, not a preferred or note series)
        if current is None or (len(other), other) < (len(current), current):
            by_issuer[cik] = other
    peers = sorted(by_issuer.values(), key=lambda peer: (preferred is not None and peer not in preferred, peer))
    return (peers[:MAX_PEERS], len(peers)) if len(peers) >= 2 else None


def build_evidence(*, ticker: str, cik: int, leaf_node: str, sec_by_ticker: dict[str, dict[str, Any]],
                   preferred: set[str] | None = None, today: date | None = None,
                   companyfacts_dir: Path = DEFAULT_COMPANYFACTS_DIR) -> dict[str, Any]:
    """Evidence fields for the sector agent input: values, their source references and claim sources."""
    today = today or date.today()
    parts = str(leaf_node or "").split(".")
    evidence: dict[str, Any] = {"source_references": [], "claim_sources": {}}
    if len(parts) < 2:
        return evidence
    stored = load_companyfacts(cik, companyfacts_dir) if (parts[0], parts[1]) in KPI_SUBCATEGORIES else None
    if stored:
        doc, meta = stored
        kpis, facts = sector_kpis(doc, parts[0], parts[1], today)
        if kpis:
            source_ids = []
            for accn in sorted({str(fact.get("accn")) for fact in facts if fact.get("accn")}):
                filed = max(str(fact.get("filed") or "") for fact in facts if str(fact.get("accn")) == accn)
                source_id = f"sec_companyfacts:{int(cik):010d}:{accn}"
                source_ids.append(source_id)
                evidence["source_references"].append({
                    "source_id": source_id, "provider": "sec_companyfacts", "as_of": filed or meta.get("fetched_date_et"),
                    "evidence_label": "fact_source_reported", "accession": accn,
                    "url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json",
                    "document_sha256": meta.get("sha256")})
            evidence["sector_kpis"] = kpis
            evidence["claim_sources"]["sector_kpis"] = source_ids
    found = peer_group(ticker, sec_by_ticker, preferred)
    record = sec_by_ticker.get(ticker.upper()) or {}
    if found and record.get("observed_at"):
        peers, total = found
        source_id = f"sec_submissions:{str(record.get('cik') or cik).zfill(10)}"
        evidence["source_references"].append({
            "source_id": source_id, "provider": "sec_submissions", "as_of": str(record["observed_at"])[:10],
            "evidence_label": "fact_source_reported", "sic": record.get("sic"),
            "sic_description": record.get("sic_description"), "evidence_sha256": record.get("hash")})
        evidence["peer_group"] = [{"ticker": peer, "basis": "same_sec_sic_code", "sic": record.get("sic"),
                                   "issuers_with_same_sic": total} for peer in peers]
        evidence["claim_sources"]["peer_group"] = [source_id]
    return evidence


def catalyst_evidence(row: dict[str, Any]) -> dict[str, Any]:
    """Recent content-bearing SEC events (8-K items) the ecosystem already recorded for the company, with sources."""
    events, sources = [], []
    for event in row.get("catalyst_evidence") or []:
        if not isinstance(event, dict) or event.get("source_kind") != "sec_item" or not event.get("source_url"):
            continue
        source_id = "sec_filing:" + str(event["source_url"])
        if source_id in {s["source_id"] for s in sources}:
            continue
        events.append({"event": event.get("event_name"), "date": event.get("event_date"), "form": event.get("sec_form"),
                       "item": event.get("sec_item"), "category": event.get("category_id"),
                       "direction": event.get("direction"), "source_id": source_id})
        sources.append({"source_id": source_id, "provider": "sec_edgar_filing", "as_of": event.get("event_date"),
                        "evidence_label": "fact_source_reported", "url": event["source_url"]})
    if not events or any(not source["as_of"] for source in sources):
        return {}
    return {"catalysts": events, "claim_sources": {"catalysts": [s["source_id"] for s in sources]},
            "source_references": sources}
