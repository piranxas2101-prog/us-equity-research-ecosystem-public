from __future__ import annotations

import argparse
import hashlib
import json
from contextlib import closing
from collections import Counter
from pathlib import Path
from typing import Any

import market_intel_db
import research_control_plane
from run_context import attach_run_context, require_explicit_compatible_runs


PACKET_SCHEMA = "ecosystem.fundamental_research_packet.v1"
THESIS_SCHEMA = "ecosystem.fundamental_thesis.v1"


class FundamentalPacketError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise FundamentalPacketError(f"Expected JSON object: {path}")
    return payload


def rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("results")
    if not isinstance(value, list):
        value = payload.get("shortlist", [])
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _number(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _positive_facts(row: dict[str, Any]) -> list[str]:
    facts: list[str] = []
    if (_number(row.get("net_cash")) or 0) > 0:
        facts.append("positive_net_cash")
    if (_number(row.get("fcf_proxy")) or 0) > 0:
        facts.append("positive_free_cash_flow")
    if (_number(row.get("ocf_proxy")) or 0) > 0:
        facts.append("positive_operating_cash_flow")
    if str(row.get("catalyst_direction") or "") == "positive":
        facts.append("positive_primary_catalyst")
    return facts


def _adverse_facts(row: dict[str, Any]) -> list[str]:
    facts = [str(flag) for flag in (row.get("dilution_flags") or [])]
    if str(row.get("catalyst_direction") or "") == "negative":
        facts.append("negative_primary_catalyst")
    if str(row.get("risk_level") or "") == "High":
        facts.append("high_structural_risk")
    return list(dict.fromkeys(facts))


def build_packet(
    babis: dict[str, Any],
    giannis: dict[str, Any],
    petros: dict[str, Any],
    *,
    deep_research: dict[str, Any] | None = None,
    source_paths: dict[str, Path] | None = None,
) -> dict[str, Any]:
    run_id = require_explicit_compatible_runs(babis, giannis, petros)
    if deep_research is not None and str(deep_research.get("run_id") or "") != run_id:
        raise FundamentalPacketError("Company deep-research run_id does not match the fundamental run.")
    if str(petros.get("analysis_mode") or "") != "fundamental_only":
        raise FundamentalPacketError("Petros artifact is not a fundamental-only synthesis.")
    if bool(petros.get("technical_inputs_used")) or bool(petros.get("trader_inputs_used")):
        raise FundamentalPacketError("Technical or trader input leakage detected in Petros artifact.")

    babis_by_ticker = {
        str(row.get("ticker") or "").upper(): row for row in rows(babis) if row.get("ticker")
    }
    giannis_by_ticker = {
        str(row.get("ticker") or "").upper(): row for row in rows(giannis) if row.get("ticker")
    }
    deep_by_ticker = {
        str(row.get("ticker") or "").upper(): row
        for row in (deep_research or {}).get("results") or []
        if isinstance(row, dict) and row.get("ticker")
    }
    thesis_rows: list[dict[str, Any]] = []
    for petros_row in rows(petros):
        ticker = str(petros_row.get("ticker") or "").upper().strip()
        if not ticker or ticker not in giannis_by_ticker or ticker not in babis_by_ticker:
            continue
        babis_row = babis_by_ticker[ticker]
        giannis_row = giannis_by_ticker[ticker]
        readiness = dict(petros_row.get("research_readiness") or {})
        blockers = [str(value) for value in (readiness.get("decision_blockers") or [])]
        evidence = list(babis_row.get("catalyst_evidence") or [])
        source_identity = {
            "babis_row_sha256": digest(babis_row),
            "giannis_row_sha256": digest(giannis_row),
            "petros_row_sha256": digest(petros_row),
        }
        deep_row = deep_by_ticker.get(ticker)
        if deep_row is not None:
            source_identity["deep_research_row_sha256"] = digest(deep_row)
        unknown_trace = {
            "babis": list(babis_row.get("unknown_events") or []),
            "giannis_upstream": list(giannis_row.get("upstream_unknown_events") or []),
            "giannis": list(giannis_row.get("unknown_events") or []),
            "petros_upstream": list(petros_row.get("upstream_unknown_events") or []),
            "petros": list(petros_row.get("unknown_events") or []),
        }
        thesis = {
            "schema": THESIS_SCHEMA,
            "thesis_id": f"thesis_{run_id}_{ticker}",
            "run_id": run_id,
            "ticker": ticker,
            "company": petros_row.get("company") or giannis_row.get("company"),
            "sector_id": giannis_row.get("sector_id"),
            "industry_id": giannis_row.get("industry_id"),
            "analysis_lane": "fundamental",
            "technical_inputs_used": False,
            "trader_inputs_used": False,
            "company_thesis_status": readiness.get("company_thesis_status", "untested"),
            "security_thesis_readiness": readiness.get(
                "security_thesis_readiness", "not_decision_grade"
            ),
            "technical_review_eligible": bool(readiness.get("technical_review_eligible")),
            "execution_ready": bool(readiness.get("execution_ready")),
            "paper_test_eligible": bool(readiness.get("paper_test_eligible")),
            "live_capital_eligible": False,
            "decision_blockers": blockers,
            "primary_blocker": readiness.get("primary_blocker"),
            "blocker_categories": list(readiness.get("blocker_categories") or []),
            "blocker_details": list(readiness.get("blocker_details") or []),
            "missing_required_fields": list(readiness.get("missing_required_fields") or []),
            "resolution_actions": list(readiness.get("resolution_actions") or []),
            "readiness_status": readiness.get("readiness_status"),
            "fundamental_summary": {
                "giannis_score": giannis_row.get("score_final"),
                "giannis_tier": giannis_row.get("giannis_tier"),
                "petros_score": petros_row.get("petros_score"),
                "petros_verdict": petros_row.get("petros_verdict"),
                "confidence": petros_row.get("confidence"),
                "risk_level": petros_row.get("risk_level"),
                "instrument_type": petros_row.get("instrument_type"),
                "fundamental_attention_score": giannis_row.get("fundamental_attention_score"),
                "fundamental_coverage": giannis_row.get("fundamental_coverage"),
                "valuation_status": readiness.get("valuation_status"),
                "priced_in_status": readiness.get("priced_in_status"),
                "priced_in_assessed": readiness.get("priced_in_assessed"),
                "priced_in_confidence": readiness.get("priced_in_confidence"),
                "priced_in_source_as_of": readiness.get("priced_in_source_as_of"),
                "expectations_burden_score": petros_row.get("expectations_burden_score"),
                "catalyst_category_id": giannis_row.get("catalyst_category_id"),
                "catalyst_direction": giannis_row.get("catalyst_direction"),
            },
            "priced_in_assessment": dict(petros_row.get("priced_in_assessment") or {}),
            "deep_research": dict(deep_row or {}),
            "long_case": {
                "status": "hypothesis_not_trade_instruction",
                "supporting_facts": _positive_facts(giannis_row),
                "catalyst_score": giannis_row.get("long_catalyst_score"),
            },
            "short_case": {
                "status": "hypothesis_not_trade_instruction",
                "supporting_facts": _adverse_facts(giannis_row),
                "catalyst_score": giannis_row.get("short_catalyst_score"),
            },
            "neutral_case": {
                "status": "wait_for_proof" if blockers else "balanced_review",
                "reasons": blockers,
            },
            "prove_criteria": list(readiness.get("prove_criteria") or []),
            "kill_criteria": list(readiness.get("kill_criteria") or []),
            "next_diligence": list(readiness.get("next_diligence") or []),
            "rationale": list(petros_row.get("rationale") or []),
            "evidence": evidence,
            "unknown_trace": unknown_trace,
            "source_identity": source_identity,
        }
        thesis["fundamental_thesis_hash"] = digest(thesis)
        thesis_rows.append(thesis)

    source_artifacts: dict[str, Any] = {}
    source_payloads = {"babis": babis, "giannis": giannis, "petros": petros}
    if deep_research is not None:
        source_payloads["deep_research"] = deep_research
    for name, source_payload in source_payloads.items():
        artifact = {"payload_sha256": digest(source_payload)}
        path = (source_paths or {}).get(name)
        if path is not None:
            artifact.update({"path": str(path), "file_sha256": file_digest(path)})
        source_artifacts[name] = artifact

    readiness_counts = Counter(
        str(row.get("security_thesis_readiness") or "unknown") for row in thesis_rows
    )
    execution_blocker_counts = Counter(
        blocker
        for row in thesis_rows
        for blocker in (row.get("decision_blockers") or [])
    )
    blocker_category_counts = Counter(
        category
        for row in thesis_rows
        for category in (row.get("blocker_categories") or [])
    )
    payload = {
        "schema": PACKET_SCHEMA,
        "run_timestamp": str(
            petros.get("run_timestamp") or giannis.get("run_timestamp") or babis.get("run_timestamp") or ""
        ),
        "frozen_at": str(
            petros.get("run_timestamp") or giannis.get("run_timestamp") or babis.get("run_timestamp") or ""
        ),
        "analysis_order": ["babis", "giannis", "petros", "company_deep_research"],
        "analysis_lane": "fundamental",
        "technical_inputs_used": False,
        "trader_inputs_used": False,
        "immutable": True,
        "source_artifacts": source_artifacts,
        "row_count": len(thesis_rows),
        "technical_review_eligible_count": sum(
            1 for row in thesis_rows if row.get("technical_review_eligible")
        ),
        "execution_ready_count": sum(1 for row in thesis_rows if row.get("execution_ready")),
        "readiness_counts": dict(sorted(readiness_counts.items())),
        "execution_blocker_counts": dict(sorted(execution_blocker_counts.items())),
        "blocker_category_counts": dict(sorted(blocker_category_counts.items())),
        "theses": thesis_rows,
    }
    payload = attach_run_context(
        payload,
        stage="fundamental_research_packet",
        run_id=run_id,
        source_run_ids=[run_id],
    )
    payload["packet_hash"] = digest(payload)
    return payload


def validate_packet(payload: dict[str, Any], expected_run_id: str | None = None) -> str:
    if payload.get("schema") != PACKET_SCHEMA:
        raise FundamentalPacketError(f"Unsupported packet schema: {payload.get('schema')!r}")
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise FundamentalPacketError("Fundamental packet is missing run_id.")
    if expected_run_id and run_id != expected_run_id:
        raise FundamentalPacketError(
            f"Fundamental packet run_id mismatch: expected {expected_run_id}, got {run_id}."
        )
    if not bool(payload.get("immutable")):
        raise FundamentalPacketError("Fundamental packet is not marked immutable.")
    if bool(payload.get("technical_inputs_used")) or bool(payload.get("trader_inputs_used")):
        raise FundamentalPacketError("Fundamental packet contains forbidden technical/trader synthesis inputs.")
    source_artifacts = payload.get("source_artifacts")
    if not isinstance(source_artifacts, dict):
        raise FundamentalPacketError("Fundamental packet has no source-artifact identity map.")
    for source_name in ("babis", "giannis", "petros"):
        source = source_artifacts.get(source_name)
        if not isinstance(source, dict) or len(str(source.get("payload_sha256") or "")) != 64:
            raise FundamentalPacketError(f"Fundamental packet lacks canonical source hash for {source_name}.")
    seen_tickers: set[str] = set()
    for thesis in payload.get("theses", []):
        if not isinstance(thesis, dict) or thesis.get("schema") != THESIS_SCHEMA:
            raise FundamentalPacketError("Fundamental packet contains an invalid thesis schema.")
        ticker = str(thesis.get("ticker") or "").upper().strip()
        if not ticker or ticker in seen_tickers:
            raise FundamentalPacketError(f"Fundamental packet contains invalid or duplicate ticker {ticker!r}.")
        if str(thesis.get("run_id") or "") != run_id:
            raise FundamentalPacketError(f"Fundamental thesis run_id mismatch for {ticker}.")
        if bool(thesis.get("technical_inputs_used")) or bool(thesis.get("trader_inputs_used")):
            raise FundamentalPacketError(f"Fundamental thesis contains forbidden inputs for {ticker}.")
        thesis_claimed = str(thesis.get("fundamental_thesis_hash") or "")
        thesis_body = dict(thesis)
        thesis_body.pop("fundamental_thesis_hash", None)
        if not thesis_claimed or thesis_claimed != digest(thesis_body):
            raise FundamentalPacketError(f"Fundamental thesis hash validation failed for {ticker}.")
        seen_tickers.add(ticker)
    claimed = str(payload.get("packet_hash") or "")
    body = dict(payload)
    body.pop("packet_hash", None)
    if not claimed or claimed != digest(body):
        raise FundamentalPacketError("Fundamental packet hash validation failed.")
    return run_id


def index_theses(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("ticker") or "").upper(): row
        for row in payload.get("theses", [])
        if isinstance(row, dict) and row.get("ticker")
    }


