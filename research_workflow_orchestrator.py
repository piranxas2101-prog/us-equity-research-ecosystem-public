"""Checkpointed, read-only research information flow for one symbol at a time.

Collection is deliberately narrow: no universe loop, no automatic retry, no
LLM call. Batch schedulers may invoke this command for eligible symbols while
the shared rate ledger prevents quota overruns across processes.
"""

from __future__ import annotations
import argparse
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from financial_evidence_packet import PACKET_SCHEMA, build_financial_evidence_packet, readiness_gate
from provider_rate_limiter import SharedProviderRateLimiter
import research_control_plane
from research_information_flow import ObservationEnvelope, ResearchPacketRegistry, digest
from sec_xbrl_pilot import extract_companyfacts, fetch_companyfacts

ROOT = Path(__file__).resolve().parent
# The ecosystem registry already uses this local SQLite database.  Keeping the
# financial flow here makes packet IDs usable by Company Profile, Giannis and
# the frozen fundamental handoff without a second control-plane database.
DEFAULT_FLOW_DB = ROOT / "data" / "market_intel.db"


@contextmanager
def _control_plane_connection(registry: ResearchPacketRegistry):
    """Open the shared control-plane tables in the existing flow database."""
    db = sqlite3.connect(registry.db_path)
    db.row_factory = sqlite3.Row
    try:
        research_control_plane.init_schema(db)
        yield db
    finally:
        db.close()


