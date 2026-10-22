"""Refresh and reconcile the complete listed-symbol universe before a full run.

This preparation step contacts only the configured public Nasdaq/SEC reference
sources. It does not fetch market snapshots, start agents, or contact a broker.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from research_registry import acquire_universe
from research_registry.classification_governance import carry_forward_overrides
from research_registry.classification_reassessment import daily_apply as reassessment_daily_apply
from research_registry.core import DEFAULT_DB, connect
from research_registry.full_universe import build as build_catalog
from research_registry.instrument_links import build as build_instrument_links
from research_registry import sec_filing_index
from research_workflow_orchestrator import load_env
from us_equity_monitor import load_all_listed_routes, load_us_universe_from_sec


ROOT = Path(__file__).resolve().parent


def build_route_manifest(
    *, routes: dict[str, dict[str, Any]], snapshot_id: str | None,
    eligible_symbols: list[str], output_path: Path,
) -> dict[str, Any]:
    entries = [routes[ticker] for ticker in sorted(routes)]
    company_count = sum(bool(row.get("company_fundamental_eligible")) for row in entries)
    eligible_set = {str(ticker).upper() for ticker in eligible_symbols}
    routed_sec_tickers = {
        str(row.get("sec_eligibility_ticker") or "").upper()
        for row in entries if row.get("sec_eligibility_ticker")
    }
    not_currently_listed = sorted(eligible_set - routed_sec_tickers)
    payload = {
        "schema": "ecosystem.all_listed_universe_routes.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_id": snapshot_id,
        "listed_symbol_count": len(entries),
        "eligible_company_symbols": len(eligible_set),
        "company_fundamental_route_count": company_count,
        "eligible_company_not_listed_count": len(not_currently_listed),
        "eligible_company_not_listed": not_currently_listed,
        "instrument_observation_route_count": len(entries) - company_count,
        "route_counts": dict(sorted(Counter(str(row["research_route"]) for row in entries).items())),
        "instrument_type_counts": dict(sorted(Counter(str(row["instrument_type"]) for row in entries).items())),
        "routes": entries,
    }
    if (
        company_count != len(routed_sec_tickers)
        or not routed_sec_tickers.issubset(eligible_set)
        or company_count + len(not_currently_listed) != len(eligible_set)
    ):
        raise ValueError(
            "Company route reconciliation failed: "
            f"routed={company_count}, not_listed={len(not_currently_listed)}, eligible={len(eligible_set)}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def sync_filing_index(db: Any) -> dict[str, Any]:
    try:
        load_env(ROOT / ".env")  # SEC_USER_AGENT, never printed
        agent = os.getenv("SEC_USER_AGENT", "").strip()
        if not agent:
            return {"status": "not_configured"}
        result = sec_filing_index.sync(db, sec_filing_index.default_fetch(agent), today=sec_filing_index.sec_today())
        return {"status": "ok" if not result["failed"] else "partial", "days_read": len(result["read"]),
                "events_added": result["events_added"], "not_yet_published": result["not_yet_published"],
                "failed": result["failed"]}
    except Exception as exc:  # noqa: BLE001 - surfaced in the result, research continues
        return {"status": "failed", "error_type": type(exc).__name__, "error": str(exc)[:300]}


def prepare(root: Path, *, refresh_sources: bool) -> dict[str, Any]:
    if refresh_sources:
        acquire_universe.main()
    manifest = json.loads(
        (root / "data/research_universe_sources/manifest.json").read_text(encoding="utf-8-sig")
    )
    if len(manifest) != 3 or any(row.get("status") != "ok" for row in manifest):
        raise ValueError("All three listed-symbol source snapshots must be available")

    db = connect(DEFAULT_DB)
    try:
        coverage = build_catalog(db)
        # Approved classification corrections are stored per snapshot; carry them
        # onto the snapshot just built so they keep applying. A failure here is
        # reported but does not block the research run.
        try:
            carry = carry_forward_overrides(db, coverage["snapshot_id"])
            carry_summary = {"status": "ok", "carried": carry["carried"], "already_current": carry["already_current"],
                             "base_changed_needs_review": len(carry["base_changed_needs_review"]),
                             "listing_missing": len(carry["listing_missing"]), "batch_id": carry["batch_id"]}
        except Exception as exc:  # noqa: BLE001 - surfaced in the result, research continues
            carry_summary = {"status": "failed", "error_type": type(exc).__name__, "error": str(exc)[:300]}
        # Then re-decide classifications from the local evidence (no network, no
        # model): only changes that independent sources agree on are applied, as
        # a reversible batch under policy automatic_agreement.
        try:
            reassess_summary = reassessment_daily_apply(db, coverage["snapshot_id"], report_dir=root / "output")
        except Exception as exc:  # noqa: BLE001 - surfaced in the result, research continues
            reassess_summary = {"status": "failed", "error_type": type(exc).__name__, "error": str(exc)[:300]}
        # Link warrants/preferreds/units/rights of the new snapshot to their issuer. Without
        # links every instrument is simply scanned on its own, so a failure does not block.
        try:
            links = build_instrument_links(db, snapshot=coverage["snapshot_id"])
            links_summary = {"status": "ok", **{k: links[k] for k in ("instruments", "linked", "unresolved_by_reason")}}
        except Exception as exc:  # noqa: BLE001 - surfaced in the result, research continues
            links_summary = {"status": "failed", "error_type": type(exc).__name__, "error": str(exc)[:300]}
        # Read the SEC daily filing index for the days not yet recorded. Evidence reuse
        # relies on it; days that could not be read stay uncovered, so nothing is reused
        # across them. A failure does not block the run.
        filing_index_summary = sync_filing_index(db) if refresh_sources else {"status": "skipped"}
    finally:
        db.close()

    config = json.loads((root / "scanner_config.json").read_text(encoding="utf-8-sig"))
    universe_cfg = dict(config.get("universe") or {})
    if refresh_sources:
        universe_cfg["cache_max_age_hours"] = 0
    eligible = load_us_universe_from_sec(universe_cfg)
    routes, snapshot_id = load_all_listed_routes(universe_cfg)
    if len(routes) != int(coverage.get("unique_symbols") or 0):
        raise ValueError(
            f"Listed route reconciliation failed: routes={len(routes)}, catalog={coverage.get('unique_symbols')}"
        )
    route_manifest = build_route_manifest(
        routes=routes, snapshot_id=snapshot_id, eligible_symbols=eligible,
        output_path=root / "output/all_listed_universe_routes_latest.json",
    )
    return {
        "status": "ready",
        "snapshot_id": snapshot_id,
        "listed_symbols": len(routes),
        "eligible_company_symbols": len(eligible),
        "instrument_observation_symbols": route_manifest["instrument_observation_route_count"],
        "route_counts": route_manifest["route_counts"],
        "classification_carry_forward": carry_summary,
        "classification_reassessment": reassess_summary,
        "instrument_links": links_summary,
        "sec_filing_index": filing_index_summary,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--refresh-sources", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = prepare(args.root.resolve(), refresh_sources=args.refresh_sources)
    except Exception as exc:
        result = {"status": "blocked", "error_type": type(exc).__name__, "error": str(exc)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
