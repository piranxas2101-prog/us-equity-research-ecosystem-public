"""Enrich sector packets from already stored evidence, without new API calls.

The runner reuses Babis SEC metadata, prior FMP/Polygon profile evidence and
the immutable exchange-profile snapshot. It creates source-backed business
context and deterministic taxonomy peer candidates. Unsupported KPIs, risks,
drivers and catalysts remain explicit unknowns.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

from control_plane_store import connect as connect_control_plane
import research_control_plane

from research_registry.core import DEFAULT_DB, PROJECT, connect, digest
from sector_ecosystem_integration import schema as ecosystem_schema
from sector_research_agent import analyze_input


ROOT = PROJECT / "data" / "sector_agent_evidence_runs"
ELIGIBLE_STATUSES = ("sector_context_review_required", "sector_context_local_evidence_review_required")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def schema(db: sqlite3.Connection) -> None:
    ecosystem_schema(db)
    db.executescript("""
      CREATE TABLE IF NOT EXISTS sector_agent_evidence_run(
        run_id TEXT PRIMARY KEY,activation_id TEXT NOT NULL,status TEXT NOT NULL,
        total_count INTEGER NOT NULL,processed_count INTEGER NOT NULL,
        rich_description_count INTEGER NOT NULL,profile_context_count INTEGER NOT NULL,
        peer_group_count INTEGER NOT NULL,review_required_count INTEGER NOT NULL,
        failed_count INTEGER NOT NULL,network_calls INTEGER NOT NULL,llm_calls INTEGER NOT NULL,
        created_at TEXT NOT NULL,updated_at TEXT NOT NULL,finished_at TEXT,
        summary_json TEXT NOT NULL);
    """)


def _latest_activation(db: sqlite3.Connection) -> str:
    row = db.execute(
        "SELECT activation_id FROM sector_activation_run ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        raise ValueError("No sector activation exists")
    return row[0]


def prepare(activation_id: str | None = None, db_path: Path = DEFAULT_DB,
            root: Path = ROOT) -> Path:
    db = connect(db_path)
    try:
        schema(db)
        activation_id = activation_id or _latest_activation(db)
        activation = db.execute(
            "SELECT babis_pipeline_job,snapshot_id FROM sector_activation_run WHERE activation_id=?",
            (activation_id,),
        ).fetchone()
        if not activation:
            raise ValueError(f"Unknown activation: {activation_id}")
        total = db.execute(
            "SELECT COUNT(*) FROM sector_analysis_queue WHERE activation_id=? AND status IN (?,?)",
            (activation_id, *ELIGIBLE_STATUSES),
        ).fetchone()[0]
        if not total:
            raise ValueError("No sector packets are eligible for local evidence enrichment")
        pipeline_state = json.loads((Path(activation[0]) / "state.json").read_text(encoding="utf-8"))
        sec_results = Path(pipeline_state["sec_job"]) / "results.sqlite3"
        enrichment_results = Path(pipeline_state["enrichment_job"]) / "results.sqlite3"
        if not sec_results.exists():
            raise ValueError("Babis SEC result store is missing")
        run_id = "sector-evidence-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        folder = Path(root) / run_id
        folder.mkdir(parents=True, exist_ok=False)
        payload = {
            "run_id": run_id, "activation_id": activation_id, "snapshot_id": activation[1],
            "babis_pipeline_job": str(activation[0]), "sec_results": str(sec_results),
            "enrichment_results": str(enrichment_results) if enrichment_results.exists() else None,
            "total_symbols": total, "created_at": now(),
            "policy": {
                "stored_evidence_only": True, "network_calls": 0, "llm_calls": 0,
                "technical_analysis": False, "classification_writes": False,
                "financial_verdicts": False, "unknowns_are_explicit": True,
                "peer_groups_are_taxonomy_candidates_not_verified_comps": True,
            },
        }
        save(folder / "input.json", payload)
        save(folder / "state.json", {"status": "prepared", **payload})
        return folder
    finally:
        db.close()


def _result_map(path: Path, *, key_by_venue: bool) -> dict[Any, dict[str, Any]]:
    if not path or not Path(path).exists():
        return {}
    source = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)
    try:
        output: dict[Any, dict[str, Any]] = {}
        if key_by_venue:
            for ticker, venue, payload in source.execute("SELECT ticker,venue,payload FROM results"):
                row = json.loads(payload)
                output[(ticker, row.get("venue_code") or venue)] = row
        else:
            for ticker, payload in source.execute("SELECT ticker,payload FROM results"):
                output[ticker] = json.loads(payload)
        return output
    finally:
        source.close()


def _best_description(enrichment: dict[str, Any] | None) -> tuple[str | None, dict[str, Any] | None]:
    for source in (enrichment or {}).get("source_refs") or []:
        data = source.get("data") or {}
        description = str(data.get("description") or "").strip()
        if source.get("status") == "ok" and description:
            return description, source
    return None, None


def _build_evidence(queue: sqlite3.Row, listed_payload: dict[str, Any],
                    sec: dict[str, Any] | None, enrichment: dict[str, Any] | None,
                    snapshot_as_of: str, peers: list[str], gap_ids: list[str]) -> tuple[dict[str, Any], str]:
    raw_profile = listed_payload.get("raw_profile") or {}
    sources: list[dict[str, Any]] = []
    claim_sources: dict[str, list[str]] = defaultdict(list)
    description, description_source = _best_description(enrichment)
    business_model: str | None = None
    evidence_mode = "missing"

    if description and description_source:
        source_id = "src_stored_" + str(description_source.get("provider") or "profile") + "_" + str(description_source.get("hash") or digest(description))[:20]
        sources.append({
            "source_id": source_id, "provider": description_source.get("provider"),
            "as_of": description_source.get("observed_at") or snapshot_as_of,
            "url": "https://api.polygon.io/v3/reference/tickers/" + queue["ticker"],
            "evidence_label": "fact_provider_standardized",
        })
        business_model = description
        claim_sources["business_model"].append(source_id)
        evidence_mode = "stored_rich_description"
    else:
        industry = str(raw_profile.get("industry") or listed_payload.get("raw_industry") or "").strip()
        sector = str(raw_profile.get("sector") or listed_payload.get("raw_sector") or "").strip()
        if industry or sector:
            source_id = "src_exchange_profile_" + digest([
                queue["snapshot_id"], queue["venue_code"], queue["ticker"], industry, sector
            ])[:20]
            profile_url = str(raw_profile.get("url") or "")
            if profile_url.startswith("/"):
                profile_url = "https://www.nasdaq.com" + profile_url
            sources.append({
                "source_id": source_id, "provider": "exchange_bulk_profile",
                "as_of": snapshot_as_of, "url": profile_url or "urn:ecosystem:exchange-profile",
                "evidence_label": "fact_provider_standardized",
            })
            label = industry or sector
            business_model = (
                f"The stored exchange profile classifies {queue['ticker']} under {label}. "
                "This is provider operating context, not a verified issuer revenue-mix narrative."
            )
            claim_sources["business_model"].append(source_id)
            evidence_mode = "exchange_profile_context"

    details = (((sec or {}).get("source_ref") or {}).get("details") or {})
    sec_ref = (sec or {}).get("source_ref") or {}
    if (sec or {}).get("source_status") == "ok" and sec_ref.get("hash"):
        sec_id = "src_sec_" + str(sec_ref["hash"])[:20]
        sources.append({
            "source_id": sec_id, "provider": "sec_submissions",
            "as_of": sec_ref.get("observed_at") or snapshot_as_of,
            "url": "https://data.sec.gov/submissions/",
            "evidence_label": "fact_source_reported",
        })
        if business_model is None and details.get("sicDescription"):
            business_model = (
                f"SEC issuer metadata reports SIC {details.get('sic')}: {details['sicDescription']}. "
                "This identifies operating context but does not verify revenue mix."
            )
            claim_sources["business_model"].append(sec_id)
            evidence_mode = "sec_sic_context"

    peer_group: list[str] = []
    if peers:
        peer_id = "src_taxonomy_peers_" + digest([queue["snapshot_id"], queue["node_id"], peers])[:20]
        sources.append({
            "source_id": peer_id, "provider": "babis_classification_registry",
            "as_of": snapshot_as_of, "url": "urn:ecosystem:taxonomy-peers:" + str(queue["node_id"]),
            "evidence_label": "derived_calculation",
        })
        peer_group = peers
        claim_sources["peer_group"].append(peer_id)

    return ({
        "business_model": business_model, "value_chain_position": None,
        "revenue_drivers": [], "cost_drivers": [], "peer_group": peer_group,
        "competitive_position": None, "sector_kpis": {}, "industry_risks": [],
        "company_specific_risks": [], "catalysts": [], "source_references": sources,
        "claim_sources": dict(claim_sources), "evidence_gap_ids": gap_ids,
    }, evidence_mode)


def _gap_ids(db: sqlite3.Connection, ticker: str) -> list[str]:
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ecosystem_evidence_gap'"
    ).fetchone()
    if not exists:
        return []
    return [row[0] for row in db.execute(
        "SELECT gap_id FROM ecosystem_evidence_gap WHERE ticker=? "
        "AND resolution_state IN ('open','cooldown','retry_eligible') ORDER BY gap_id", (ticker,)
    )]


def _write_report(folder: Path, state: dict[str, Any], rows: list[dict[str, Any]],
                  evidence_modes: Counter[str], unknowns: Counter[str]) -> None:
    with (folder / "symbol_results.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["ticker", "venue_code", "agent_id", "sector", "subcategory", "evidence_mode",
                  "source_count", "peer_count", "unknown_count", "confidence", "status"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(sorted(rows, key=lambda row: (row["ticker"], row["venue_code"])))
    lines = [
        "# Sector-agent local evidence report", "", f"Run: `{state['run_id']}`",
        f"Status: `{state['status']}`", "", "## Result", "",
        f"- Symbols processed: **{state['processed_symbols']} / {state['total_symbols']}**",
        f"- Stored rich company descriptions reused: **{state['rich_descriptions']}**",
        f"- Exchange-profile business context: **{state['profile_context']}**",
        f"- Packets with taxonomy peer candidates: **{state['peer_groups']}**",
        f"- Packets still review-required: **{state['review_required']}**",
        f"- Failures: **{state['failed']}**", "- New network calls: **0**",
        "- LLM calls: **0**", "- Technical analysis: **0**", "- Financial verdicts: **0**", "",
        "## Evidence mode", "", "| Mode | Symbols |", "|---|---:|",
    ]
    lines += [f"| `{key}` | {value} |" for key, value in sorted(evidence_modes.items())]
    lines += ["", "## Most common remaining unknowns", "", "| Unknown field | Packets |", "|---|---:|"]
    lines += [f"| `{key}` | {value} |" for key, value in unknowns.most_common()]
    lines += [
        "", "## Readiness interpretation", "",
        "The agents now have source-backed company operating context and auditable peer candidates where local evidence exists.",
        "These packets are intentionally not promoted to evidence-complete: issuer-reported revenue drivers, cost drivers, sector KPI values, competitive position, risks and catalysts still require filing/IR evidence.",
        "Taxonomy peer groups are screening candidates, not verified valuation comparables.",
        "No packet was handed to Giannis because the current evidence gate correctly rejected incomplete sector context.",
    ]
    (folder / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _publish_control_plane(db: sqlite3.Connection, packet: dict[str, Any]) -> dict[str, Any]:
    listing_subject = (
        f"listing:{packet['snapshot_id']}:{packet['venue_code']}:{packet['symbol']}"
    )
    research_control_plane.register_subject_alias(
        db, alias_subject_id=listing_subject, canonical_subject_id=listing_subject,
        alias_kind="listing", source="sector_agent_local_evidence_runner",
    )
    observation, _ = research_control_plane.record_observation(
        db, subject_id=listing_subject, capability="research.sector_context",
        provider="ecosystem_sector_agents", source_ref=packet["packet_id"],
        observed_at=packet["created_at"], as_of=packet["created_at"],
        run_id=packet["run_id"], status="available", payload=packet,
    )
    inputs = [("observation", observation["observation_id"])]
    fingerprint = research_control_plane.input_fingerprint(
        stage="sector_context", subject_id=listing_subject, inputs=inputs,
        config={"schema": packet["schema"], "framework_id": packet["framework_id"]},
        implementation_version="sector_agent_local_evidence_runner.v2",
    )
    checkpoint = research_control_plane.acquire_checkpoint(
        db, run_id=packet["run_id"], stage="sector_context", subject_id=listing_subject,
        fingerprint=fingerprint, details={"sector_packet_id": packet["packet_id"]},
    )
    if checkpoint["status"] == "reused":
        return {"status": "reused", "control_packet_id": checkpoint["output_packet_id"]}
    control_packet, _ = research_control_plane.build_packet(
        db, kind="sector_context", subject_id=listing_subject, run_id=packet["run_id"],
        producer_name=packet["agent_id"], producer_version="v1",
        as_of=packet["created_at"],
        quality_state="ready" if packet.get("status") == "ready" else "conditional",
        inputs=inputs, payload=packet,
    )
    research_control_plane.set_packet_freshness(
        db, packet_id=control_packet["packet_id"], knowledge_date=packet["created_at"],
        stale_after=None,
        must_refresh_if=("new_filing", "sector_reclassification", "material_industry_event"),
        freshness_state="current" if packet.get("status") == "ready" else "needs_refresh",
    )
    research_control_plane.record_usage_event(
        db, run_id=packet["run_id"], stage="sector_context", subject_id=listing_subject,
        api_calls=0, input_tokens=0, output_tokens=0, estimated_cost_usd=0.0,
        cache_status="miss", metadata={"agent_id": packet["agent_id"]},
    )
    completed = research_control_plane.complete_checkpoint(
        db, checkpoint["checkpoint_id"], control_packet
    )
    return {"status": completed["status"], "control_packet_id": control_packet["packet_id"]}


def run(folder: Path, db_path: Path = DEFAULT_DB) -> int:
    import msvcrt
    folder = Path(folder)
    lock = (folder.parent / "sector_evidence_worker.lock").open("a+b"); lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close(); return 2
    inputs = json.loads((folder / "input.json").read_text(encoding="utf-8"))
    state = {
        **inputs, "status": "running", "pid": os.getpid(), "started_at": now(),
        "processed_symbols": 0, "rich_descriptions": 0, "profile_context": 0,
        "peer_groups": 0, "review_required": 0, "failed": 0,
        "network_calls": 0, "llm_calls": 0, "financial_handoffs": 0,
    }
    save(folder / "state.json", state)
    db = connect(db_path)
    control_db = connect_control_plane()
    result_rows: list[dict[str, Any]] = []
    evidence_modes: Counter[str] = Counter(); unknowns: Counter[str] = Counter()
    try:
        schema(db)
        sec_map = _result_map(Path(inputs["sec_results"]), key_by_venue=True)
        enrichment_map = _result_map(Path(inputs["enrichment_results"]), key_by_venue=False) if inputs.get("enrichment_results") else {}
        snapshot_row = db.execute(
            "SELECT retrieved_at FROM universe_snapshot WHERE snapshot_id=?", (inputs["snapshot_id"],)
        ).fetchone()
        snapshot_as_of = snapshot_row[0] if snapshot_row else inputs["created_at"]
        queue = list(db.execute(
            "SELECT q.*,v.name,v.payload_json FROM sector_analysis_queue q "
            "JOIN effective_listed_symbol v ON v.snapshot_id=q.snapshot_id AND v.venue_code=q.venue_code AND v.ticker=q.ticker "
            "WHERE q.activation_id=? AND q.status IN (?,?) ORDER BY q.ticker,q.venue_code",
            (inputs["activation_id"], *ELIGIBLE_STATUSES),
        ))
        peers_by_node: dict[str, list[str]] = defaultdict(list)
        for item in queue:
            peers_by_node[str(item["node_id"])].append(item["ticker"])
        with db:
            db.execute("INSERT OR REPLACE INTO sector_agent_evidence_run VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                inputs["run_id"], inputs["activation_id"], state["status"], len(queue), 0, 0, 0, 0, 0, 0,
                0, 0, inputs["created_at"], now(), None,
                json.dumps(state, ensure_ascii=False, sort_keys=True),
            ))
        for index, item in enumerate(queue, start=1):
            if (folder / "STOP").exists():
                state["status"] = "stopped_with_pending"; break
            try:
                peer_candidates = [ticker for ticker in peers_by_node[str(item["node_id"])] if ticker != item["ticker"]][:5]
                evidence, mode = _build_evidence(
                    item, json.loads(item["payload_json"]),
                    sec_map.get((item["ticker"], item["venue_code"])), enrichment_map.get(item["ticker"]),
                    snapshot_as_of, peer_candidates, _gap_ids(db, item["ticker"]),
                )
                packet = analyze_input({"identity": {
                    "run_id": inputs["run_id"], "snapshot_id": item["snapshot_id"],
                    "symbol": item["ticker"], "venue_code": item["venue_code"],
                    "classification_id": item["queue_id"], "leaf_node": item["node_id"],
                }, "evidence": evidence})
                with db:
                    db.execute("INSERT OR REPLACE INTO sector_context_packet_store VALUES(?,?,?,?,?,?,?,?,?)", (
                        packet["packet_id"], inputs["activation_id"], item["queue_id"], packet["agent_id"],
                        inputs["run_id"], item["ticker"], json.dumps(packet, ensure_ascii=False, sort_keys=True),
                        packet["status"], packet["created_at"],
                    ))
                    db.execute(
                        "UPDATE sector_analysis_queue SET status='sector_context_local_evidence_review_required' WHERE queue_id=?",
                        (item["queue_id"],),
                    )
                control_plane = _publish_control_plane(control_db, packet)
                evidence_modes[mode] += 1
                unknowns.update(packet["unknown_fields"])
                result_rows.append({
                    "ticker": item["ticker"], "venue_code": item["venue_code"],
                    "agent_id": packet["agent_id"], "sector": packet["sector"],
                    "subcategory": packet["subcategory"], "evidence_mode": mode,
                    "source_count": len(packet["source_references"]),
                    "peer_count": len(packet["peer_group"]), "unknown_count": len(packet["unknown_fields"]),
                    "confidence": packet["confidence"], "status": packet["status"],
                    "control_packet_id": control_plane.get("control_packet_id"),
                })
                state["rich_descriptions"] += int(mode == "stored_rich_description")
                state["profile_context"] += int(mode in {"exchange_profile_context", "sec_sic_context"})
                state["peer_groups"] += int(bool(packet["peer_group"]))
                state["review_required"] += int(packet["status"] == "review_required")
            except Exception as exc:
                state["failed"] += 1
                with (folder / "errors.log").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({
                        "ticker": item["ticker"], "venue_code": item["venue_code"],
                        "error_type": type(exc).__name__, "error": str(exc),
                    }, ensure_ascii=False) + "\n")
            state["processed_symbols"] = index
            if index % 100 == 0:
                state["updated_at"] = now(); save(folder / "state.json", state)
        else:
            state["status"] = "finished_with_review_required" if state["review_required"] else "finished"
    except Exception as exc:
        state.update(status="failed", error_type=type(exc).__name__, error_message=str(exc)[:1000])
    finally:
        state["finished_at"] = now(); state["updated_at"] = state["finished_at"]
        save(folder / "state.json", state)
        if result_rows:
            _write_report(folder, state, result_rows, evidence_modes, unknowns)
        with db:
            db.execute(
                "UPDATE sector_agent_evidence_run SET status=?,processed_count=?,rich_description_count=?,"
                "profile_context_count=?,peer_group_count=?,review_required_count=?,failed_count=?,"
                "updated_at=?,finished_at=?,summary_json=? WHERE run_id=?",
                (state["status"], state["processed_symbols"], state["rich_descriptions"], state["profile_context"],
                 state["peer_groups"], state["review_required"], state["failed"], state["updated_at"],
                 state["finished_at"], json.dumps(state, ensure_ascii=False, sort_keys=True), inputs["run_id"]),
            )
        control_db.close(); db.close(); lock.close()
    return 1 if state["status"] == "failed" else 0


def start(activation_id: str | None = None, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    folder = prepare(activation_id, db_path)
    log = (folder / "worker.log").open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            [sys.executable, "-B", str(Path(__file__).resolve()), "run", "--job", str(folder), "--db", str(db_path)],
            cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
        )
    finally:
        log.close()
    save(ROOT / "latest.json", {"run": str(folder), "run_id": folder.name, "pid": process.pid})
    notifier = PROJECT / "notify_sector_evidence_run.ps1"
    if notifier.exists():
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(notifier),
             "-StatePath", str(folder / "state.json")],
            cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
        )
    return {"status": "dispatched", "pid": process.pid, "run": str(folder), "run_id": folder.name}


def status(job: Path | None = None) -> dict[str, Any]:
    if job is None:
        latest = json.loads((ROOT / "latest.json").read_text(encoding="utf-8")); job = Path(latest["run"])
    return json.loads((Path(job) / "state.json").read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "run", "status"))
    parser.add_argument("--activation-id"); parser.add_argument("--job", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)
    if args.action == "start": result = start(args.activation_id, args.db)
    elif args.action == "run":
        if not args.job: parser.error("--job is required for run")
        return run(args.job, args.db)
    else: result = status(args.job)
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
