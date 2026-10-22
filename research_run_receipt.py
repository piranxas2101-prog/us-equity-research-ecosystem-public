"""Build a compact evidence, lineage, gap and usage receipt for one research run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
from typing import Any

from control_plane_store import DEFAULT_CONTROL_PLANE_DB, connect
from research_asset_graph import ASSET_BY_KIND, evaluate_packet, graph_status

DECISION_OUTCOMES = {"verdict_not_execution_candidate", "high_risk"}
# Tracked progress, not a reason for review: a sector packet with 20-49 % of its framework answered.
TRACKED_NOT_ACTIONABLE = {"sector_evidence_partial"}
NOT_ACTIONABLE = DECISION_OUTCOMES | TRACKED_NOT_ACTIONABLE


def _rows(db: sqlite3.Connection, query: str, values: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(query, values).fetchall()]


def _evaluate_run_packets(db: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    """Evaluate every packet in a run, not only one representative per kind."""
    rows = db.execute(
        "SELECT * FROM ecosystem_evidence_packet_store WHERE run_id=? "
        "ORDER BY kind,subject_id,created_at,packet_id",
        (run_id,),
    ).fetchall()
    health: list[dict[str, Any]] = []
    for row in rows:
        body = json.loads(row["payload_json"])
        packet = {
            "packet_id": row["packet_id"], "kind": row["kind"],
            "subject_id": row["subject_id"], "run_id": row["run_id"],
            "as_of": row["as_of"], "producer_name": row["producer_name"],
            "producer_version": row["producer_version"],
            "quality_state": row["quality_state"], "content_hash": row["content_hash"],
            "created_at": row["created_at"], "body": body,
        }
        checks = evaluate_packet(db, packet, persist=True)
        error_failed = any(
            check["status"] == "failed" and check["severity"] == "error"
            for check in checks
        )
        warning_failed = any(
            check["status"] == "failed" and check["severity"] == "warning"
            for check in checks
        )
        status = "blocked" if error_failed else "degraded" if warning_failed else "healthy"
        spec = ASSET_BY_KIND.get(str(row["kind"]))
        health.append({
            "packet_id": row["packet_id"],
            "asset_key": spec.key if spec else str(row["kind"]),
            "kind": row["kind"], "subject_id": row["subject_id"], "status": status,
        })
    return health


def build_receipt(db: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    packet_health = _evaluate_run_packets(db, run_id)
    graph = graph_status(db, run_id=run_id, persist_checks=True)
    packets = _rows(
        db,
        "SELECT kind,quality_state,COUNT(*) AS count FROM ecosystem_evidence_packet_store "
        "WHERE run_id=? GROUP BY kind,quality_state ORDER BY kind,quality_state",
        (run_id,),
    )
    gaps = _rows(
        db,
        "SELECT status,reason_code,COUNT(*) AS count FROM ecosystem_control_plane_gap "
        "WHERE run_id=? GROUP BY status,reason_code ORDER BY status,reason_code",
        (run_id,),
    )
    usage = _rows(
        db,
        "SELECT stage,provider,SUM(api_calls) AS api_calls,SUM(input_tokens) AS input_tokens,"
        "SUM(output_tokens) AS output_tokens,SUM(estimated_cost_usd) AS estimated_cost_usd,"
        "COUNT(*) AS events FROM ecosystem_usage_event WHERE run_id=? GROUP BY stage,provider ORDER BY stage,provider",
        (run_id,),
    )
    claims = db.execute(
        "SELECT COUNT(*) FROM ecosystem_claim_store WHERE run_id=?", (run_id,)
    ).fetchone()[0]
    theses = db.execute(
        "SELECT COUNT(*) FROM ecosystem_thesis_revision WHERE run_id=?", (run_id,)
    ).fetchone()[0]
    aliases = db.execute("SELECT COUNT(*) FROM ecosystem_subject_alias").fetchone()[0]
    checks = _rows(
        db,
        "SELECT status,severity,COUNT(*) AS count FROM ecosystem_asset_check_result "
        "WHERE packet_id IN (SELECT packet_id FROM ecosystem_evidence_packet_store WHERE run_id=?) "
        "GROUP BY status,severity ORDER BY status,severity",
        (run_id,),
    )
    packet_total = sum(int(row["count"]) for row in packets)
    open_gap_total = sum(int(row["count"]) for row in gaps if row["status"] == "open")
    decision_outcome_total = sum(
        int(row["count"]) for row in gaps
        if row["status"] == "open" and row["reason_code"] in DECISION_OUTCOMES
    )
    tracked_total = sum(
        int(row["count"]) for row in gaps
        if row["status"] == "open" and row["reason_code"] in TRACKED_NOT_ACTIONABLE
    )
    actionable_gap_total = open_gap_total - decision_outcome_total - tracked_total
    failed_error_checks = sum(
        int(row["count"]) for row in checks if row["status"] == "failed" and row["severity"] == "error"
    )
    packet_health_summary = {
        status: sum(item["status"] == status for item in packet_health)
        for status in ("healthy", "degraded", "blocked")
    }
    open_by_reason: dict[str, int] = {}
    for row in gaps:
        if row["status"] == "open" and row["reason_code"] not in NOT_ACTIONABLE:
            open_by_reason[row["reason_code"]] = open_by_reason.get(row["reason_code"], 0) + int(row["count"])
    # status stays as it was; the reasons say what keeps it there, so a change in the mix is visible run to run
    review_reasons = {
        "failed_error_checks": failed_error_checks,
        "actionable_gaps_by_reason": dict(sorted(open_by_reason.items(), key=lambda item: (-item[1], item[0]))),
    }
    return {
        "schema": "ecosystem.research_run_receipt.v1",
        "run_id": run_id,
        "status": "review_required" if failed_error_checks or actionable_gap_total else "ready",
        "packet_total": packet_total,
        "packet_breakdown": packets,
        "gap_total_open": open_gap_total,
        "actionable_gap_total": actionable_gap_total,
        "decision_outcome_total": decision_outcome_total,
        "tracked_not_actionable_total": tracked_total,
        "gap_breakdown": gaps,
        "review_reasons": review_reasons,
        "claim_count": int(claims),
        "thesis_revision_count": int(theses),
        "registered_alias_count": int(aliases),
        "usage": usage,
        "asset_checks": checks,
        "packet_health": packet_health,
        "packet_health_summary": packet_health_summary,
        "asset_graph": graph,
    }


def render_markdown(receipt: dict[str, Any]) -> str:
    lines = [
        "# Research Control-Plane Run Receipt", "",
        f"- Run ID: `{receipt['run_id']}`",
        f"- Status: **{receipt['status']}**",
        f"- Packets: {receipt['packet_total']}",
        f"- Open evidence gaps: {receipt['gap_total_open']}",
        f"- Actionable evidence gaps: {receipt['actionable_gap_total']}",
        f"- Decision outcomes (not data failures): {receipt['decision_outcome_total']}",
        f"- Tracked progress, not actionable (partial sector evidence): {receipt.get('tracked_not_actionable_total', 0)}",
        f"- Checkable claims: {receipt['claim_count']}",
        f"- Thesis revisions: {receipt['thesis_revision_count']}",
        f"- Registered subject aliases: {receipt['registered_alias_count']}",
        "", "## Packet coverage", "",
    ]
    for row in receipt["packet_breakdown"]:
        lines.append(f"- {row['kind']} / {row['quality_state']}: {row['count']}")
    reasons = receipt.get("review_reasons") or {}
    lines.extend(["", "## Why review is required", "",
                  f"- Failed error-level checks: {reasons.get('failed_error_checks', 0)}"])
    for reason, count in (reasons.get("actionable_gaps_by_reason") or {}).items():
        lines.append(f"- Open gaps · {reason}: {count}")
    lines.extend(["", "## Evidence gaps", ""])
    if receipt["gap_breakdown"]:
        for row in receipt["gap_breakdown"]:
            lines.append(f"- {row['status']} / {row['reason_code']}: {row['count']}")
    else:
        lines.append("- None recorded for this run.")
    lines.extend(["", "## Usage ledger", ""])
    if receipt["usage"]:
        for row in receipt["usage"]:
            lines.append(
                f"- {row['stage']} / {row['provider'] or 'local'}: "
                f"{row['api_calls'] or 0} API calls, {row['input_tokens'] or 0} input tokens, "
                f"{row['output_tokens'] or 0} output tokens, ${float(row['estimated_cost_usd'] or 0):.4f} estimated"
            )
    else:
        lines.append("- No usage events recorded.")
    lines.extend(["", "## All packet health", ""])
    summary = receipt["packet_health_summary"]
    lines.append(
        f"- Healthy: {summary['healthy']} · Degraded: {summary['degraded']} · "
        f"Blocked: {summary['blocked']}"
    )
    lines.extend(["", "## Asset graph", ""])
    for node in receipt["asset_graph"]["nodes"]:
        lines.append(f"- {node['asset_key']}: {node['status']}")
    lines.extend([
        "", "## Interpretation", "",
        "`ready` means no unresolved run-scoped evidence gaps or failed error-level asset checks were found. ",
        "Warnings and conditional packets remain visible and are not silently converted into facts.",
    ])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_CONTROL_PLANE_DB)
    parser.add_argument("--json", type=Path, default=Path("output/research_run_receipt_latest.json"))
    parser.add_argument("--markdown", type=Path, default=Path("output/research_run_receipt_latest.md"))
    args = parser.parse_args(argv)
    db = connect(args.db)
    try:
        receipt = build_receipt(db, args.run_id)
    finally:
        db.close()
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    args.markdown.write_text(render_markdown(receipt), encoding="utf-8")
    print(json.dumps({"run_id": args.run_id, "status": receipt["status"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
