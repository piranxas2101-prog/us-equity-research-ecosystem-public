"""Create the owner review report after Babis and sector activation finish."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from research_registry.core import DEFAULT_DB, PROJECT
from sector_ecosystem_integration import ROOT as SECTOR_ROOT


REPORT_ROOT = PROJECT / "output" / "ecosystem_review"


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def db_snapshot(db_path: Path, activation_id: str, enrichment_job_id: str | None,
                babis_run_id: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    db = sqlite3.connect(db_path)
    try:
        result["registered_agents"] = dict(db.execute(
            "SELECT agent_type,COUNT(*) FROM ecosystem_agent_registration GROUP BY agent_type"
        ))
        result["sector_queue_status"] = dict(db.execute(
            "SELECT status,COUNT(*) FROM sector_analysis_queue WHERE activation_id=? GROUP BY status", (activation_id,)
        ))
        result["sector_queue_by_agent"] = dict(db.execute(
            "SELECT agent_id,COUNT(*) FROM sector_analysis_queue WHERE activation_id=? AND agent_id IS NOT NULL GROUP BY agent_id ORDER BY agent_id",
            (activation_id,),
        ))
        result["information_categories"] = dict(db.execute(
            "SELECT owner,COUNT(*) FROM research_information_category GROUP BY owner ORDER BY owner"
        ))
        if enrichment_job_id:
            result["evidence_gaps"] = dict(db.execute(
                "SELECT resolution_state,COUNT(*) FROM ecosystem_evidence_gap WHERE source_run_id=? GROUP BY resolution_state",
                (enrichment_job_id,),
            ))
            result["evidence_gap_reasons"] = dict(db.execute(
                "SELECT reason_code,COUNT(*) FROM ecosystem_evidence_gap WHERE source_run_id=? GROUP BY reason_code ORDER BY reason_code",
                (enrichment_job_id,),
            ))
        if babis_run_id and db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='babis_completion_run'"
        ).fetchone():
            row = db.execute(
                "SELECT status,applied_count,no_change_count,deferred_count,completed_at "
                "FROM babis_completion_run WHERE problem_run_id=? ORDER BY completed_at DESC LIMIT 1",
                (babis_run_id,),
            ).fetchone()
            if row:
                result["babis_completion"] = {
                    "status": row[0], "classification_applied": row[1],
                    "resolved_no_change": row[2], "deferred_notes": row[3],
                    "completed_at": row[4],
                }
    finally:
        db.close()
    return result


def build_report(babis_job: Path, db_path: Path = DEFAULT_DB, out_root: Path = REPORT_ROOT) -> dict[str, Any]:
    babis_job = Path(babis_job).resolve()
    state = read_json(babis_job / "state.json", {})
    if state.get("status") != "finished_with_review_required":
        raise ValueError("The post-run report requires a completed Babis pipeline")
    latest = read_json(SECTOR_ROOT / "latest.json", {})
    activation_folder = Path(latest.get("activation") or "")
    sector = read_json(activation_folder / "summary.json", {}) if activation_folder else {}
    if not sector or sector.get("babis_pipeline_job") != str(babis_job):
        raise ValueError("Sector activation for this Babis job is missing")
    enrichment_folder = Path(state.get("enrichment_job") or "")
    enrichment = read_json(enrichment_folder / "state.json", {}) if enrichment_folder else {}
    db_data = db_snapshot(db_path, sector["activation_id"], state.get("enrichment_job_id"), state.get("sec_job_id"))
    registry = read_json(PROJECT / "sector_agent_registry.json", {})
    info = read_json(PROJECT / "research_information_taxonomy.json", {})
    stamp = datetime.now(timezone.utc)
    report_id = "ecosystem-post-babis-" + stamp.strftime("%Y%m%dT%H%M%SZ")
    folder = Path(out_root) / report_id
    folder.mkdir(parents=True, exist_ok=False)
    payload = {
        "report_id": report_id, "generated_at": stamp.isoformat(),
        "babis": state, "enrichment": enrichment, "sector_activation": sector,
        "database": db_data, "sector_registry_version": registry.get("version"),
        "information_taxonomy_version": info.get("version"),
        "review_posture": "architecture_and_run_review_not_investment_recommendation",
    }
    (folder / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Ecosystem post-Babis review",
        "",
        f"Generated: `{payload['generated_at']}`",
        f"Babis pipeline: `{babis_job.name}`",
        f"Babis status: `{state.get('status')}`",
        f"Sector activation: `{sector.get('status')}`",
        "",
        "## Τι εκτελέστηκε",
        "",
        "1. Ο Μπάμπης φόρτωσε το σταθερό symbol snapshot και επιβεβαίωσε identity, instrument type και taxonomy state.",
        "2. Το SEC/catalog στάδιο δημιούργησε classification proposals και οργανωμένο problem registry.",
        "3. Το supplemental enrichment χρησιμοποίησε μόνο τα επιτρεπόμενα provider budgets, χωρίς retries ή LLM calls.",
        "4. Οι αποτυχίες πηγών αποθηκεύτηκαν ως reason-coded evidence gaps και όχι ως αρνητικά εταιρικά ευρήματα.",
        "5. Οι 11 κανονικοί sector agents και ο ειδικός unresolved-classification agent εγγράφηκαν στο ecosystem.",
        "6. Οι πληροφορίες διαχωρίστηκαν σε classification, sector context, financial analysis και evidence control.",
        "7. Ο Γιάννης έλαβε contract ιδιοκτησίας για normalized financials και χρηματοοικονομική αξιολόγηση.",
        "",
        "## Babis run",
        "",
        f"- SEC run: `{state.get('sec_job_id')}`",
        f"- Enrichment run: `{state.get('enrichment_job_id')}`",
        f"- Enrichment symbols: **{enrichment.get('processed_symbols', 0)} / {enrichment.get('total_symbols', 0)}**",
        f"- Source requests: **{enrichment.get('source_requests', 0)}**",
        f"- Retries: **{enrichment.get('retries', 0)}**",
        f"- LLM calls: **{enrichment.get('llm_calls', 0)}**",
        f"- Automatic classification writes: **{enrichment.get('registry_writes', 0)}**",
        f"- Approved Babis completion writes: **{(db_data.get('babis_completion') or {}).get('classification_applied', 0)}**",
        f"- Deferred evidence notes: **{(db_data.get('babis_completion') or {}).get('deferred_notes', 0)}**",
        "",
        "## Sector-agent activation",
        "",
        f"- Registered analysis agents: **{sector.get('registered_agents', 0)}**",
        f"- Normal sector agents: **{sector.get('sector_agents', 0)}**",
        f"- Classification-resolution agents: **{sector.get('special_agents', 0)}**",
        f"- Subcategory frameworks: **{sum(len(v.get('subcategories', {})) for v in registry.get('agents', {}).values())}**",
        f"- Ready sector queue items: **{sector.get('queued', 0)}**",
        f"- Unresolved-classification queue items: **{sector.get('unresolved_queued', 0)}**",
        f"- Removed non-corporate instruments: **{sector.get('removed_non_corporate', 0)}**",
        f"- Blocked queue items: **{sector.get('blocked', 0)}**",
        f"- Information categories: **{sector.get('information_categories', 0)}**",
        "",
        "| Agent | Queue items |",
        "| --- | ---: |",
    ]
    lines += [f"| `{agent}` | {count} |" for agent, count in sorted((sector.get("by_agent") or {}).items())]
    lines += ["", "## Information ownership", "", "| Owner | Categories |", "| --- | ---: |"]
    lines += [f"| `{owner}` | {count} |" for owner, count in sorted((db_data.get("information_categories") or {}).items())]
    lines += ["", "## Evidence gaps", "", "| State | Count |", "| --- | ---: |"]
    lines += [f"| `{key}` | {value} |" for key, value in sorted((db_data.get("evidence_gaps") or {}).items())]
    lines += ["", "### Gap reasons", "", "| Reason | Count |", "| --- | ---: |"]
    lines += [f"| `{key}` | {value} |" for key, value in sorted((db_data.get("evidence_gap_reasons") or {}).items())]
    lines += [
        "", "## Πώς λειτουργεί πλέον το οικοσύστημα", "",
        "`sources -> Babis classification -> sector queue -> Sector Context Packet -> Giannis Financial Analysis Packet -> future synthesis -> dashboard`",
        "",
        "`unresolved symbol -> unresolved-classification agent -> sourced proposal -> Babis review -> normal sector queue`",
        "",
        "Ο Μπάμπης δεν αναλύει οικονομικές καταστάσεις. Οι sector agents δεν αλλάζουν classification και δεν εκδίδουν χρηματοοικονομικό verdict. Ο Γιάννης δεν ξαναγράφει sector evidence. Όλα τα επίπεδα διατηρούν κοινό run lineage, sources, timestamps, confidence και gaps.",
        "",
        "## Θέματα για αυριανή αναβάθμιση", "",
        "1. Αναλύουμε την ειδική unresolved-classification ουρά και επιστρέφουμε τεκμηριωμένες προτάσεις στον Μπάμπη.",
        "2. Μετράμε την πραγματική κάλυψη ανά sector agent και υποκατηγορία.",
        "3. Ορίζουμε source adapters για business model, peer group και sector KPIs χωρίς full-universe επαναλήψεις.",
        "4. Αναβαθμίζουμε το runtime του Γιάννη ώστε να καταναλώνει normalized statements και Sector Context Packets.",
        "5. Συνδέουμε τα packets με την canonical symbol-analysis σελίδα του dashboard.",
        "6. Εξετάζουμε provider costs, rate limits, cache reuse και incremental refresh policy.",
        "7. Καθορίζουμε acceptance gates πριν αρχίσει πραγματική μαζική sector ανάλυση.",
        "",
        "## Περιορισμός",
        "",
        "Η ενεργοποίηση και η δημιουργία queues δεν σημαίνουν ότι οι sector agents ολοκλήρωσαν εταιρική ανάλυση. Τα πραγματικά Sector Context Packets θα παραχθούν μόνο από τεκμηριωμένες πηγές και θα παραμένουν review-required όταν λείπουν στοιχεία.",
    ]
    report_path = folder / "ECOSYSTEM_REVIEW.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (Path(out_root) / "latest.json").write_text(json.dumps({"report":str(report_path),"report_id":report_id}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status":"report_created","report_id":report_id,"report":str(report_path),"json":str(folder / "report.json")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--babis-job", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)
    print(json.dumps(build_report(args.babis_job, args.db), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__": raise SystemExit(main())
