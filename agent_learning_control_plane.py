"""Local, auditable learning memory shared by ecosystem agents.

The control plane stores immutable decision-time episodes, delayed outcomes,
feature definitions and safe JSON model versions.  It never executes trades and
never promotes a model automatically.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import market_intel_db


SCHEMA_VERSION = "ecosystem.agent_learning.v1"
ALLOWED_MODEL_STATES = {"candidate", "shadow", "champion", "rejected", "frozen"}
ALLOWED_TRANSITIONS = {
    "candidate": {"shadow", "rejected", "frozen"},
    "shadow": {"champion", "rejected", "frozen"},
    "champion": {"frozen"},
    "rejected": set(),
    "frozen": {"shadow", "rejected"},
}
DEFAULT_FEATURE_CONTRACTS = (
    ("decision_payload", "json", "shared", "Complete decision-time payload; future fields are forbidden."),
    ("source_artifact_sha256", "text", "shared", "Hash of the immutable run artifact."),
    ("market_outcome", "number", "petros", "Future return at the declared completed-session horizon."),
    ("discovery_quality", "number", "babis", "Evidence coverage and downstream acceptance objective."),
    ("fundamental_quality", "number", "giannis", "Thesis quality, completeness and calibrated outcome objective."),
)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def init_schema(conn: sqlite3.Connection) -> None:
    market_intel_db.init_db(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS agent_learning_episodes (
            episode_id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            available_at TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            model_version TEXT,
            confidence REAL,
            context_sha256 TEXT NOT NULL,
            decision_sha256 TEXT NOT NULL,
            context_json TEXT NOT NULL,
            decision_json TEXT NOT NULL,
            source_path TEXT,
            recorded_at TEXT NOT NULL,
            UNIQUE(agent_id, task_id, run_id, subject_id)
        );
        CREATE INDEX IF NOT EXISTS idx_learning_episode_agent
            ON agent_learning_episodes(agent_id, task_id, observed_at);
        CREATE INDEX IF NOT EXISTS idx_learning_episode_subject
            ON agent_learning_episodes(subject_id, observed_at);

        CREATE TABLE IF NOT EXISTS agent_learning_outcomes (
            outcome_id TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL REFERENCES agent_learning_episodes(episode_id),
            objective_id TEXT NOT NULL,
            horizon_days INTEGER NOT NULL,
            matured_at TEXT NOT NULL,
            label REAL NOT NULL,
            label_confidence REAL NOT NULL,
            evidence_sha256 TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            UNIQUE(episode_id, objective_id, horizon_days)
        );

        CREATE TABLE IF NOT EXISTS agent_feature_registry (
            feature_id TEXT PRIMARY KEY,
            feature_name TEXT NOT NULL,
            feature_version TEXT NOT NULL,
            value_type TEXT NOT NULL,
            owner_agent TEXT NOT NULL,
            event_time_field TEXT NOT NULL,
            available_time_field TEXT NOT NULL,
            source_lineage TEXT NOT NULL,
            definition_json TEXT NOT NULL,
            definition_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(feature_name, feature_version, owner_agent)
        );

        CREATE TABLE IF NOT EXISTS agent_model_registry (
            model_id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            model_version TEXT NOT NULL,
            model_type TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('candidate','shadow','champion','rejected','frozen')),
            model_path TEXT NOT NULL,
            model_sha256 TEXT NOT NULL,
            dataset_sha256 TEXT NOT NULL,
            feature_schema_sha256 TEXT NOT NULL,
            training_rows INTEGER NOT NULL,
            metrics_json TEXT NOT NULL,
            gates_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(agent_id, task_id, model_version)
        );
        CREATE INDEX IF NOT EXISTS idx_agent_model_state
            ON agent_model_registry(agent_id, task_id, state);

        CREATE TABLE IF NOT EXISTS agent_model_state_events (
            event_id TEXT PRIMARY KEY,
            model_id TEXT NOT NULL REFERENCES agent_model_registry(model_id),
            previous_state TEXT NOT NULL,
            new_state TEXT NOT NULL,
            reason TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agent_prediction_log (
            prediction_id TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL REFERENCES agent_learning_episodes(episode_id),
            model_id TEXT NOT NULL REFERENCES agent_model_registry(model_id),
            prediction REAL NOT NULL,
            confidence REAL,
            feature_snapshot_sha256 TEXT NOT NULL,
            feature_snapshot_json TEXT NOT NULL,
            predicted_at TEXT NOT NULL,
            UNIQUE(episode_id, model_id)
        );

        CREATE TABLE IF NOT EXISTS agent_episode_evaluations (
            evaluation_id TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL REFERENCES agent_learning_episodes(episode_id),
            evaluator_id TEXT NOT NULL,
            rubric_version TEXT NOT NULL,
            verdict TEXT NOT NULL CHECK(verdict IN ('pass','partial','fail')),
            score REAL NOT NULL,
            criteria_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            evaluation_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(episode_id, evaluator_id, rubric_version)
        );

        CREATE TABLE IF NOT EXISTS agent_memory_items (
            memory_id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            category TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            memory_key TEXT NOT NULL,
            current_version_id TEXT,
            status TEXT NOT NULL CHECK(status IN ('active','superseded','rejected','archived')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(agent_id, category, subject_id, memory_key)
        );

        CREATE TABLE IF NOT EXISTS agent_memory_versions (
            version_id TEXT PRIMARY KEY,
            memory_id TEXT NOT NULL REFERENCES agent_memory_items(memory_id),
            version_number INTEGER NOT NULL,
            content_sha256 TEXT NOT NULL,
            content_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(memory_id, version_number),
            UNIQUE(memory_id, content_sha256)
        );

        CREATE TABLE IF NOT EXISTS agent_lesson_candidates (
            lesson_id TEXT PRIMARY KEY,
            target_agent TEXT NOT NULL,
            task_id TEXT NOT NULL,
            rubric_version TEXT NOT NULL,
            criterion_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('candidate','shadow','accepted','rejected','archived')),
            support_count INTEGER NOT NULL,
            proposal_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            proposal_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(target_agent, task_id, rubric_version, criterion_id)
        );
        """
    )
    for name, value_type, owner, description in DEFAULT_FEATURE_CONTRACTS:
        register_feature(
            conn, feature_name=name, feature_version="v1", value_type=value_type,
            owner_agent=owner, event_time_field="observed_at",
            available_time_field="available_at", source_lineage="immutable_run_artifact",
            definition={"description": description, "point_in_time_required": True},
            commit=False,
        )
    conn.commit()


