"""Build source-aware company and instrument profiles from stored ecosystem data.

This is a finite local-only synthesis step. It performs no network or LLM calls,
does not create financial conclusions, and preserves explicit unknown fields.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from control_plane_store import DEFAULT_CONTROL_PLANE_DB, connect as connect_control_plane
import research_control_plane
from research_registry.core import DEFAULT_DB, PROJECT, connect, digest
from sector_ecosystem_integration import schema as ecosystem_schema


RICH_PROVIDERS = {"fmp", "fmp_company_profile", "polygon", "polygon_ticker_reference"}
ROOT = PROJECT / "data" / "company_profiles"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def schema(db: sqlite3.Connection) -> None:
    ecosystem_schema(db)
    db.executescript("""
      CREATE TABLE IF NOT EXISTS company_profile_run(
        run_id TEXT PRIMARY KEY,snapshot_id TEXT NOT NULL,activation_id TEXT,
        status TEXT NOT NULL,total_count INTEGER NOT NULL,rich_count INTEGER NOT NULL,
        contextual_count INTEGER NOT NULL,identity_only_count INTEGER NOT NULL,
        instrument_count INTEGER NOT NULL,network_calls INTEGER NOT NULL,llm_calls INTEGER NOT NULL,
        created_at TEXT NOT NULL,finished_at TEXT NOT NULL,summary_json TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS company_profile_store(
        profile_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,snapshot_id TEXT NOT NULL,
        venue_code TEXT NOT NULL,ticker TEXT NOT NULL,profile_json TEXT NOT NULL,
        status TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(run_id,venue_code,ticker));
      CREATE INDEX IF NOT EXISTS company_profile_listing
        ON company_profile_store(snapshot_id,venue_code,ticker);
    """)


def entity_kind(instrument_type: str | None) -> str:
    value = str(instrument_type or "unknown").lower()
    if "etf" in value or "fund" in value:
        return "fund_or_exchange_traded_product"
    if "spac" in value or "blank" in value:
        return "acquisition_vehicle"
    if "adr" in value or "depositary" in value:
        return "depositary_receipt_listing"
    if "warrant" in value or "right" in value or "unit" in value:
        return "listed_security_or_unit"
    if value in {"equity", "equity_candidate", "common_stock"}:
        return "operating_company_candidate"
    return "listed_instrument"


def build_profile(listing: dict[str, Any], packet: dict[str, Any] | None,
                  snapshot_id: str, run_id: str) -> dict[str, Any]:
    payload = listing.get("payload") or {}
    raw = payload.get("raw_profile") or {}
    instrument_type = listing.get("instrument_type") or payload.get("instrument_type")
    kind = entity_kind(instrument_type)
    packet = packet or {}
    sources = list(packet.get("source_references") or [])
    business_model = str(packet.get("business_model") or "").strip() or None
    rich = bool(business_model and any(str(source.get("provider") or "").lower() in RICH_PROVIDERS for source in sources))
    raw_sector = str(raw.get("sector") or payload.get("raw_sector") or "").strip() or None
    raw_industry = str(raw.get("industry") or payload.get("raw_industry") or "").strip() or None
    profile_url = str(raw.get("url") or "").strip() or None
    if profile_url and profile_url.startswith("/"):
        profile_url = "https://www.nasdaq.com" + profile_url
    if profile_url and not any(source.get("url") == profile_url for source in sources):
        sources.append({"provider": "exchange_bulk_profile", "as_of": None,
                        "url": profile_url, "evidence_label": "fact_provider_standardized"})

    if rich:
        status = "rich_profile"
        overview = business_model
    elif business_model:
        status = "contextual_profile"
        overview = business_model
    elif raw_industry or raw_sector:
        status = "contextual_profile"
        context = " / ".join(value for value in (raw_sector, raw_industry) if value)
        overview = (
            f"The stored exchange profile places this listing in {context}. "
            "This is classification context, not a verified description of its revenue mix."
        )
    else:
        status = "identity_only"
        overview = (
            f"This listing is currently identified as {kind.replace('_', ' ')}. "
            "A verified operating description is not yet available in the stored evidence."
        )

    unknown = []
    if not rich:
        unknown.append("verified_company_description")
    for field, value in (("products_services", []), ("headquarters", None),
                         ("website", None), ("founded", None)):
        if not value:
            unknown.append(field)
    if not raw.get("country"):
        unknown.append("country")

    profile = {
        "schema": "ecosystem.company_profile.v1",
        "profile_id": "company_profile_" + digest([run_id, listing["venue_code"], listing["ticker"]])[:24],
        "agent_id": "company_profile_agent",
        "run_id": run_id,
        "snapshot_id": snapshot_id,
        "status": status,
        "ticker": listing["ticker"],
        "venue_code": listing["venue_code"],
        "venue": payload.get("venue"),
        "display_name": listing.get("name") or payload.get("name"),
        "instrument_type": instrument_type,
        "entity_kind": kind,
        "country": raw.get("country"),
        "ipo_year": raw.get("ipoyear"),
        "provider_sector": raw_sector,
        "provider_industry": raw_industry,
        "taxonomy_node": listing.get("node_id"),
        "overview": overview,
        "business_model": business_model,
        "products_services": [],
        "headquarters": None,
        "website": None,
        "founded": None,
        "source_references": sources,
        "unknown_fields": unknown,
        "financial_analysis": "not_part_of_company_profile",
        "created_at": now(),
    }
    return profile


def publish_profile(
    db: sqlite3.Connection, profile: dict[str, Any], *, canonical_subject_id: str | None = None,
) -> dict[str, Any]:
    """Publish one stored profile as source-aware, resumable control-plane data."""
    research_control_plane.init_schema(db)
    run_id = str(profile.get("run_id") or "").strip()
    snapshot_id = str(profile.get("snapshot_id") or "").strip()
    venue_code = str(profile.get("venue_code") or "").strip()
    ticker = str(profile.get("ticker") or "").upper().strip()
    if not all((run_id, snapshot_id, venue_code, ticker)):
        raise ValueError("Company profile lacks run/listing identity for control-plane publishing.")
    listing_subject_id = f"listing:{snapshot_id}:{venue_code}:{ticker}"
    subject_id = str(canonical_subject_id or listing_subject_id).strip()
    research_control_plane.register_subject_alias(
        db, alias_subject_id=listing_subject_id, canonical_subject_id=subject_id,
        alias_kind="listing", source="company_profile_agent",
    )
    source_ref = f"{snapshot_id}/{venue_code}/{ticker}"
    observation, _ = research_control_plane.record_observation(
        db,
        subject_id=listing_subject_id,
        capability="company.profile_input",
        provider="ecosystem_registry",
        source_ref=source_ref,
        observed_at=str(profile.get("created_at") or now()),
        as_of=snapshot_id,
        run_id=run_id,
        status="available",
        payload={
            "display_name": profile.get("display_name"),
            "instrument_type": profile.get("instrument_type"),
            "taxonomy_node": profile.get("taxonomy_node"),
            "source_references": profile.get("source_references", []),
            "unknown_fields": profile.get("unknown_fields", []),
        },
    )
    inputs = [("observation", observation["observation_id"])]
    fingerprint = research_control_plane.input_fingerprint(
        stage="company_profile",
        subject_id=subject_id,
        inputs=inputs,
        config={"profile_schema": profile.get("schema"), "profile_status": profile.get("status")},
        implementation_version="company_profile_agent.v1",
    )
    checkpoint = research_control_plane.acquire_checkpoint(
        db,
        run_id=run_id,
        stage="company_profile",
        subject_id=subject_id,
        fingerprint=fingerprint,
        details={"profile_id": profile.get("profile_id")},
    )
    if checkpoint["status"] == "reused":
        return {
            "observation_id": observation["observation_id"],
            "control_packet_id": checkpoint["output_packet_id"],
            "checkpoint_id": checkpoint["checkpoint_id"],
            "status": "reused",
        }
    packet, created = research_control_plane.build_packet(
        db,
        kind="company_profile",
        subject_id=subject_id,
        run_id=run_id,
        producer_name="company_profile_agent",
        producer_version="v1",
        as_of=snapshot_id,
        quality_state="ready" if profile.get("status") == "rich_profile" else "conditional",
        inputs=inputs,
        payload=profile,
    )
    research_control_plane.set_packet_freshness(
        db, packet_id=packet["packet_id"], knowledge_date=str(profile.get("created_at") or now()),
        stale_after=None,
        must_refresh_if=("new_issuer_filing", "listing_identity_change", "new_company_profile_source"),
        freshness_state="current" if profile.get("status") == "rich_profile" else "needs_refresh",
    )
    result = research_control_plane.complete_checkpoint(db, checkpoint["checkpoint_id"], packet)
    return {
        "observation_id": observation["observation_id"],
        "control_packet_id": packet["packet_id"],
        "checkpoint_id": result["checkpoint_id"],
        "status": result["status"],
        "created": created,
    }


def publish_profile_to_control_plane(
    profile: dict[str, Any], db_path: Path = DEFAULT_CONTROL_PLANE_DB,
    *, canonical_subject_id: str | None = None,
) -> dict[str, Any]:
    """Publish a profile to the ecosystem-wide packet store, not the registry DB."""
    db = connect_control_plane(db_path)
    try:
        return publish_profile(db, profile, canonical_subject_id=canonical_subject_id)
    finally:
        db.close()


def build(
    db_path: Path = DEFAULT_DB,
    control_plane_db: Path = DEFAULT_CONTROL_PLANE_DB,
) -> dict[str, Any]:
    db = connect(db_path)
    try:
        schema(db)
        activation = db.execute(
            "SELECT activation_id,snapshot_id FROM sector_activation_run ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not activation:
            raise ValueError("No sector activation exists")
        activation_id, snapshot_id = activation[0], activation[1]
        run_id = "company-profiles-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        packets = {}
        for row in db.execute(
            "SELECT q.venue_code,q.ticker,p.packet_json FROM sector_context_packet_store p "
            "JOIN sector_analysis_queue q ON q.queue_id=p.queue_id WHERE p.activation_id=?",
            (activation_id,),
        ):
            packets[(row["venue_code"], row["ticker"])] = json.loads(row["packet_json"])
        rows = []
        for row in db.execute(
            "SELECT venue_code,ticker,name,instrument_type,node_id,payload_json "
            "FROM effective_listed_symbol WHERE snapshot_id=? ORDER BY venue_code,ticker",
            (snapshot_id,),
        ):
            listing = dict(row)
            listing["payload"] = json.loads(listing.pop("payload_json") or "{}")
            rows.append(build_profile(listing, packets.get((row["venue_code"], row["ticker"])), snapshot_id, run_id))
        control_plane_counts = Counter()
        control_db = connect_control_plane(control_plane_db)
        try:
            for profile in rows:
                control_plane = publish_profile(control_db, profile)
                profile["control_plane"] = control_plane
                control_plane_counts[control_plane["status"]] += 1
        finally:
            control_db.close()
        counts = Counter(profile["status"] for profile in rows)
        instruments = sum(profile["entity_kind"] != "operating_company_candidate" for profile in rows)
        created_at = now()
        summary = {
            "run_id": run_id, "snapshot_id": snapshot_id, "activation_id": activation_id,
            "status": "finished_with_explicit_unknowns", "total_profiles": len(rows),
            "rich_profiles": counts["rich_profile"], "contextual_profiles": counts["contextual_profile"],
            "identity_only_profiles": counts["identity_only"], "instrument_profiles": instruments,
            "network_calls": 0, "llm_calls": 0, "created_at": created_at, "finished_at": created_at,
            "control_plane_packets": len(rows),
            "control_plane_status": dict(sorted(control_plane_counts.items())),
        }
        with db:
            db.execute("INSERT INTO company_profile_run VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                run_id, snapshot_id, activation_id, summary["status"], len(rows), counts["rich_profile"],
                counts["contextual_profile"], counts["identity_only"], instruments, 0, 0,
                created_at, created_at, json.dumps(summary, ensure_ascii=False, sort_keys=True),
            ))
            db.executemany("INSERT INTO company_profile_store VALUES(?,?,?,?,?,?,?,?)", [
                (profile["profile_id"], run_id, snapshot_id, profile["venue_code"], profile["ticker"],
                 json.dumps(profile, ensure_ascii=False, sort_keys=True), profile["status"], created_at)
                for profile in rows
            ])
        folder = ROOT / run_id
        folder.mkdir(parents=True, exist_ok=False)
        (folder / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        report = "\n".join([
            "# Company Profile Agent production report", "", f"Run: `{run_id}`",
            f"Status: `{summary['status']}`", "", "## Coverage", "",
            f"- Total profiles: **{summary['total_profiles']}**",
            f"- Rich profiles: **{summary['rich_profiles']}**",
            f"- Contextual profiles: **{summary['contextual_profiles']}**",
            f"- Identity-only profiles: **{summary['identity_only_profiles']}**",
            f"- Instrument profiles: **{summary['instrument_profiles']}**", "",
            "## Resource usage", "", "- Network calls: **0**", "- LLM calls: **0**", "",
            "## Information control plane", "",
            f"- Published Company Profile packets: **{summary['control_plane_packets']}**",
            f"- Packet states: `{summary['control_plane_status']}`", "",
            "## Governance", "",
            "Every profile preserves explicit unknown fields. Company descriptions are reused only when stored source-backed evidence exists; instrument listings are not presented as operating companies.",
        ]) + "\n"
        (folder / "REPORT.md").write_text(report, encoding="utf-8")
        (ROOT / "latest.json").write_text(
            json.dumps({"run_id": run_id, "run": str(folder)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return summary
    finally:
        db.close()


def status(db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    db = connect(db_path)
    try:
        schema(db)
        row = db.execute("SELECT summary_json FROM company_profile_run ORDER BY created_at DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else {"status": "not_started"}
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "status"))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--control-plane-db", type=Path, default=DEFAULT_CONTROL_PLANE_DB)
    args = parser.parse_args(argv)
    result = build(args.db, args.control_plane_db) if args.action == "build" else status(args.db)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