def publish_financial_evidence_control_plane(
    *,
    registry: ResearchPacketRegistry,
    observation: ObservationEnvelope,
    financial_packet: dict[str, Any],
    registry_packet_id: str,
    readiness: dict[str, Any],
    run_id: str,
    upstream_control_packet_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Project one frozen financial packet into the common evidence plane.

    The original ResearchPacketRegistry contract remains unchanged.  This is an
    additive projection for cross-agent provenance and handoff, with no network
    or LLM activity.
    """
    subject_id = observation.subject_id
    with _control_plane_connection(registry) as db:
        source_ref = observation.source_url or f"{observation.provider}/{subject_id}/{observation.as_of or 'unknown'}"
        source, _ = research_control_plane.record_observation(
            db,
            subject_id=subject_id,
            capability="financial.company_facts",
            provider="sec_edgar" if observation.provider == "sec" else observation.provider,
            source_ref=source_ref,
            observed_at=observation.observed_at,
            as_of=observation.as_of,
            run_id=run_id,
            status="available",
            payload=observation.payload,
        )
        inputs = [("observation", source["observation_id"])]
        for packet_id in upstream_control_packet_ids:
            upstream = db.execute(
                "SELECT subject_id FROM ecosystem_evidence_packet_store WHERE packet_id=?", (packet_id,)
            ).fetchone()
            if not upstream:
                raise research_control_plane.ControlPlaneError(f"Unknown upstream packet: {packet_id}")
            upstream_subject = research_control_plane.canonical_subject(db, str(upstream["subject_id"]))
            current_subject = research_control_plane.canonical_subject(db, subject_id)
            if upstream_subject != current_subject:
                raise research_control_plane.ControlPlaneError(
                    f"Canonical subject mismatch: {packet_id} cannot feed {subject_id}"
                )
            inputs.append(("packet", packet_id))
        gap_count = 0
        for missing in financial_packet.get("unknown_fields") or []:
            field = str(missing.get("field") or "unknown_financial_field")
            reason = str(missing.get("reason_code") or "no_data")
            _, created = research_control_plane.record_evidence_gap(
                db,
                subject_id=subject_id,
                run_id=run_id,
                field=field,
                reason_code=reason,
                owner="financial_evidence_engine",
                source_event_id=source["observation_id"],
                payload={"ticker": financial_packet.get("ticker"), "provider": "sec_edgar"},
            )
            gap_count += int(created)
        for conflict in financial_packet.get("conflicts") or []:
            field = str(conflict.get("field") or "financial_source_conflict")
            _, created = research_control_plane.record_evidence_gap(
                db,
                subject_id=subject_id,
                run_id=run_id,
                field=field,
                reason_code="conflicting_sources",
                owner="financial_evidence_engine",
                source_event_id=source["observation_id"],
                payload=conflict,
            )
            gap_count += int(created)
        fingerprint = research_control_plane.input_fingerprint(
            stage="financial_evidence",
            subject_id=subject_id,
            inputs=inputs,
            config={
                "financial_packet_schema": financial_packet.get("schema"),
                "financial_packet_hash": financial_packet.get("payload_sha256"),
                "readiness_hash": readiness.get("readiness_sha256"),
            },
            implementation_version="research_workflow_orchestrator.v3",
        )
        checkpoint = research_control_plane.acquire_checkpoint(
            db,
            run_id=run_id,
            stage="financial_evidence",
            subject_id=subject_id,
            fingerprint=fingerprint,
            details={"financial_packet_id": registry_packet_id},
        )
        if checkpoint["status"] == "reused":
            return {
                "status": "reused",
                "observation_id": source["observation_id"],
                "control_packet_id": checkpoint["output_packet_id"],
                "checkpoint_id": checkpoint["checkpoint_id"],
                "new_evidence_gaps": gap_count,
            }
        control_packet, created = research_control_plane.build_packet(
            db,
            kind="financial_evidence",
            subject_id=subject_id,
            run_id=run_id,
            producer_name="research_workflow_orchestrator",
            producer_version="v3",
            as_of=financial_packet.get("as_of"),
            quality_state="ready" if readiness.get("ready_for_fundamental_synthesis") else "evidence_gap",
            inputs=inputs,
            payload={
                "financial_packet_id": registry_packet_id,
                "financial_evidence_id": financial_packet.get("packet_id"),
                "financial_packet_hash": financial_packet.get("payload_sha256"),
                "ticker": financial_packet.get("ticker"),
                "readiness": readiness,
                "source_references": financial_packet.get("source_references", []),
                "unknown_fields": financial_packet.get("unknown_fields", []),
                "conflicts": financial_packet.get("conflicts", []),
            },
        )
        research_control_plane.set_packet_freshness(
            db, packet_id=control_packet["packet_id"], knowledge_date=financial_packet.get("as_of"),
            stale_after=None,
            must_refresh_if=("new_10_k", "new_10_q", "material_8_k", "source_correction"),
            freshness_state="current" if readiness.get("ready_for_fundamental_synthesis") else "needs_refresh",
        )
        still_missing = {
            str(item.get("field") or "") for item in (financial_packet.get("unknown_fields") or [])
            if isinstance(item, dict)
        }
        for gap in db.execute(
            "SELECT gap_id,field FROM ecosystem_control_plane_gap "
            "WHERE subject_id=? AND owner='financial_evidence_engine' AND status='open'",
            (subject_id,),
        ).fetchall():
            if str(gap["field"]) not in still_missing:
                research_control_plane.resolve_evidence_gap(
                    db, gap_id=str(gap["gap_id"]), resolving_input_kind="packet",
                    resolving_input_id=control_packet["packet_id"],
                    reason="resolved_by_new_financial_evidence",
                )
        result = research_control_plane.complete_checkpoint(db, checkpoint["checkpoint_id"], control_packet)
    return {
        "status": result["status"],
        "observation_id": source["observation_id"],
        "control_packet_id": control_packet["packet_id"],
        "checkpoint_id": result["checkpoint_id"],
        "created": created,
        "new_evidence_gaps": gap_count,
    }


def publish_financial_block_control_plane(
    *, registry: ResearchPacketRegistry, observation: ObservationEnvelope, run_id: str
) -> dict[str, Any]:
    """Expose an unavailable financial input as an explicit shared evidence gap."""
    subject_id = observation.subject_id
    with _control_plane_connection(registry) as db:
        source_ref = observation.source_url or f"{observation.provider}/{subject_id}/{observation.reason_code or 'blocked'}"
        source, _ = research_control_plane.record_observation(
            db,
            subject_id=subject_id,
            capability="financial.company_facts",
            provider="sec_edgar" if observation.provider == "sec" else observation.provider,
            source_ref=source_ref,
            observed_at=observation.observed_at,
            as_of=observation.as_of,
            run_id=run_id,
            status="blocked",
            reason_code=observation.reason_code,
            payload=observation.payload,
        )
        gap, created = research_control_plane.record_evidence_gap(
            db,
            subject_id=subject_id,
            run_id=run_id,
            field="financial_evidence",
            reason_code=str(observation.reason_code or "source_unavailable"),
            owner="financial_evidence_engine",
            source_event_id=source["observation_id"],
            payload={"ticker": observation.ticker, "provider": source["provider"]},
        )
    return {"observation_id": source["observation_id"], "evidence_gap_id": gap["gap_id"], "created": created}

def load_env(path: Path) -> None:
    if not path.is_file(): return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        key, value = line.split("=", 1); key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}: value = value[1:-1]
        if key: os.environ.setdefault(key, value)

def _blocked_observation(symbol: str, cik: int, decision: dict[str, Any]) -> ObservationEnvelope:
    return ObservationEnvelope.create(provider="sec", capability="company_facts",
        subject_id=f"sec:cik:{cik:010d}", ticker=symbol, payload_schema="ecosystem.provider_block.v1",
        payload={"next_eligible_at": decision.get("next_eligible_at"), "rate_policy_version": decision.get("rate_policy_version")},
        status="blocked", reason_code=str(decision["reason_code"]))

def collect_sec_financials(*, symbol: str, cik: int, as_of: str, run_id: str,
                           registry: ResearchPacketRegistry,
                           limiter: SharedProviderRateLimiter,
                           upstream_control_packet_ids: tuple[str, ...] = (),
                           companyfacts_cache: Any = None) -> dict[str, Any]:
    subject_id = f"sec:cik:{cik:010d}"
    signature = digest({"subject_id": subject_id, "as_of": as_of, "stage": "financial_evidence", "contract": "v2-ifrs-currency-freshness"})
    if registry.checkpoint_matches(run_id, subject_id, "financial_evidence", signature):
        return {"status": "checkpoint_reused", "run_id": run_id, "subject_id": subject_id, "api_calls": 0}
    with _control_plane_connection(registry) as db:
        reusable = db.execute(
            "SELECT p.packet_id,p.payload_json,p.as_of,p.quality_state,"
            "f.knowledge_date,f.stale_after,f.must_refresh_if_json FROM ecosystem_evidence_packet_store p "
            "JOIN ecosystem_packet_freshness f ON f.packet_id=p.packet_id "
            "WHERE p.kind='financial_evidence' AND p.subject_id=? AND p.as_of=? "
            "AND p.producer_version IN ('v3','v3-reuse-receipt') "
            "AND p.quality_state='ready' AND f.freshness_state='current' "
            "ORDER BY p.created_at DESC LIMIT 1",
            (subject_id, as_of),
        ).fetchone()
        if reusable:
            payload = json.loads(reusable["payload_json"])
            packet_payload = dict(payload.get("payload") or {})
            readiness = dict(packet_payload.get("readiness") or {})
            inputs = [("packet", packet_id) for packet_id in upstream_control_packet_ids]
            inputs.append(("packet", str(reusable["packet_id"])))
            fingerprint = research_control_plane.input_fingerprint(
                stage="financial_evidence",
                subject_id=subject_id,
                inputs=inputs,
                config={
                    "reuse_source_packet_id": reusable["packet_id"],
                    "as_of": reusable["as_of"],
                },
                implementation_version="research_workflow_orchestrator.v3-reuse-receipt",
            )
            checkpoint = research_control_plane.acquire_checkpoint(
                db, run_id=run_id, stage="financial_evidence", subject_id=subject_id,
                fingerprint=fingerprint,
                details={"reused_control_packet_id": reusable["packet_id"]},
            )
            if checkpoint["status"] == "reused":
                current_packet_id = str(checkpoint["output_packet_id"])
            else:
                current_packet, _ = research_control_plane.build_packet(
                    db,
                    kind="financial_evidence",
                    subject_id=subject_id,
                    run_id=run_id,
                    producer_name="research_workflow_orchestrator",
                    producer_version="v3-reuse-receipt",
                    as_of=reusable["as_of"],
                    quality_state=str(reusable["quality_state"]),
                    inputs=inputs,
                    payload={
                        **packet_payload,
                        "reused_from_control_packet_id": reusable["packet_id"],
                    },
                )
                triggers = json.loads(reusable["must_refresh_if_json"] or "[]")
                research_control_plane.set_packet_freshness(
                    db,
                    packet_id=current_packet["packet_id"],
                    knowledge_date=reusable["knowledge_date"],
                    stale_after=reusable["stale_after"],
                    must_refresh_if=triggers,
                    freshness_state="current",
                )
                research_control_plane.complete_checkpoint(
                    db, checkpoint["checkpoint_id"], current_packet
                )
                current_packet_id = str(current_packet["packet_id"])
            registry_packet_id = str(packet_payload.get("financial_packet_id") or "").strip()
            if registry_packet_id:
                registry.set_packet_ref(
                    run_id, subject_id, "financial_evidence_packet_id", registry_packet_id
                )
                registry.complete_stage(
                    run_id, subject_id, "financial_evidence", signature, registry_packet_id
                )
            research_control_plane.record_usage_event(
                db, run_id=run_id, stage="financial_evidence", subject_id=subject_id,
                provider="sec_edgar", api_calls=0, input_tokens=0, output_tokens=0,
                estimated_cost_usd=0.0, cache_status="cross_run_hit",
                metadata={
                    "reused_control_packet_id": reusable["packet_id"],
                    "current_run_receipt_packet_id": current_packet_id,
                },
            )
            return {
                "status": "cross_run_reused", "run_id": run_id, "subject_id": subject_id,
                "ready_for_giannis": bool(readiness.get("ready_for_fundamental_synthesis")),
                "blockers": list(readiness.get("blockers") or []),
                "control_plane": {
                    "status": "cross_run_reused",
                    "control_packet_id": current_packet_id,
                    "reused_from_control_packet_id": reusable["packet_id"],
                },
                "api_calls": 0, "llm_calls": 0,
            }
    cached_raw, cache_info = (companyfacts_cache.decide(cik) if companyfacts_cache is not None
                              else (None, {"reason": "cache_not_configured"}))
    if cached_raw is not None:
        return _publish_companyfacts(
            raw=cached_raw, provenance=cache_info, symbol=symbol, subject_id=subject_id, as_of=as_of,
            run_id=run_id, signature=signature, registry=registry,
            upstream_control_packet_ids=upstream_control_packet_ids)
    absent = companyfacts_cache.known_absent(cik) if companyfacts_cache is not None else None
    if absent is not None:
        # The SEC has no companyfacts for this CIK (HTTP 404 seen recently): no request, no rate-limit slot.
        return _block_companyfacts(
            registry, symbol, subject_id, run_id, "source_unavailable", api_calls=0,
            payload={"http_status": absent.get("http_status", 404), "permanent": True,
                     "checked_date_et": absent.get("checked_date_et"), "recheck_after": absent.get("recheck_after"),
                     "detail": "the SEC publishes no companyfacts for this issuer"})
    user_agent = os.getenv("SEC_USER_AGENT", "").strip()
    if not user_agent:
        observation = ObservationEnvelope.create(provider="sec", capability="company_facts",
            subject_id=subject_id, ticker=symbol, payload_schema="ecosystem.provider_block.v1",
            payload={}, status="blocked", reason_code="not_configured")
        registry.put_observation(observation)
        gap_id = registry.record_gap(run_id=run_id, observation=observation)
        control_plane = publish_financial_block_control_plane(
            registry=registry, observation=observation, run_id=run_id
        )
        return {"status": "blocked", "reason_code": "not_configured", "evidence_gap_id": gap_id,
                "control_plane": control_plane, "api_calls": 0}
    decision = limiter.permit("sec", max_interval_wait_seconds=0.5)
    decision_data = {**decision.as_dict(), "rate_policy_version": limiter.policy_version}
    if not decision.permitted:
        observation = _blocked_observation(symbol, cik, decision_data)
        registry.put_observation(observation)
        gap_id = registry.record_gap(run_id=run_id, observation=observation,
                                     next_eligible_at=decision.next_eligible_at)
        control_plane = publish_financial_block_control_plane(
            registry=registry, observation=observation, run_id=run_id
        )
        return {"status": "blocked", "run_id": run_id, "subject_id": subject_id,
                "reason_code": decision.reason_code, "next_eligible_at": decision.next_eligible_at,
                "evidence_gap_id": gap_id, "control_plane": control_plane, "api_calls": 0}
    try:
        raw = fetch_companyfacts(cik, user_agent)
        limiter.record_result("sec", "ok")
    except RuntimeError as exc:
        status = "rate_limited" if "429" in str(exc) else "source_unavailable"
        limiter.record_result("sec", status)
        permanent = "HTTP 404" in str(exc)  # 404 = the SEC publishes nothing for this CIK; 403/5xx are not permanent
        marker = companyfacts_cache.note_absent(cik, 404) if permanent and companyfacts_cache is not None else None
        return _block_companyfacts(
            registry, symbol, subject_id, run_id, status, api_calls=1,
            payload=({"http_status": 404, "permanent": True, "detail": "the SEC publishes no companyfacts for this issuer",
                      "recheck_after": (marker or {}).get("recheck_after")} if permanent else {}))
    stored = companyfacts_cache.store(cik, raw) if companyfacts_cache is not None else None
    return _publish_companyfacts(
        raw=raw, provenance={"source": "fresh_download", "cache_decision": cache_info.get("reason"),
                             "stored_sha256": (stored or {}).get("sha256")},
        symbol=symbol, subject_id=subject_id, as_of=as_of, run_id=run_id, signature=signature,
        registry=registry, upstream_control_packet_ids=upstream_control_packet_ids)


def _block_companyfacts(registry: ResearchPacketRegistry, symbol: str, subject_id: str, run_id: str, reason: str, *,
                        api_calls: int, payload: dict[str, Any]) -> dict[str, Any]:
    """Record a blocked companyfacts collection as an observation, an evidence gap and a control-plane block."""
    observation = ObservationEnvelope.create(provider="sec", capability="company_facts",
        subject_id=subject_id, ticker=symbol, payload_schema="ecosystem.provider_block.v1",
        payload=payload, status="blocked", reason_code=reason)
    registry.put_observation(observation)
    gap_id = registry.record_gap(run_id=run_id, observation=observation)
    control_plane = publish_financial_block_control_plane(registry=registry, observation=observation, run_id=run_id)
    result = {"status": "blocked", "reason_code": reason, "evidence_gap_id": gap_id,
              "control_plane": control_plane, "api_calls": api_calls}
    if payload.get("permanent"):
        result["permanent"] = True
    return result


def _publish_companyfacts(*, raw: dict[str, Any], provenance: dict[str, Any], symbol: str, subject_id: str,
                          as_of: str, run_id: str, signature: str, registry: ResearchPacketRegistry,
                          upstream_control_packet_ids: tuple[str, ...]) -> dict[str, Any]:
    """Extract, register and publish one companyfacts document (fresh or from the local store)."""
    cached = provenance.get("source") == "cached_companyfacts"
    extracted = {**extract_companyfacts(raw, as_of), "companyfacts_source": provenance}
    observation = ObservationEnvelope.create(provider="sec", capability="company_facts",
        subject_id=subject_id, ticker=symbol, source_url="https://data.sec.gov/",
        payload_schema=str(extracted["schema"]), payload=extracted, as_of=extracted.get("as_of"),
        # A reused document was observed when it was downloaded, not now.
        observed_at=provenance.get("fetched_at") if cached else None,
        raw_sha256=extracted.get("payload_sha256"))
    observation_id = registry.put_observation(observation)
    packet = build_financial_evidence_packet(subject_id=subject_id, ticker=symbol,
                                              sec_observation=observation.as_dict())
    readiness = readiness_gate(packet)
    packet_id = registry.put_packet(PACKET_SCHEMA, subject_id,
        {**packet, "readiness": readiness}, [observation_id])
    registry.set_packet_ref(run_id, subject_id, "financial_evidence_packet_id", packet_id)
    registry.complete_stage(run_id, subject_id, "financial_evidence", signature, packet_id)
    control_plane = publish_financial_evidence_control_plane(
        registry=registry,
        observation=observation,
        financial_packet=packet,
        registry_packet_id=packet_id,
        readiness=readiness,
        run_id=run_id,
        upstream_control_packet_ids=upstream_control_packet_ids,
    )
    with _control_plane_connection(registry) as db:
        research_control_plane.record_usage_event(
            db, run_id=run_id, stage="financial_evidence", subject_id=subject_id,
            provider="sec_edgar", api_calls=0 if cached else 1, input_tokens=0, output_tokens=0,
            estimated_cost_usd=0.0, cache_status="companyfacts_store_hit" if cached else "miss",
            metadata={"ready_for_giannis": readiness["ready_for_fundamental_synthesis"],
                      "companyfacts_source": provenance},
        )
    return {"status": "completed", "run_id": run_id, "subject_id": subject_id,
            "observation_id": observation_id, "packet_id": packet_id,
            "ready_for_giannis": readiness["ready_for_fundamental_synthesis"],
            "blockers": readiness["blockers"], "control_plane": control_plane,
            "companyfacts_source": provenance.get("source"),
            "api_calls": 0 if cached else 1, "llm_calls": 0}

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rate-limited research information flow")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--db", default=str(DEFAULT_FLOW_DB))
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect-sec-financials")
    collect.add_argument("--symbol", required=True); collect.add_argument("--cik", required=True, type=int)
    collect.add_argument("--as-of", required=True); collect.add_argument("--run-id", required=True)
    sub.add_parser("rate-status")
    args = parser.parse_args(argv); load_env(Path(args.env_file))
    limiter = SharedProviderRateLimiter(); registry = ResearchPacketRegistry(Path(args.db))
    if args.command == "rate-status":
        print(json.dumps({"policy_version": limiter.policy_version, "providers": limiter.status()}, ensure_ascii=False, indent=2)); return 0
    result = collect_sec_financials(symbol=args.symbol.upper(), cik=args.cik, as_of=args.as_of,
        run_id=args.run_id, registry=registry, limiter=limiter)
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0 if result["status"] in {"completed", "checkpoint_reused", "blocked"} else 1

if __name__ == "__main__": raise SystemExit(main())