def attach_fundamental_handoff(
    market_rows: list[dict[str, Any]], payload: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Attach frozen thesis identity and return only rows allowed into technical review."""

    theses = index_theses(payload)
    accepted: list[dict[str, Any]] = []
    blocked: list[str] = []
    for row in market_rows:
        ticker = str(row.get("ticker") or "").upper().strip()
        thesis = theses.get(ticker)
        if not thesis:
            blocked.append(f"{ticker}:missing_fundamental_thesis")
            continue
        if not bool(thesis.get("technical_review_eligible")):
            blockers = ",".join(str(item) for item in thesis.get("decision_blockers", []))
            blocked.append(f"{ticker}:technical_review_blocked:{blockers}")
            continue
        merged = dict(row)
        merged.update(
            {
                "fundamental_thesis_id": thesis.get("thesis_id"),
                "fundamental_thesis_hash": thesis.get("fundamental_thesis_hash"),
                "fundamental_packet_hash": payload.get("packet_hash"),
                "fundamental_security_readiness": thesis.get("security_thesis_readiness"),
                "fundamental_execution_ready": bool(thesis.get("execution_ready")),
                "fundamental_decision_blockers": list(thesis.get("decision_blockers") or []),
                "fundamental_long_case": thesis.get("long_case", {}),
                "fundamental_short_case": thesis.get("short_case", {}),
                "fundamental_prove_criteria": list(thesis.get("prove_criteria") or []),
                "fundamental_kill_criteria": list(thesis.get("kill_criteria") or []),
            }
        )
        accepted.append(merged)
    return accepted, blocked


def enforce_execution_gate(signal: dict[str, Any]) -> dict[str, Any]:
    """Keep technical analysis observable while preventing an unready thesis becoming executable."""

    row = dict(signal)
    ready = bool(row.get("fundamental_execution_ready"))
    pre_gate_verdict = str(row.get("signal_verdict") or "").strip().lower()
    gate_reasons = [str(value) for value in (row.get("fundamental_decision_blockers") or [])]
    row["fundamental_gate_status"] = "pass" if ready else "technical_review_only"
    row["execution_eligible"] = ready
    row["pre_gate_verdict"] = pre_gate_verdict
    if not ready and pre_gate_verdict == "candidate":
        row["signal_verdict"] = "watch"
        rationale = list(row.get("rationale") or [])
        rationale.append(
            "Fundamental thesis is not execution-ready; candidate downgraded to watch."
        )
        row["rationale"] = rationale
        gate_reasons = ["fundamental_not_execution_ready", *gate_reasons]
    row["post_gate_verdict"] = str(row.get("signal_verdict") or "").strip().lower()
    row["gate_decision_reasons"] = list(dict.fromkeys(gate_reasons))
    row["verdict_transition"] = {
        "from": row["pre_gate_verdict"],
        "to": row["post_gate_verdict"],
        "changed": row["pre_gate_verdict"] != row["post_gate_verdict"],
        "gate": "fundamental_readiness",
    }
    return row


def render_md(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Frozen Fundamental Research Packet",
            "",
            f"- Run ID: `{payload.get('run_id')}`",
            f"- Packet hash: `{payload.get('packet_hash')}`",
            f"- Fundamental theses: {payload.get('row_count', 0)}",
            f"- Eligible for technical review: {payload.get('technical_review_eligible_count', 0)}",
            f"- Execution ready: {payload.get('execution_ready_count', 0)}",
            f"- Readiness: {payload.get('readiness_counts', {})}",
            "- Technical/trader inputs used in thesis: no",
            "",
            "This packet is the immutable handoff from fundamental research to the trader agents.",
            "Technical analysis may append a separate trade plan but may not modify this thesis.",
            "",
        ]
    )


def write_packet(payload: dict[str, Any], out_prefix: Path) -> tuple[Path, Path, Path]:
    run_id = str(payload["run_id"])
    run_path = Path(f"{out_prefix}_{run_id}.json")
    latest_path = Path(f"{out_prefix}_latest.json")
    md_path = Path(f"{out_prefix}_latest.md")
    run_path.parent.mkdir(parents=True, exist_ok=True)
    if run_path.exists():
        existing = load(run_path)
        validate_packet(existing, expected_run_id=run_id)
        if existing.get("packet_hash") != payload.get("packet_hash"):
            raise FundamentalPacketError(f"Frozen packet conflict for run {run_id}.")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    run_path.write_text(encoded, encoding="utf-8")
    latest_path.write_text(encoded, encoding="utf-8")
    md_path.write_text(render_md(payload), encoding="utf-8")
    return run_path, latest_path, md_path


def publish_control_plane(payload: dict[str, Any], db_path: Path) -> dict[str, Any]:
    """Publish the frozen handoff as references in the local control plane.

    The existing JSON artifact remains the immutable trader-facing contract.  This
    additional projection gives dashboards and later stages queryable lineage,
    explicit blocker gaps, and a resumable checkpoint without changing the packet.
    """
    run_id = validate_packet(payload)
    subject_id = f"research_run:{run_id}"
    observed_at = str(payload.get("frozen_at") or payload.get("run_timestamp") or "")
    if not observed_at:
        raise FundamentalPacketError("Fundamental packet is missing frozen_at/run_timestamp for control-plane publishing.")
    with closing(market_intel_db.connect(Path(db_path))) as conn:
        research_control_plane.init_schema(conn)
        observation_refs: list[tuple[str, str]] = []
        gap_count = 0
        for thesis in payload.get("theses", []):
            ticker = str(thesis.get("ticker") or "").upper().strip()
            thesis_id = str(thesis.get("thesis_id") or "").strip()
            if not ticker or not thesis_id:
                raise FundamentalPacketError("Fundamental packet contains a thesis without ticker/thesis_id.")
            observation, _ = research_control_plane.record_observation(
                conn,
                subject_id=f"ticker:{ticker}",
                capability="research.fundamental_thesis",
                provider="ecosystem_fundamental_research",
                source_ref=f"{run_id}/{thesis_id}",
                observed_at=observed_at,
                as_of=observed_at,
                run_id=run_id,
                payload=thesis,
                status="available",
            )
            observation_refs.append(("observation", observation["observation_id"]))
            for blocker in thesis.get("decision_blockers") or []:
                if str(blocker) in {"verdict_not_execution_candidate", "high_risk"}:
                    continue
                _, created = research_control_plane.record_evidence_gap(
                    conn,
                    subject_id=f"ticker:{ticker}",
                    run_id=run_id,
                    field="fundamental_research_readiness",
                    reason_code=str(blocker),
                    owner="petros",
                    source_event_id=thesis_id,
                    payload={
                        "thesis_id": thesis_id,
                        "blocker_categories": thesis.get("blocker_categories", []),
                        "resolution_actions": thesis.get("resolution_actions", []),
                    },
                )
                gap_count += int(created)
        fingerprint = research_control_plane.input_fingerprint(
            stage="fundamental_research_packet",
            subject_id=subject_id,
            inputs=observation_refs,
            config={"packet_schema": PACKET_SCHEMA, "analysis_lane": "fundamental"},
            implementation_version="fundamental_research_packet.v1",
        )
        checkpoint = research_control_plane.acquire_checkpoint(
            conn,
            run_id=run_id,
            stage="fundamental_research_packet",
            subject_id=subject_id,
            fingerprint=fingerprint,
            details={"fundamental_packet_hash": payload["packet_hash"]},
        )
        if checkpoint["status"] == "reused":
            return {
                "status": "reused",
                "run_id": run_id,
                "checkpoint_id": checkpoint["checkpoint_id"],
                "control_packet_id": checkpoint["output_packet_id"],
                "new_evidence_gaps": gap_count,
            }
        control_packet, created = research_control_plane.build_packet(
            conn,
            kind="fundamental_research_packet",
            subject_id=subject_id,
            run_id=run_id,
            producer_name="fundamental_research_packet",
            producer_version="v1",
            as_of=observed_at,
            quality_state="ready" if not payload.get("execution_blocker_counts") else "conditional",
            inputs=observation_refs,
            payload={
                "fundamental_packet_hash": payload["packet_hash"],
                "row_count": payload.get("row_count", 0),
                "technical_review_eligible_count": payload.get("technical_review_eligible_count", 0),
                "execution_ready_count": payload.get("execution_ready_count", 0),
                "execution_blocker_counts": payload.get("execution_blocker_counts", {}),
            },
        )
        research_control_plane.set_packet_freshness(
            conn, packet_id=control_packet["packet_id"], knowledge_date=observed_at,
            stale_after=None,
            must_refresh_if=("new_filing", "new_earnings", "guidance_change", "valuation_refresh"),
            freshness_state="current" if not payload.get("execution_blocker_counts") else "needs_refresh",
        )
        research_control_plane.record_usage_event(
            conn, run_id=run_id, stage="fundamental_research_packet", subject_id=subject_id,
            api_calls=0, input_tokens=0, output_tokens=0, estimated_cost_usd=0.0,
            cache_status="miss", metadata={"row_count": payload.get("row_count", 0)},
        )
        result = research_control_plane.complete_checkpoint(conn, checkpoint["checkpoint_id"], control_packet)
    return {
        "status": result["status"],
        "run_id": run_id,
        "checkpoint_id": result["checkpoint_id"],
        "control_packet_id": control_packet["packet_id"],
        "created": created,
        "new_evidence_gaps": gap_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze the fundamental handoff before technical analysis.")
    parser.add_argument("--babis", default="output/babis_shortlist_latest.json")
    parser.add_argument("--giannis", default="output/giannis_rule_based_latest.json")
    parser.add_argument("--petros", default="output/petros_review_latest.json")
    parser.add_argument("--deep-research", default="output/company_deep_research_latest.json")
    parser.add_argument("--out-prefix", default="output/fundamental_research_packet")
    parser.add_argument("--control-plane-db", default="data/market_intel.db")
    args = parser.parse_args()
    paths = {
        "babis": Path(args.babis),
        "giannis": Path(args.giannis),
        "petros": Path(args.petros),
        "deep_research": Path(args.deep_research),
    }
    payload = build_packet(
        load(paths["babis"]),
        load(paths["giannis"]),
        load(paths["petros"]),
        deep_research=load(paths["deep_research"]),
        source_paths=paths,
    )
    run_path, latest_path, md_path = write_packet(payload, Path(args.out_prefix))
    control_plane = publish_control_plane(payload, Path(args.control_plane_db))
    print(f"[FUNDAMENTAL] frozen={payload['row_count']} technical_review={payload['technical_review_eligible_count']} execution_ready={payload['execution_ready_count']}")
    print(f"[OUTPUT] RUN: {run_path}")
    print(f"[OUTPUT] LATEST: {latest_path}")
    print(f"[OUTPUT] MD: {md_path}")
    print(f"[CONTROL-PLANE] {json.dumps(control_plane, ensure_ascii=False, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
