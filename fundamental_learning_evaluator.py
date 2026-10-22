"""Read-only fundamental quality evaluator and versioned learning-memory writer.

The evaluator never changes an agent decision, score, threshold, model state or
trading policy.  It grades evidence completeness from an independent rubric,
stores versioned evaluation memories and proposes candidate lessons only after
the same missing criterion is observed repeatedly.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable

import agent_learning_control_plane as learning
import market_intel_db


ROOT = Path(__file__).resolve().parent
EVALUATOR_ID = "fundamental_evidence_evaluator"
RUBRIC_VERSION = "fundamental-evidence-rubric.v1"
MIN_LESSON_SUPPORT = 20
AGENT_TASKS = {
    "babis": "discovery",
    "giannis": "fundamental_quality",
    "petros": "fundamental_synthesis",
}


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _nonempty_list_or_explicit_unknown(value: Any) -> bool:
    return isinstance(value, list) or str(value or "").lower() in {"unknown", "not_available"}


RUBRICS: dict[str, tuple[tuple[str, Callable[[dict[str, Any]], bool], str], ...]] = {
    "babis": (
        ("ticker_identity", lambda row: _present(row.get("ticker")), "Require a canonical ticker identity."),
        ("source_lineage", lambda row: isinstance(row.get("source_base"), dict), "Require source lineage in source_base."),
        ("coverage_measure", lambda row: _number(row.get("fundamental_coverage")), "Require numeric fundamental coverage."),
        ("unknown_contract", lambda row: _present(row.get("unknown_contract_sha256")), "Require the explicit unknown-data contract."),
        ("classification", lambda row: _present(row.get("classification_status")), "Require instrument classification status."),
    ),
    "giannis": (
        ("ticker_identity", lambda row: _present(row.get("ticker")), "Require a canonical ticker identity."),
        ("deterministic_score", lambda row: _number(row.get("score_final")), "Require a deterministic final score."),
        ("decision_tier", lambda row: _present(row.get("giannis_tier")), "Require an explicit fundamental tier."),
        ("risk_label", lambda row: _present(row.get("risk_level")), "Require an explicit risk label."),
        ("unknown_contract", lambda row: _present(row.get("unknown_contract_sha256")), "Require the explicit unknown-data contract."),
    ),
    "petros": (
        ("ticker_identity", lambda row: _present(row.get("ticker")), "Require a canonical ticker identity."),
        ("synthesis_verdict", lambda row: _present(row.get("petros_verdict")), "Require an explicit synthesis verdict."),
        ("rationale", lambda row: _nonempty_list_or_explicit_unknown(row.get("rationale")), "Require rationale or an explicit unknown marker."),
        ("research_readiness", lambda row: _present(row.get("research_readiness")), "Require an explicit research-readiness state."),
        ("valuation_state", lambda row: _present(row.get("valuation_range")) or _present(row.get("deterministic_valuation")), "Require a valuation result or explicit valuation state."),
        ("unknown_contract", lambda row: _present(row.get("unknown_contract_sha256")), "Require the explicit unknown-data contract."),
    ),
}


def evaluate(agent_id: str, decision: dict[str, Any]) -> tuple[str, float, list[dict[str, Any]]]:
    rubric = RUBRICS[agent_id]
    criteria = []
    for criterion_id, check, lesson in rubric:
        passed = bool(check(decision))
        criteria.append({"criterion_id": criterion_id, "passed": passed,
                         "lesson_if_repeated": lesson})
    score = sum(int(row["passed"]) for row in criteria) / len(criteria)
    verdict = "pass" if score == 1.0 else "partial" if score > 0.0 else "fail"
    return verdict, score, criteria


def sync_matured_outcomes(conn: Any) -> dict[str, int]:
    """Attach already observed market outcomes to matching immutable episodes."""
    try:
        rows = conn.execute(
            "SELECT e.episode_id,e.agent_id,po.horizon_days,po.observed_at,po.return_pct,"
            "po.target_session_date,po.price_session_date,po.price_source,po.price_adjustment "
            "FROM agent_learning_episodes e "
            "JOIN candidate_setups cs ON cs.run_id=e.run_id AND UPPER(cs.ticker)=UPPER(e.subject_id) "
            "JOIN price_outcomes po ON po.setup_id=cs.id "
            "WHERE e.agent_id IN ('babis','giannis','petros') AND po.return_pct IS NOT NULL "
            "ORDER BY e.episode_id,po.horizon_days"
        ).fetchall()
    except sqlite3.OperationalError:
        return {"available": 0, "inserted": 0, "existing": 0}
    inserted = existing = 0
    for row in rows:
        evidence = {
            "schema": "ecosystem.market_outcome_evidence.v1",
            "agent_id": row["agent_id"],
            "return_pct": float(row["return_pct"]),
            "target_session_date": row["target_session_date"],
            "price_session_date": row["price_session_date"],
            "price_source": row["price_source"],
            "price_adjustment": row["price_adjustment"],
            "point_in_time": True,
        }
        _, created = learning.record_outcome(
            conn, episode_id=str(row["episode_id"]), objective_id="market_return_pct",
            horizon_days=int(row["horizon_days"]), matured_at=str(row["observed_at"]),
            label=float(row["return_pct"]), label_confidence=1.0, evidence=evidence,
        )
        inserted += int(created)
        existing += int(not created)
    return {"available": len(rows), "inserted": inserted, "existing": existing}


def run(*, run_id: str, db_path: Path, output_path: Path,
        minimum_lesson_support: int = MIN_LESSON_SUPPORT) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    failures: Counter[tuple[str, str]] = Counter()
    with closing(market_intel_db.connect(db_path)) as conn:
        learning.init_schema(conn)
        outcome_sync = sync_matured_outcomes(conn)
        rows = conn.execute(
            "SELECT episode_id,agent_id,task_id,subject_id,decision_json,decision_sha256 "
            "FROM agent_learning_episodes WHERE run_id=? AND agent_id IN ('babis','giannis','petros') "
            "ORDER BY agent_id,subject_id",
            (run_id,),
        ).fetchall()
        for row in rows:
            agent_id = str(row["agent_id"])
            decision = json.loads(row["decision_json"])
            verdict, score, criteria = evaluate(agent_id, decision)
            evidence = {
                "run_id": run_id, "decision_sha256": row["decision_sha256"],
                "point_in_time": True, "execution_authority": False,
            }
            evaluation_id, created = learning.record_evaluation(
                conn, episode_id=str(row["episode_id"]), evaluator_id=EVALUATOR_ID,
                rubric_version=RUBRIC_VERSION, verdict=verdict, score=score,
                criteria=criteria, evidence=evidence,
            )
            memory_content = {
                "schema": "ecosystem.agent_evaluation_memory.v1",
                "run_id": run_id, "agent_id": agent_id,
                "subject_id": row["subject_id"], "evaluation_id": evaluation_id,
                "rubric_version": RUBRIC_VERSION, "verdict": verdict,
                "score": score, "criteria": criteria,
            }
            _, memory_version_id, memory_created = learning.remember_version(
                conn, agent_id=agent_id, category="evidence_evaluation",
                subject_id=str(row["subject_id"]), memory_key=RUBRIC_VERSION,
                content=memory_content, evidence=evidence, created_by=EVALUATOR_ID,
            )
            for criterion in criteria:
                if not criterion["passed"]:
                    failures[(agent_id, str(criterion["criterion_id"]))] += 1
            results.append({
                "agent_id": agent_id, "subject_id": row["subject_id"],
                "evaluation_id": evaluation_id, "evaluation_created": created,
                "memory_version_id": memory_version_id,
                "memory_version_created": memory_created,
                "verdict": verdict, "score": score,
            })

        lessons = []
        for (agent_id, criterion_id), support_count in sorted(failures.items()):
            if support_count < minimum_lesson_support:
                continue
            rubric_row = next(item for item in RUBRICS[agent_id] if item[0] == criterion_id)
            proposal = {
                "schema": "ecosystem.agent_lesson_candidate.v1",
                "target_agent": agent_id, "criterion_id": criterion_id,
                "proposed_change": rubric_row[2],
                "activation": "candidate_only",
                "changes_active_scores": False,
                "requires_shadow_validation": True,
                "requires_manual_promotion": True,
            }
            lesson_id, created = learning.upsert_lesson_candidate(
                conn, target_agent=agent_id, task_id=AGENT_TASKS[agent_id],
                rubric_version=RUBRIC_VERSION, criterion_id=criterion_id,
                support_count=support_count, proposal=proposal,
                evidence={"run_id": run_id, "support_count": support_count,
                          "evaluation_count": len(results)},
            )
            lessons.append({"lesson_id": lesson_id, "created": created,
                            "agent_id": agent_id, "criterion_id": criterion_id,
                            "support_count": support_count, "status": "candidate"})

    verdict_counts = Counter(row["verdict"] for row in results)
    agent_counts = Counter(row["agent_id"] for row in results)
    payload = {
        "schema": "ecosystem.fundamental_learning_evaluation.v1",
        "run_id": run_id, "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluator_id": EVALUATOR_ID, "rubric_version": RUBRIC_VERSION,
        "scope": ["babis", "giannis", "petros"],
        "sokratis_role_changed": False,
        "active_decisions_changed": False, "model_states_changed": False,
        "evaluation_count": len(results), "agent_counts": dict(agent_counts),
        "verdict_counts": dict(verdict_counts), "lesson_candidates": lessons,
        "matured_outcomes": outcome_sync,
        "results": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--db", type=Path, default=market_intel_db.DEFAULT_DB_PATH)
    parser.add_argument("--output", type=Path,
                        default=Path("output/fundamental_learning_evaluation_latest.json"))
    parser.add_argument("--minimum-lesson-support", type=int, default=MIN_LESSON_SUPPORT)
    args = parser.parse_args()
    payload = run(run_id=args.run_id, db_path=args.db, output_path=args.output,
                  minimum_lesson_support=max(1, args.minimum_lesson_support))
    print(json.dumps({key: payload[key] for key in (
        "run_id", "evaluation_count", "agent_counts", "verdict_counts",
        "active_decisions_changed", "sokratis_role_changed")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