def register_feature(conn: sqlite3.Connection, *, feature_name: str, feature_version: str,
                     value_type: str, owner_agent: str, event_time_field: str,
                     available_time_field: str, source_lineage: str,
                     definition: dict[str, Any], commit: bool = True) -> str:
    identity = {"name": feature_name, "version": feature_version, "owner": owner_agent}
    feature_id = "feature_" + digest(identity)[:24]
    definition_hash = digest(definition)
    existing = conn.execute(
        "SELECT definition_sha256 FROM agent_feature_registry WHERE feature_id=?", (feature_id,)
    ).fetchone()
    if existing and existing[0] != definition_hash:
        raise ValueError(f"Registered feature definition changed: {feature_id}")
    if not existing:
        conn.execute(
            "INSERT INTO agent_feature_registry VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (feature_id, feature_name, feature_version, value_type, owner_agent,
             event_time_field, available_time_field, source_lineage,
             canonical_json(definition), definition_hash, now_utc()),
        )
    if commit:
        conn.commit()
    return feature_id


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def record_episode(conn: sqlite3.Connection, *, agent_id: str, task_id: str, run_id: str,
                   subject_id: str, observed_at: str, context: dict[str, Any],
                   decision: dict[str, Any], policy_version: str, source_path: str = "",
                   available_at: str | None = None, model_version: str | None = None,
                   confidence: float | None = None) -> tuple[str, bool]:
    locked = {
        "agent_id": agent_id, "task_id": task_id, "run_id": run_id,
        "subject_id": subject_id.upper(), "observed_at": observed_at,
        "context": context, "decision": decision, "policy_version": policy_version,
    }
    episode_id = "episode_" + digest(locked)[:24]
    existing = conn.execute(
        "SELECT episode_id, context_sha256, decision_sha256 FROM agent_learning_episodes "
        "WHERE agent_id=? AND task_id=? AND run_id=? AND subject_id=?",
        (agent_id, task_id, run_id, subject_id.upper()),
    ).fetchone()
    context_hash, decision_hash = digest(context), digest(decision)
    if existing:
        if existing[1] != context_hash or existing[2] != decision_hash:
            raise ValueError(f"Immutable learning episode changed: {agent_id}/{run_id}/{subject_id}")
        return str(existing[0]), False
    conn.execute(
        """INSERT INTO agent_learning_episodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (episode_id, agent_id, task_id, run_id, subject_id.upper(), observed_at,
         available_at or observed_at, policy_version, model_version, confidence,
         context_hash, decision_hash, canonical_json(context), canonical_json(decision),
         source_path, now_utc()),
    )
    return episode_id, True


def record_artifact(conn: sqlite3.Connection, path: Path, agent_id: str, task_id: str,
                    rows_key: str, policy_version: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("Artifact root must be an object")
    rows = payload.get(rows_key, [])
    if not isinstance(rows, list):
        raise ValueError(f"Artifact field {rows_key!r} must be a list")
    run_id = str(payload.get("run_id") or "")
    observed_at = str(payload.get("run_timestamp") or payload.get("generated_at") or "")
    if not run_id or not observed_at:
        raise ValueError("Artifact requires run_id and run_timestamp/generated_at")
    artifact_hash = digest(payload)
    prepared: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    decisions_by_subject: dict[str, str] = {}
    skipped = 0
    valid_rows: list[tuple[str, dict[str, Any]]] = []
    for row in rows:
        if not isinstance(row, dict):
            skipped += 1
            continue
        subject = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
        if not subject:
            skipped += 1
            continue
        valid_rows.append((subject, row))

    subject_counts: dict[str, int] = {}
    for subject, _ in valid_rows:
        subject_counts[subject] = subject_counts.get(subject, 0) + 1
    for base_subject, row in valid_rows:
        decision_hash = digest(row)
        # Strategy artifacts can legitimately contain more than one playbook
        # for a ticker.  Give each distinct decision a stable episode identity
        # instead of treating the second playbook as a mutation of the first.
        subject = (
            f"{base_subject}::variant:{decision_hash[:16]}"
            if subject_counts[base_subject] > 1 else base_subject
        )
        # Episode identity is row-local.  The whole-artifact hash belongs to
        # lineage metadata: using it as context made an unchanged ticker look
        # changed whenever another row in the same artifact changed.
        context = {
            "schema": SCHEMA_VERSION,
            "source_row_sha256": digest(row),
            "source_agent": agent_id,
            "subject_id": subject,
        }
        previous_hash = decisions_by_subject.get(subject)
        if previous_hash is not None and previous_hash != decision_hash:
            raise ValueError(f"Duplicate subject has conflicting decisions: {agent_id}/{run_id}/{subject}")
        if previous_hash is None:
            prepared.append((subject, context, row))
            decisions_by_subject[subject] = decision_hash

    # Validate the complete artifact before writing any row.  A real conflict
    # therefore cannot leave a partially recorded agent artifact.
    for subject, context, decision in prepared:
        found = conn.execute(
            "SELECT context_json,decision_sha256 FROM agent_learning_episodes "
            "WHERE agent_id=? AND task_id=? AND run_id=? AND subject_id=?",
            (agent_id, task_id, run_id, subject),
        ).fetchone()
        if found and str(found[1]) != digest(decision):
            raise ValueError(f"Immutable learning episode changed: {agent_id}/{run_id}/{subject}")

    inserted = existing = 0
    conn.execute("SAVEPOINT record_agent_artifact")
    try:
        for subject, context, decision in prepared:
            found = conn.execute(
                "SELECT episode_id,decision_sha256 FROM agent_learning_episodes "
                "WHERE agent_id=? AND task_id=? AND run_id=? AND subject_id=?",
                (agent_id, task_id, run_id, subject),
            ).fetchone()
            if found:
                # Backward-compatible acceptance of an old artifact-level
                # context when the immutable decision itself is identical.
                existing += 1
                continue
            _, created = record_episode(
                conn, agent_id=agent_id, task_id=task_id, run_id=run_id,
                subject_id=subject, observed_at=observed_at, context=context,
                decision=decision, policy_version=policy_version,
                source_path=str(path.resolve()),
            )
            inserted += int(created)
            existing += int(not created)
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT record_agent_artifact")
        conn.execute("RELEASE SAVEPOINT record_agent_artifact")
        raise
    else:
        conn.execute("RELEASE SAVEPOINT record_agent_artifact")
        conn.commit()
    return {"agent_id": agent_id, "run_id": run_id, "inserted": inserted,
            "existing": existing, "skipped": skipped,
            "source_artifact_sha256": artifact_hash}


def record_outcome(conn: sqlite3.Connection, *, episode_id: str, objective_id: str,
                   horizon_days: int, matured_at: str, label: float,
                   label_confidence: float, evidence: dict[str, Any]) -> tuple[str, bool]:
    if not 0.0 <= label_confidence <= 1.0:
        raise ValueError("label_confidence must be between 0 and 1")
    if not conn.execute("SELECT 1 FROM agent_learning_episodes WHERE episode_id=?", (episode_id,)).fetchone():
        raise ValueError(f"Unknown episode: {episode_id}")
    key = {"episode_id": episode_id, "objective_id": objective_id, "horizon_days": horizon_days}
    outcome_id = "outcome_" + digest(key)[:24]
    existing = conn.execute("SELECT label,evidence_sha256 FROM agent_learning_outcomes WHERE outcome_id=?", (outcome_id,)).fetchone()
    evidence_hash = digest(evidence)
    if existing:
        if float(existing[0]) != float(label) or existing[1] != evidence_hash:
            raise ValueError(f"Immutable outcome changed: {outcome_id}")
        return outcome_id, False
    conn.execute("INSERT INTO agent_learning_outcomes VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (outcome_id, episode_id, objective_id, horizon_days, matured_at, float(label),
                  float(label_confidence), evidence_hash, canonical_json(evidence), now_utc()))
    conn.commit()
    return outcome_id, True


def record_evaluation(conn: sqlite3.Connection, *, episode_id: str, evaluator_id: str,
                      rubric_version: str, verdict: str, score: float,
                      criteria: list[dict[str, Any]], evidence: dict[str, Any]) -> tuple[str, bool]:
    if verdict not in {"pass", "partial", "fail"}:
        raise ValueError(f"Unknown evaluation verdict: {verdict}")
    if not 0.0 <= float(score) <= 1.0:
        raise ValueError("evaluation score must be between 0 and 1")
    if not conn.execute("SELECT 1 FROM agent_learning_episodes WHERE episode_id=?", (episode_id,)).fetchone():
        raise ValueError(f"Unknown episode: {episode_id}")
    identity = {"episode_id": episode_id, "evaluator_id": evaluator_id,
                "rubric_version": rubric_version}
    evaluation_id = "evaluation_" + digest(identity)[:24]
    payload = {"verdict": verdict, "score": float(score), "criteria": criteria,
               "evidence": evidence}
    payload_hash = digest(payload)
    existing = conn.execute(
        "SELECT evaluation_sha256 FROM agent_episode_evaluations WHERE evaluation_id=?",
        (evaluation_id,),
    ).fetchone()
    if existing:
        if str(existing[0]) != payload_hash:
            raise ValueError(f"Immutable evaluation changed: {evaluation_id}")
        return evaluation_id, False
    conn.execute(
        "INSERT INTO agent_episode_evaluations VALUES (?,?,?,?,?,?,?,?,?,?)",
        (evaluation_id, episode_id, evaluator_id, rubric_version, verdict, float(score),
         canonical_json(criteria), canonical_json(evidence), payload_hash, now_utc()),
    )
    conn.commit()
    return evaluation_id, True


def remember_version(conn: sqlite3.Connection, *, agent_id: str, category: str,
                     subject_id: str, memory_key: str, content: dict[str, Any],
                     evidence: dict[str, Any], created_by: str) -> tuple[str, str, bool]:
    identity = {"agent_id": agent_id, "category": category,
                "subject_id": subject_id.upper(), "memory_key": memory_key}
    memory_id = "memory_" + digest(identity)[:24]
    stamp = now_utc()
    row = conn.execute(
        "SELECT current_version_id FROM agent_memory_items WHERE memory_id=?", (memory_id,)
    ).fetchone()
    if not row:
        conn.execute(
            "INSERT INTO agent_memory_items VALUES (?,?,?,?,?,?,?,?,?)",
            (memory_id, agent_id, category, subject_id.upper(), memory_key, None,
             "active", stamp, stamp),
        )
    content_hash = digest(content)
    existing = conn.execute(
        "SELECT version_id FROM agent_memory_versions WHERE memory_id=? AND content_sha256=?",
        (memory_id, content_hash),
    ).fetchone()
    if existing:
        return memory_id, str(existing[0]), False
    version_number = int(conn.execute(
        "SELECT COALESCE(MAX(version_number),0)+1 FROM agent_memory_versions WHERE memory_id=?",
        (memory_id,),
    ).fetchone()[0])
    version_id = "memver_" + digest({**identity, "version": version_number,
                                      "content_sha256": content_hash})[:24]
    conn.execute(
        "INSERT INTO agent_memory_versions VALUES (?,?,?,?,?,?,?,?)",
        (version_id, memory_id, version_number, content_hash, canonical_json(content),
         canonical_json(evidence), created_by, stamp),
    )
    conn.execute(
        "UPDATE agent_memory_items SET current_version_id=?,status='active',updated_at=? WHERE memory_id=?",
        (version_id, stamp, memory_id),
    )
    conn.commit()
    return memory_id, version_id, True


def upsert_lesson_candidate(conn: sqlite3.Connection, *, target_agent: str, task_id: str,
                            rubric_version: str, criterion_id: str, support_count: int,
                            proposal: dict[str, Any], evidence: dict[str, Any]) -> tuple[str, bool]:
    identity = {"target_agent": target_agent, "task_id": task_id,
                "rubric_version": rubric_version, "criterion_id": criterion_id}
    lesson_id = "lesson_" + digest(identity)[:24]
    proposal_hash, stamp = digest(proposal), now_utc()
    existing = conn.execute(
        "SELECT status,proposal_sha256,evidence_json FROM agent_lesson_candidates WHERE lesson_id=?", (lesson_id,)
    ).fetchone()
    if existing:
        # Evaluation may add evidence, but it cannot silently rewrite or promote
        # an already proposed lesson.
        if str(existing[1]) != proposal_hash:
            raise ValueError(f"Lesson candidate definition changed: {lesson_id}")
        decisions = json.loads(existing[2] or "{}").get("decisions")
        if decisions:  # the owner's decisions stay with the lesson when new evidence arrives
            evidence = {**evidence, "decisions": decisions}
        conn.execute(
            "UPDATE agent_lesson_candidates SET support_count=?,evidence_json=?,updated_at=? WHERE lesson_id=?",
            (int(support_count), canonical_json(evidence), stamp, lesson_id),
        )
        conn.commit()
        return lesson_id, False
    conn.execute(
        "INSERT INTO agent_lesson_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (lesson_id, target_agent, task_id, rubric_version, criterion_id, "candidate",
         int(support_count), canonical_json(proposal), canonical_json(evidence),
         proposal_hash, stamp, stamp),
    )
    conn.commit()
    return lesson_id, True


LESSON_TRANSITIONS = {"candidate": {"shadow", "rejected", "archived"}, "shadow": {"accepted", "rejected", "archived"},
                      "rejected": {"archived"}, "accepted": {"archived"}, "archived": set()}


def set_lesson_status(conn: sqlite3.Connection, lesson_id: str, status: str, *, reason: str) -> None:
    """The owner's decision on a lesson (candidate -> shadow -> accepted, or rejected). Never automatic: only an
    explicit call with a reason moves a lesson, and only along the allowed transitions."""
    row = conn.execute("SELECT status, evidence_json FROM agent_lesson_candidates WHERE lesson_id=?",
                       (lesson_id,)).fetchone()
    if row is None:
        raise KeyError(lesson_id)
    if status not in LESSON_TRANSITIONS.get(str(row[0]), set()):
        raise ValueError(f"lesson {lesson_id}: {row[0]} -> {status} is not allowed")
    evidence = json.loads(row[1] or "{}")
    evidence.setdefault("decisions", []).append({"from": row[0], "to": status, "reason": reason, "at": now_utc()})
    conn.execute("UPDATE agent_lesson_candidates SET status=?, evidence_json=?, updated_at=? WHERE lesson_id=?",
                 (status, canonical_json(evidence), now_utc(), lesson_id))
    conn.commit()


def register_model(conn: sqlite3.Connection, *, agent_id: str, task_id: str,
                   model_version: str, model_type: str, model_path: Path,
                   dataset_sha256: str, feature_schema_sha256: str,
                   training_rows: int, metrics: dict[str, Any], gates: dict[str, Any]) -> str:
    raw = model_path.read_bytes()
    # JSON-only is an explicit safety boundary: no executable pickle/joblib artifacts.
    json.loads(raw.decode("utf-8"))
    identity = {"agent_id": agent_id, "task_id": task_id, "model_version": model_version}
    model_id = "model_" + digest(identity)[:24]
    model_hash = hashlib.sha256(raw).hexdigest()
    existing = conn.execute("SELECT model_sha256 FROM agent_model_registry WHERE model_id=?", (model_id,)).fetchone()
    if existing:
        if existing[0] != model_hash:
            raise ValueError(f"Registered model bytes changed: {model_id}")
        return model_id
    created = now_utc()
    conn.execute(
        "INSERT INTO agent_model_registry VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (model_id, agent_id, task_id, model_version, model_type, "candidate",
         str(model_path.resolve()), model_hash, dataset_sha256, feature_schema_sha256,
         int(training_rows), canonical_json(metrics), canonical_json(gates), created, created),
    )
    conn.commit()
    return model_id


def transition_model(conn: sqlite3.Connection, model_id: str, new_state: str,
                     reason: str, evidence: dict[str, Any]) -> None:
    if new_state not in ALLOWED_MODEL_STATES:
        raise ValueError(f"Unknown model state: {new_state}")
    row = conn.execute("SELECT agent_id,task_id,state FROM agent_model_registry WHERE model_id=?", (model_id,)).fetchone()
    if not row:
        raise ValueError(f"Unknown model: {model_id}")
    old_state = str(row[2])
    if new_state not in ALLOWED_TRANSITIONS[old_state]:
        raise ValueError(f"Disallowed model transition: {old_state} -> {new_state}")
    if new_state == "champion":
        other = conn.execute(
            "SELECT model_id FROM agent_model_registry WHERE agent_id=? AND task_id=? AND state='champion' AND model_id<>?",
            (row[0], row[1], model_id),
        ).fetchone()
        if other:
            raise ValueError(f"Champion already exists: {other[0]}")
    event_payload = {"model_id": model_id, "from": old_state, "to": new_state,
                     "reason": reason, "evidence": evidence, "at": now_utc()}
    conn.execute("UPDATE agent_model_registry SET state=?, updated_at=? WHERE model_id=?",
                 (new_state, event_payload["at"], model_id))
    conn.execute("INSERT INTO agent_model_state_events VALUES (?,?,?,?,?,?,?)",
                 ("event_" + digest(event_payload)[:24], model_id, old_state, new_state,
                  reason, canonical_json(evidence), event_payload["at"]))
    conn.commit()


def summary(conn: sqlite3.Connection) -> dict[str, Any]:
    agents = [dict(row) for row in conn.execute(
        """SELECT agent_id, COUNT(*) episodes, COUNT(DISTINCT run_id) runs,
           MIN(observed_at) first_observed, MAX(observed_at) last_observed
           FROM agent_learning_episodes GROUP BY agent_id ORDER BY agent_id""").fetchall()]
    states = Counter({str(row[0]): int(row[1]) for row in conn.execute(
        "SELECT state,COUNT(*) FROM agent_model_registry GROUP BY state").fetchall()})
    return {"schema": SCHEMA_VERSION, "generated_at": now_utc(), "agents": agents,
            "episode_count": sum(int(row["episodes"]) for row in agents),
            "outcome_count": conn.execute("SELECT COUNT(*) FROM agent_learning_outcomes").fetchone()[0],
            "prediction_count": conn.execute("SELECT COUNT(*) FROM agent_prediction_log").fetchone()[0],
            "feature_count": conn.execute("SELECT COUNT(*) FROM agent_feature_registry").fetchone()[0],
            "evaluation_count": conn.execute("SELECT COUNT(*) FROM agent_episode_evaluations").fetchone()[0],
            "memory_version_count": conn.execute("SELECT COUNT(*) FROM agent_memory_versions").fetchone()[0],
            "lesson_candidate_count": conn.execute("SELECT COUNT(*) FROM agent_lesson_candidates WHERE status='candidate'").fetchone()[0],
            "model_states": dict(states)}


def write_dashboard(conn: sqlite3.Connection, output: Path) -> None:
    data = summary(conn)
    models = [dict(row) for row in conn.execute(
        "SELECT agent_id,task_id,model_version,model_type,state,training_rows,metrics_json,updated_at FROM agent_model_registry ORDER BY updated_at DESC"
    ).fetchall()]
    agent_rows = "".join(
        f"<tr><td>{html.escape(str(r['agent_id']))}</td><td>{r['episodes']}</td><td>{r['runs']}</td><td>{html.escape(str(r['last_observed']))}</td></tr>"
        for r in data["agents"]
    ) or "<tr><td colspan='4'>Δεν έχουν καταγραφεί επεισόδια ακόμη.</td></tr>"
    model_rows = "".join(
        f"<tr><td>{html.escape(str(r['agent_id']))}</td><td>{html.escape(str(r['task_id']))}</td><td>{html.escape(str(r['model_version']))}</td><td><b>{html.escape(str(r['state']))}</b></td><td>{r['training_rows']}</td><td><code>{html.escape(str(r['metrics_json']))}</code></td></tr>"
        for r in models
    ) or "<tr><td colspan='6'>Δεν υπάρχει καταχωρισμένο μοντέλο ακόμη.</td></tr>"
    page = f"""<!doctype html><html lang='el'><meta charset='utf-8'><title>Agent Learning</title>
    <style>body{{font:16px system-ui;margin:2rem;max-width:1200px;color:#172033}}h1{{margin-bottom:.2rem}}.cards{{display:flex;gap:1rem;flex-wrap:wrap}}.card{{padding:1rem 1.4rem;border:1px solid #ccd5e0;border-radius:12px;background:#f7f9fc}}table{{border-collapse:collapse;width:100%;margin:1rem 0 2rem}}th,td{{border-bottom:1px solid #dde3eb;padding:.65rem;text-align:left;vertical-align:top}}code{{white-space:pre-wrap}}small{{color:#566}}</style>
    <h1>Agent Learning Control Plane</h1><small>Τοπικό, research-only · {html.escape(data['generated_at'])}</small>
    <div class='cards'><div class='card'><b>{data['episode_count']}</b><br>episodes</div><div class='card'><b>{data['outcome_count']}</b><br>ώριμα outcomes</div><div class='card'><b>{data['evaluation_count']}</b><br>independent evaluations</div><div class='card'><b>{data['memory_version_count']}</b><br>memory versions</div><div class='card'><b>{data['lesson_candidate_count']}</b><br>lesson candidates</div><div class='card'><b>{data['prediction_count']}</b><br>shadow predictions</div><div class='card'><b>{data['feature_count']}</b><br>feature contracts</div><div class='card'><b>{html.escape(str(data['model_states']))}</b><br>model states</div></div>
    <h2>Agents</h2><table><tr><th>Agent</th><th>Episodes</th><th>Runs</th><th>Τελευταία παρατήρηση</th></tr>{agent_rows}</table>
    <h2>Model registry</h2><table><tr><th>Agent</th><th>Task</th><th>Version</th><th>State</th><th>Rows</th><th>Metrics</th></tr>{model_rows}</table>
    <p><b>Όριο ασφαλείας:</b> κανένα μοντέλο δεν προάγεται αυτόματα και το control plane δεν διαθέτει λειτουργία εκτέλεσης συναλλαγών.</p></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=market_intel_db.DEFAULT_DB_PATH)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    record = commands.add_parser("record-artifact")
    record.add_argument("--path", type=Path, required=True)
    record.add_argument("--agent", required=True)
    record.add_argument("--task", required=True)
    record.add_argument("--rows-key", required=True)
    record.add_argument("--policy-version", required=True)
    commands.add_parser("summary")
    dashboard = commands.add_parser("dashboard")
    dashboard.add_argument("--output", type=Path, default=Path("output/dashboards/agent_learning_latest.html"))
    transition = commands.add_parser("transition-model")
    transition.add_argument("--model-id", required=True)
    transition.add_argument("--state", required=True, choices=sorted(ALLOWED_MODEL_STATES))
    transition.add_argument("--reason", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    with market_intel_db.connect(args.db) as conn:
        init_schema(conn)
        if args.command == "record-artifact":
            print(canonical_json(record_artifact(conn, args.path, args.agent, args.task,
                                                 args.rows_key, args.policy_version)))
        elif args.command == "summary":
            print(json.dumps(summary(conn), ensure_ascii=False, indent=2))
        elif args.command == "dashboard":
            write_dashboard(conn, args.output)
            print(args.output.resolve())
        elif args.command == "transition-model":
            transition_model(conn, args.model_id, args.state, args.reason, {"manual": True})
            print(f"{args.model_id}: {args.state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
