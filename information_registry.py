"""Canonical information registry for the local research ecosystem.

The registry adds a shared semantic layer over the existing immutable control-plane
tables.  It keeps the current producer APIs intact while giving observations,
packets, claims and theses stable URNs, governed classifications, typed relations,
metadata-first retrieval and OpenLineage-inspired run events.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parent
ARCHITECTURE_PATH = ROOT / "research_information_architecture.json"
INFORMATION_TAXONOMY_PATH = ROOT / "research_information_taxonomy.json"
SCHEMA = "ecosystem.information_registry.v1"
CLASSIFICATION_SCHEME = "research_information"


class InformationRegistryError(RuntimeError):
    """Raised when governed information contracts are violated."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise InformationRegistryError(f"{field} is required")
    return result


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def architecture() -> dict[str, Any]:
    return _load_json(ARCHITECTURE_PATH)


def information_taxonomy() -> dict[str, Any]:
    return _load_json(INFORMATION_TAXONOMY_PATH)


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized[:48] or "global"


def build_information_urn(
    *, object_type: str, canonical_subject_id: str | None, source_kind: str, source_id: str,
) -> str:
    """Build a stable local URN without putting raw URLs or long IDs in the key."""
    object_type = _required(object_type, "object_type")
    source_kind = _required(source_kind, "source_kind")
    source_id = _required(source_id, "source_id")
    subject = str(canonical_subject_id or "global").strip() or "global"
    suffix = _digest([source_kind, source_id])[:20]
    return f"urn:ecosystem:{_slug(object_type)}:{_slug(subject)}:{suffix}"


def init_schema(conn: sqlite3.Connection) -> None:
    """Install the additive semantic registry and seed its controlled vocabularies."""
    initialized = conn.execute(
        "SELECT 1 FROM sqlite_temp_master WHERE type='table' "
        "AND name='ecosystem_information_registry_initialized'"
    ).fetchone()
    if initialized:
        return
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ecosystem_information_domain (
            domain_id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            description TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            status TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ecosystem_taxonomy_scheme (
            scheme_id TEXT NOT NULL,
            version TEXT NOT NULL,
            label TEXT NOT NULL,
            status TEXT NOT NULL,
            effective_from TEXT,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(scheme_id, version)
        );

        CREATE TABLE IF NOT EXISTS ecosystem_taxonomy_term (
            scheme_id TEXT NOT NULL,
            scheme_version TEXT NOT NULL,
            term_id TEXT NOT NULL,
            parent_term_id TEXT,
            label TEXT NOT NULL,
            definition TEXT NOT NULL,
            owner_id TEXT,
            properties_json TEXT NOT NULL,
            lifecycle_state TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(scheme_id, scheme_version, term_id),
            FOREIGN KEY(scheme_id, scheme_version)
                REFERENCES ecosystem_taxonomy_scheme(scheme_id, version)
        );
        CREATE INDEX IF NOT EXISTS idx_information_term_parent
            ON ecosystem_taxonomy_term(scheme_id, scheme_version, parent_term_id);

        CREATE TABLE IF NOT EXISTS ecosystem_information_object (
            information_urn TEXT PRIMARY KEY,
            object_type TEXT NOT NULL,
            canonical_subject_id TEXT,
            domain_id TEXT NOT NULL REFERENCES ecosystem_information_domain(domain_id),
            owner_id TEXT NOT NULL,
            verification_state TEXT NOT NULL,
            lifecycle_state TEXT NOT NULL,
            as_of TEXT,
            valid_from TEXT,
            valid_to TEXT,
            observed_at TEXT,
            recorded_at TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_id TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            title TEXT,
            summary TEXT,
            source_locator_json TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(source_kind, source_id),
            CHECK(valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from)
        );
        CREATE INDEX IF NOT EXISTS idx_information_object_subject
            ON ecosystem_information_object(canonical_subject_id, object_type, as_of);
        CREATE INDEX IF NOT EXISTS idx_information_object_filters
            ON ecosystem_information_object(domain_id, lifecycle_state, verification_state, object_type);

        CREATE TABLE IF NOT EXISTS ecosystem_claim_semantics (
            claim_id TEXT PRIMARY KEY REFERENCES ecosystem_claim_store(claim_id) ON DELETE CASCADE,
            category_ids_json TEXT NOT NULL,
            predicate TEXT,
            object_value_json TEXT,
            valid_from TEXT,
            valid_to TEXT,
            source_locator_json TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            content_hash TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ecosystem_information_classification (
            assignment_id TEXT PRIMARY KEY,
            information_urn TEXT NOT NULL REFERENCES ecosystem_information_object(information_urn) ON DELETE CASCADE,
            scheme_id TEXT NOT NULL,
            scheme_version TEXT NOT NULL,
            term_id TEXT NOT NULL,
            status TEXT NOT NULL,
            confidence TEXT NOT NULL,
            rationale TEXT NOT NULL,
            assigned_by TEXT NOT NULL,
            source_urn TEXT,
            valid_from TEXT NOT NULL,
            valid_to TEXT,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(scheme_id, scheme_version, term_id)
                REFERENCES ecosystem_taxonomy_term(scheme_id, scheme_version, term_id),
            CHECK(valid_to IS NULL OR valid_to > valid_from)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS current_information_classification
            ON ecosystem_information_classification(information_urn, scheme_id, term_id)
            WHERE valid_to IS NULL AND status != 'superseded';

        CREATE TABLE IF NOT EXISTS ecosystem_information_edge (
            edge_id TEXT PRIMARY KEY,
            source_urn TEXT NOT NULL REFERENCES ecosystem_information_object(information_urn) ON DELETE CASCADE,
            relationship_type TEXT NOT NULL,
            target_urn TEXT NOT NULL REFERENCES ecosystem_information_object(information_urn) ON DELETE CASCADE,
            run_id TEXT,
            confidence TEXT NOT NULL,
            valid_from TEXT,
            valid_to TEXT,
            evidence_urn TEXT REFERENCES ecosystem_information_object(information_urn),
            properties_json TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(source_urn, relationship_type, target_urn, run_id),
            CHECK(source_urn != target_urn),
            CHECK(valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from)
        );
        CREATE INDEX IF NOT EXISTS idx_information_edge_source
            ON ecosystem_information_edge(source_urn, relationship_type);
        CREATE INDEX IF NOT EXISTS idx_information_edge_target
            ON ecosystem_information_edge(target_urn, relationship_type);

        CREATE TABLE IF NOT EXISTS ecosystem_information_tag (
            tag_id TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ecosystem_information_object_tag (
            information_urn TEXT NOT NULL REFERENCES ecosystem_information_object(information_urn) ON DELETE CASCADE,
            tag_id TEXT NOT NULL REFERENCES ecosystem_information_tag(tag_id),
            assigned_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(information_urn, tag_id)
        );

        CREATE TABLE IF NOT EXISTS ecosystem_taxonomy_change_proposal (
            proposal_id TEXT PRIMARY KEY,
            scheme_id TEXT NOT NULL,
            base_version TEXT NOT NULL,
            operation TEXT NOT NULL,
            term_id TEXT NOT NULL,
            proposal_json TEXT NOT NULL,
            rationale TEXT NOT NULL,
            proposed_by TEXT NOT NULL,
            status TEXT NOT NULL,
            reviewed_by TEXT,
            review_note TEXT,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            reviewed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_taxonomy_proposal_status
            ON ecosystem_taxonomy_change_proposal(scheme_id, status, created_at);

        CREATE TABLE IF NOT EXISTS ecosystem_lineage_event (
            lineage_event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            event_time TEXT NOT NULL,
            job_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            parent_run_id TEXT,
            subject_id TEXT,
            producer TEXT NOT NULL,
            code_version TEXT,
            inputs_json TEXT NOT NULL,
            outputs_json TEXT NOT NULL,
            facets_json TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(event_type, job_id, run_id, subject_id, content_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_lineage_event_run
            ON ecosystem_lineage_event(run_id, job_id, event_time);

        CREATE TABLE IF NOT EXISTS ecosystem_information_state_event (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            information_urn TEXT NOT NULL REFERENCES ecosystem_information_object(information_urn),
            lifecycle_state TEXT,
            verification_state TEXT,
            reason TEXT NOT NULL,
            changed_by TEXT NOT NULL,
            event_time TEXT NOT NULL,
            CHECK(lifecycle_state IS NOT NULL OR verification_state IS NOT NULL)
        );
        CREATE INDEX IF NOT EXISTS idx_information_state_object
            ON ecosystem_information_state_event(information_urn, sequence);

        CREATE TABLE IF NOT EXISTS ecosystem_claim_conflict (
            conflict_id TEXT PRIMARY KEY,
            claim_urn_a TEXT NOT NULL REFERENCES ecosystem_information_object(information_urn),
            claim_urn_b TEXT NOT NULL REFERENCES ecosystem_information_object(information_urn),
            predicate TEXT NOT NULL,
            period_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            rationale TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            reviewed_by TEXT,
            review_note TEXT,
            reviewed_at TEXT,
            UNIQUE(claim_urn_a, claim_urn_b),
            CHECK(claim_urn_a < claim_urn_b),
            CHECK(status IN ('open','confirmed','dismissed'))
        );
        CREATE INDEX IF NOT EXISTS idx_claim_conflict_status
            ON ecosystem_claim_conflict(status, detected_at);
        """
    )
    _seed_architecture(conn)
    _seed_information_taxonomy(conn)
    _init_search(conn)
    conn.execute("CREATE TEMP TABLE ecosystem_information_registry_initialized (marker INTEGER)")
    conn.commit()


def _seed_architecture(conn: sqlite3.Connection) -> None:
    config = architecture()
    version = str(config["version"])
    stamp = _now()
    for domain in config["domains"]:
        body = {"schema": config["schema"], "version": version, **domain, "status": "active"}
        content_hash = _digest(body)
        previous = conn.execute(
            "SELECT content_hash FROM ecosystem_information_domain WHERE domain_id=?", (domain["id"],)
        ).fetchone()
        if previous and str(previous[0]) != content_hash:
            raise InformationRegistryError(f"Domain definition changed without a new ID: {domain['id']}")
        conn.execute(
            "INSERT OR IGNORE INTO ecosystem_information_domain VALUES(?,?,?,?,?,?,?)",
            (domain["id"], domain["label"], domain["description"], version, "active", content_hash, stamp),
        )


def _seed_information_taxonomy(conn: sqlite3.Connection) -> None:
    taxonomy = information_taxonomy()
    version = _required(taxonomy.get("version"), "taxonomy.version")
    scheme_body = {
        "scheme_id": CLASSIFICATION_SCHEME,
        "version": version,
        "label": "Research information taxonomy",
        "status": "active",
        "principles": taxonomy.get("principles") or {},
    }
    scheme_hash = _digest(scheme_body)
    previous_scheme = conn.execute(
        "SELECT content_hash FROM ecosystem_taxonomy_scheme WHERE scheme_id=? AND version=?",
        (CLASSIFICATION_SCHEME, version),
    ).fetchone()
    if previous_scheme and str(previous_scheme[0]) != scheme_hash:
        raise InformationRegistryError(
            f"Taxonomy scheme changed without a new version: {CLASSIFICATION_SCHEME}:{version}"
        )
    conn.execute(
        "INSERT OR IGNORE INTO ecosystem_taxonomy_scheme VALUES(?,?,?,?,?,?,?)",
        (CLASSIFICATION_SCHEME, version, scheme_body["label"], "active", None, scheme_hash, _now()),
    )
    for category in taxonomy.get("categories") or []:
        term_id = _required(category.get("id"), "category.id")
        body = {
            "scheme_id": CLASSIFICATION_SCHEME,
            "scheme_version": version,
            "term_id": term_id,
            "parent_term_id": None,
            "label": term_id.replace("_", " "),
            "definition": _required(category.get("description"), "category.description"),
            "owner_id": str(category.get("owner") or "shared_registry"),
            "properties": {"layer": category.get("layer")},
            "lifecycle_state": "published",
        }
        content_hash = _digest(body)
        previous = conn.execute(
            "SELECT content_hash FROM ecosystem_taxonomy_term WHERE scheme_id=? AND scheme_version=? AND term_id=?",
            (CLASSIFICATION_SCHEME, version, term_id),
        ).fetchone()
        if previous and str(previous[0]) != content_hash:
            raise InformationRegistryError(
                f"Taxonomy term changed without a new taxonomy version: {term_id}"
            )
        conn.execute(
            "INSERT OR IGNORE INTO ecosystem_taxonomy_term VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                CLASSIFICATION_SCHEME, version, term_id, None, body["label"], body["definition"],
                body["owner_id"], _canonical_json(body["properties"]), "published", content_hash, _now(),
            ),
        )


def _init_search(conn: sqlite3.Connection) -> None:
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS ecosystem_information_search "
            "USING fts5(information_urn UNINDEXED, title, summary, search_text)"
        )
    except sqlite3.OperationalError:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ecosystem_information_search ("
            "information_urn TEXT PRIMARY KEY, title TEXT, summary TEXT, search_text TEXT)"
        )


def _controlled(config_key: str, value: str) -> str:
    value = _required(value, config_key)
    allowed = set(architecture()[config_key])
    if value not in allowed:
        raise InformationRegistryError(f"Unsupported {config_key}: {value}")
    return value


def _search_upsert(
    conn: sqlite3.Connection, information_urn: str, title: str | None,
    summary: str | None, search_text: str,
) -> None:
    values = (information_urn, title or "", summary or "", search_text)
    definition = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='ecosystem_information_search'"
    ).fetchone()
    if definition and "fts5" in str(definition[0]).lower():
        # information_urn is UNINDEXED in the FTS5 table, so "DELETE ... WHERE information_urn=?"
        # scans the whole table (1.2 s at ~100k rows, growing with every run). The search row of an
        # object always carries the object's own rowid, so it is replaced by rowid, which is direct.
        row = conn.execute(
            "SELECT rowid FROM ecosystem_information_object WHERE information_urn=?", (information_urn,)
        ).fetchone()
        if row is not None:
            search_rowid = int(row[0])
            holder = conn.execute(
                "SELECT information_urn FROM ecosystem_information_search WHERE rowid=?", (search_rowid,)
            ).fetchone()
            # Only take the fast path when the rowid is free or already ours; if it belongs to
            # another URN (an older database), fall through to the slow, exact path below.
            if holder is None or holder[0] == information_urn:
                if holder is not None:
                    conn.execute("DELETE FROM ecosystem_information_search WHERE rowid=?", (search_rowid,))
                conn.execute(
                    "INSERT INTO ecosystem_information_search(rowid,information_urn,title,summary,search_text) "
                    "VALUES(?,?,?,?,?)", (search_rowid, *values),
                )
                return
    conn.execute("DELETE FROM ecosystem_information_search WHERE information_urn=?", (information_urn,))
    conn.execute(
        "INSERT INTO ecosystem_information_search(information_urn,title,summary,search_text) VALUES(?,?,?,?)",
        values,
    )


def register_information_object(
    conn: sqlite3.Connection, *, object_type: str, canonical_subject_id: str | None,
    domain_id: str, owner_id: str, verification_state: str, lifecycle_state: str,
    source_kind: str, source_id: str, payload: dict[str, Any], schema_version: str,
    as_of: str | None = None, valid_from: str | None = None, valid_to: str | None = None,
    observed_at: str | None = None, recorded_at: str | None = None,
    title: str | None = None, summary: str | None = None,
    source_locator: dict[str, Any] | None = None, category_ids: Iterable[str] = (),
    tags: Iterable[str] = (), assigned_by: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Register one immutable semantic object and its governed classifications."""
    init_schema(conn)
    object_type = _controlled("object_types", object_type)
    domain_id = _required(domain_id, "domain_id")
    if not conn.execute(
        "SELECT 1 FROM ecosystem_information_domain WHERE domain_id=? AND status='active'", (domain_id,)
    ).fetchone():
        raise InformationRegistryError(f"Unknown domain: {domain_id}")
    verification_state = _controlled("verification_states", verification_state)
    lifecycle_state = _controlled("lifecycle_states", lifecycle_state)
    owner_id = _required(owner_id, "owner_id")
    source_kind = _required(source_kind, "source_kind")
    source_id = _required(source_id, "source_id")
    schema_version = _required(schema_version, "schema_version")
    subject = str(canonical_subject_id or "").strip() or None
    information_urn = build_information_urn(
        object_type=object_type, canonical_subject_id=subject,
        source_kind=source_kind, source_id=source_id,
    )
    previous = conn.execute(
        "SELECT information_urn,content_hash,recorded_at FROM ecosystem_information_object "
        "WHERE source_kind=? AND source_id=?",
        (source_kind, source_id),
    ).fetchone()
    stable_recorded_at = (
        str(recorded_at or "").strip()
        or (str(previous["recorded_at"]) if previous else "")
        or str(observed_at or "").strip()
        or str(as_of or "").strip()
        or str(valid_from or "").strip()
        or _now()
    )
    body = {
        "schema": SCHEMA,
        "information_urn": information_urn,
        "object_type": object_type,
        "canonical_subject_id": subject,
        "domain_id": domain_id,
        "owner_id": owner_id,
        "verification_state": verification_state,
        "lifecycle_state": lifecycle_state,
        "as_of": str(as_of or "").strip() or None,
        "valid_from": str(valid_from or "").strip() or None,
        "valid_to": str(valid_to or "").strip() or None,
        "observed_at": str(observed_at or "").strip() or None,
        "recorded_at": stable_recorded_at,
        "source_kind": source_kind,
        "source_id": source_id,
        "schema_version": schema_version,
        "title": str(title or "").strip() or None,
        "summary": str(summary or "").strip() or None,
        "source_locator": source_locator or {},
        "payload": payload,
    }
    content_hash = _digest(body)
    created = previous is None
    if previous and str(previous["content_hash"]) != content_hash:
        raise InformationRegistryError(f"Immutable information object conflict for {source_kind}:{source_id}")
    if not previous:
        conn.execute(
            "INSERT INTO ecosystem_information_object VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                information_urn, object_type, subject, domain_id, owner_id, verification_state,
                lifecycle_state, body["as_of"], body["valid_from"], body["valid_to"],
                body["observed_at"], body["recorded_at"], source_kind, source_id, schema_version,
                body["title"], body["summary"], _canonical_json(body["source_locator"]),
                _canonical_json(payload), content_hash, _now(),
            ),
        )
        search_text = " ".join(
            value for value in (
                body["title"] or "", body["summary"] or "", _canonical_json(payload),
                subject or "", domain_id, object_type,
            ) if value
        )
        _search_upsert(conn, information_urn, body["title"], body["summary"], search_text)
    taxonomy_version = str(information_taxonomy()["version"])
    for category_id in sorted({str(value).strip() for value in category_ids if str(value).strip()}):
        assign_classification(
            conn, information_urn=information_urn, scheme_id=CLASSIFICATION_SCHEME,
            scheme_version=taxonomy_version, term_id=category_id, status="verified",
            confidence="high", rationale="producer contract mapping",
            assigned_by=assigned_by or owner_id, valid_from=body["recorded_at"], commit=False,
        )
    for tag in sorted({str(value).strip().lower() for value in tags if str(value).strip()}):
        attach_tag(conn, information_urn=information_urn, tag_id=tag, assigned_by=assigned_by or owner_id, commit=False)
    conn.commit()
    return {**body, "content_hash": content_hash}, created


def assign_classification(
    conn: sqlite3.Connection, *, information_urn: str, scheme_id: str,
    scheme_version: str, term_id: str, status: str, confidence: str,
    rationale: str, assigned_by: str, valid_from: str, valid_to: str | None = None,
    source_urn: str | None = None, commit: bool = True,
) -> dict[str, Any]:
    init_schema(conn)
    information_urn = _required(information_urn, "information_urn")
    if not conn.execute(
        "SELECT 1 FROM ecosystem_information_object WHERE information_urn=?", (information_urn,)
    ).fetchone():
        raise InformationRegistryError(f"Unknown information object: {information_urn}")
    if not conn.execute(
        "SELECT 1 FROM ecosystem_taxonomy_term WHERE scheme_id=? AND scheme_version=? AND term_id=?",
        (scheme_id, scheme_version, term_id),
    ).fetchone():
        raise InformationRegistryError(f"Unknown taxonomy term: {scheme_id}:{scheme_version}:{term_id}")
    body = {
        "information_urn": information_urn, "scheme_id": scheme_id,
        "scheme_version": scheme_version, "term_id": term_id,
        "status": _required(status, "status"), "confidence": _required(confidence, "confidence"),
        "rationale": _required(rationale, "rationale"), "assigned_by": _required(assigned_by, "assigned_by"),
        "source_urn": str(source_urn or "").strip() or None,
        "valid_from": _required(valid_from, "valid_from"),
        "valid_to": str(valid_to or "").strip() or None,
    }
    content_hash = _digest(body)
    assignment_id = "classification_" + content_hash[:24]
    previous = conn.execute(
        "SELECT assignment_id,content_hash FROM ecosystem_information_classification "
        "WHERE information_urn=? AND scheme_id=? AND term_id=? AND valid_to IS NULL AND status!='superseded'",
        (information_urn, scheme_id, term_id),
    ).fetchone()
    if previous:
        if str(previous["content_hash"]) != content_hash:
            raise InformationRegistryError(
                f"Current classification already exists for {information_urn}:{scheme_id}:{term_id}"
            )
        return {**body, "assignment_id": str(previous["assignment_id"]), "content_hash": content_hash}
    conn.execute(
        "INSERT INTO ecosystem_information_classification VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            assignment_id, information_urn, scheme_id, scheme_version, term_id, body["status"],
            body["confidence"], body["rationale"], body["assigned_by"], body["source_urn"],
            body["valid_from"], body["valid_to"], content_hash, _now(),
        ),
    )
    if commit:
        conn.commit()
    return {**body, "assignment_id": assignment_id, "content_hash": content_hash}


def attach_tag(
    conn: sqlite3.Connection, *, information_urn: str, tag_id: str,
    assigned_by: str, description: str = "", commit: bool = True,
) -> None:
    init_schema(conn)
    tag_id = _required(tag_id, "tag_id")
    body = {"tag_id": tag_id, "description": str(description or "")}
    conn.execute(
        "INSERT OR IGNORE INTO ecosystem_information_tag VALUES(?,?,?,?)",
        (tag_id, body["description"], _digest(body), _now()),
    )
    conn.execute(
        "INSERT OR IGNORE INTO ecosystem_information_object_tag VALUES(?,?,?,?)",
        (information_urn, tag_id, _required(assigned_by, "assigned_by"), _now()),
    )
    if commit:
        conn.commit()


def link_information(
    conn: sqlite3.Connection, *, source_urn: str, relationship_type: str,
    target_urn: str, run_id: str | None = None, confidence: str = "high",
    valid_from: str | None = None, valid_to: str | None = None,
    evidence_urn: str | None = None, properties: dict[str, Any] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    init_schema(conn)
    relationship_type = _controlled("relationship_types", relationship_type)
    for field, urn in (("source_urn", source_urn), ("target_urn", target_urn)):
        if not conn.execute(
            "SELECT 1 FROM ecosystem_information_object WHERE information_urn=?", (urn,)
        ).fetchone():
            raise InformationRegistryError(f"Unknown {field}: {urn}")
    if source_urn == target_urn:
        raise InformationRegistryError("Self-referential information edges are not allowed")
    body = {
        "source_urn": source_urn, "relationship_type": relationship_type,
        "target_urn": target_urn, "run_id": str(run_id or "").strip() or None,
        "confidence": _required(confidence, "confidence"),
        "valid_from": str(valid_from or "").strip() or None,
        "valid_to": str(valid_to or "").strip() or None,
        "evidence_urn": str(evidence_urn or "").strip() or None,
        "properties": properties or {},
    }
    content_hash = _digest(body)
    edge_id = "edge_" + content_hash[:24]
    previous = conn.execute(
        "SELECT edge_id,content_hash FROM ecosystem_information_edge "
        "WHERE source_urn=? AND relationship_type=? AND target_urn=? AND run_id IS ?",
        (source_urn, relationship_type, target_urn, body["run_id"]),
    ).fetchone()
    if previous and str(previous["content_hash"]) != content_hash:
        raise InformationRegistryError(f"Information edge conflict for {source_urn} -> {target_urn}")
    conn.execute(
        "INSERT OR IGNORE INTO ecosystem_information_edge VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            edge_id, source_urn, relationship_type, target_urn, body["run_id"],
            body["confidence"], body["valid_from"], body["valid_to"], body["evidence_urn"],
            _canonical_json(body["properties"]), content_hash, _now(),
        ),
    )
    if commit:
        conn.commit()
    return {**body, "edge_id": str(previous["edge_id"]) if previous else edge_id, "content_hash": content_hash}


def object_urn_for_source(conn: sqlite3.Connection, source_kind: str, source_id: str) -> str | None:
    row = conn.execute(
        "SELECT information_urn FROM ecosystem_information_object WHERE source_kind=? AND source_id=?",
        (source_kind, source_id),
    ).fetchone()
    return str(row[0]) if row else None


def effective_information_state(conn: sqlite3.Connection, information_urn: str) -> dict[str, str]:
    """Resolve current state from immutable source metadata plus append-only changes."""
    init_schema(conn)
    row = conn.execute(
        "SELECT lifecycle_state,verification_state FROM ecosystem_information_object "
        "WHERE information_urn=?", (information_urn,),
    ).fetchone()
    if not row:
        raise InformationRegistryError(f"Unknown information object: {information_urn}")
    state = {"lifecycle_state": str(row[0]), "verification_state": str(row[1])}
    for event in conn.execute(
        "SELECT lifecycle_state,verification_state FROM ecosystem_information_state_event "
        "WHERE information_urn=? ORDER BY sequence", (information_urn,),
    ):
        for key in state:
            if event[key] is not None:
                state[key] = str(event[key])
    return state


def information_impact(
    conn: sqlite3.Connection, information_urn: str, *, max_depth: int = 12,
) -> dict[str, Any]:
    """Trace downstream products using the direction of each semantic dependency."""
    init_schema(conn)
    if not conn.execute(
        "SELECT 1 FROM ecosystem_information_object WHERE information_urn=?", (information_urn,)
    ).fetchone():
        raise InformationRegistryError(f"Unknown information object: {information_urn}")
    forward = {"contains", "supports", "contradicts", "context_for", "depends_on", "affects"}
    reverse = {"derived_from"}
    reached = {information_urn}
    frontier = [(information_urn, 0)]
    affected: list[dict[str, Any]] = []
    while frontier:
        urn, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        edges = conn.execute(
            "SELECT source_urn,target_urn,relationship_type FROM ecosystem_information_edge "
            "WHERE source_urn=? OR target_urn=? ORDER BY edge_id", (urn, urn),
        ).fetchall()
        for edge in edges:
            relation = str(edge["relationship_type"])
            next_urn = None
            if relation in forward and edge["source_urn"] == urn:
                next_urn = str(edge["target_urn"])
            elif relation in reverse and edge["target_urn"] == urn:
                next_urn = str(edge["source_urn"])
            if not next_urn or next_urn in reached:
                continue
            reached.add(next_urn)
            next_depth = depth + 1
            frontier.append((next_urn, next_depth))
            item = conn.execute(
                "SELECT object_type,canonical_subject_id,source_kind,source_id,title "
                "FROM ecosystem_information_object WHERE information_urn=?", (next_urn,),
            ).fetchone()
            affected.append({
                "information_urn": next_urn, "object_type": item["object_type"],
                "canonical_subject_id": item["canonical_subject_id"],
                "source_kind": item["source_kind"], "source_id": item["source_id"],
                "title": item["title"], "depth": next_depth,
                "via": relation,
            })
    return {"source_urn": information_urn, "affected": affected,
            "affected_count": len(affected)}


def record_information_state(
    conn: sqlite3.Connection, *, information_urn: str, reason: str,
    changed_by: str, lifecycle_state: str | None = None,
    verification_state: str | None = None,
    event_time: str | None = None,
) -> dict[str, Any]:
    """Append a state change and report the objects that may need review."""
    init_schema(conn)
    if lifecycle_state is None and verification_state is None:
        raise InformationRegistryError("At least one information state must change")
    if lifecycle_state is not None:
        lifecycle_state = _controlled("lifecycle_states", lifecycle_state)
    if verification_state is not None:
        verification_state = _controlled("verification_states", verification_state)
    impact = information_impact(conn, information_urn)
    reason = _required(reason, "reason")
    changed_by = _required(changed_by, "changed_by")
    previous_state = effective_information_state(conn, information_urn)
    last_event = conn.execute(
        "SELECT sequence,event_id FROM ecosystem_information_state_event "
        "WHERE information_urn=? ORDER BY sequence DESC LIMIT 1", (information_urn,),
    ).fetchone()
    if ((lifecycle_state is None or lifecycle_state == previous_state["lifecycle_state"])
            and (verification_state is None or verification_state == previous_state["verification_state"])):
        return {"event_id": str(last_event["event_id"]) if last_event else None,
                "information_urn": information_urn,
                "effective_state": previous_state, **impact}
    body = [information_urn, lifecycle_state, verification_state, reason,
            changed_by, int(last_event["sequence"]) if last_event else 0]
    event_id = "state_" + _digest(body)[:24]
    conn.execute(
        "INSERT OR IGNORE INTO ecosystem_information_state_event "
        "(event_id,information_urn,lifecycle_state,verification_state,reason,changed_by,event_time) "
        "VALUES(?,?,?,?,?,?,?)",
        (event_id, information_urn, lifecycle_state, verification_state,
         reason, changed_by, event_time or _now()),
    )
    conn.commit()
    return {"event_id": event_id, "information_urn": information_urn,
            "effective_state": effective_information_state(conn, information_urn), **impact}


def _domain_for_observation(capability: str) -> str:
    value = capability.lower()
    if any(token in value for token in ("price", "quote", "liquidity", "market")):
        return "market"
    if any(token in value for token in ("macro", "fred", "rates", "inflation")):
        return "macro"
    if any(token in value for token in ("listing", "security", "instrument")):
        return "security"
    return "issuer"


def _verification_from_quality(value: str) -> str:
    normalized = str(value or "").lower()
    if normalized in {"ready", "verified", "available", "complete", "completed"}:
        return "supported"
    if normalized in {"blocked", "missing", "evidence_gap", "unavailable"}:
        return "unresolved"
    if normalized in {"rejected", "failed"}:
        return "rejected"
    return "provisional"


def _control_plane_created_at(
    conn: sqlite3.Connection, source_kind: str, source_id: str,
) -> str | None:
    table_and_key = {
        "observation": ("ecosystem_observation_store", "observation_id"),
        "packet": ("ecosystem_evidence_packet_store", "packet_id"),
        "claim": ("ecosystem_claim_store", "claim_id"),
        "thesis": ("ecosystem_thesis_revision", "thesis_revision_id"),
    }.get(source_kind)
    if not table_and_key:
        return None
    table, key = table_and_key
    row = conn.execute(f"SELECT created_at FROM {table} WHERE {key}=?", (source_id,)).fetchone()
    return str(row[0]) if row else None


def _stable_control_payload(value: dict[str, Any]) -> dict[str, Any]:
    """Omit store-only fields so live writes and later backfills hash identically."""
    return {
        key: item for key, item in value.items()
        if key not in {"created_at", "content_hash", "owner_id"}
    }


def sync_observation(conn: sqlite3.Connection, observation: dict[str, Any]) -> dict[str, Any]:
    subject = str(observation.get("subject_id") or "").strip() or None
    capability = str(observation.get("capability") or "observation")
    result, _ = register_information_object(
        conn, object_type="observation", canonical_subject_id=subject,
        domain_id=_domain_for_observation(capability), owner_id=str(observation.get("provider") or "shared_registry"),
        verification_state=_verification_from_quality(str(observation.get("status") or "available")),
        lifecycle_state="raw", source_kind="observation", source_id=str(observation["observation_id"]),
        payload=_stable_control_payload(observation),
        schema_version=str(observation.get("schema") or "ecosystem.observation.v1"),
        as_of=observation.get("as_of"), observed_at=observation.get("observed_at"),
        recorded_at=(observation.get("created_at") or _control_plane_created_at(
            conn, "observation", str(observation["observation_id"])
        )), title=capability,
        summary=f"{observation.get('provider')} observation for {subject or 'global'}",
        source_locator={"source_ref": observation.get("source_ref")},
        category_ids=("source_provenance",), assigned_by="shared_registry",
    )
    return result


def sync_packet(conn: sqlite3.Connection, packet: dict[str, Any]) -> dict[str, Any]:
    config = architecture()
    defaults = config.get("packet_defaults", {}).get(str(packet.get("kind")), {})
    result, _ = register_information_object(
        conn, object_type="packet", canonical_subject_id=str(packet.get("subject_id") or "").strip() or None,
        domain_id=str(defaults.get("domain_id") or "governance"),
        owner_id=str(packet.get("producer_name") or "shared_registry"),
        verification_state=_verification_from_quality(str(packet.get("quality_state") or "provisional")),
        lifecycle_state="published", source_kind="packet", source_id=str(packet["packet_id"]),
        payload=_stable_control_payload(packet),
        schema_version=str(packet.get("schema") or "ecosystem.evidence_packet.v1"),
        as_of=packet.get("as_of"), recorded_at=(packet.get("created_at") or _control_plane_created_at(
            conn, "packet", str(packet["packet_id"])
        )),
        title=str(packet.get("kind") or "research packet"),
        summary=f"{packet.get('kind')} packet produced by {packet.get('producer_name')}",
        source_locator={"run_id": packet.get("run_id")},
        category_ids=tuple(defaults.get("category_ids") or ()),
        tags=(str(packet.get("kind") or "packet"),), assigned_by=str(packet.get("producer_name") or "shared_registry"),
    )
    for item in packet.get("inputs") or []:
        source_urn = object_urn_for_source(conn, str(item.get("kind")), str(item.get("id")))
        if source_urn:
            link_information(
                conn, source_urn=result["information_urn"], relationship_type="derived_from",
                target_urn=source_urn, run_id=packet.get("run_id"),
                properties={"input_hash": item.get("content_hash")}, commit=False,
            )
    conn.commit()
    return result


def sync_claim(
    conn: sqlite3.Connection, claim: dict[str, Any], *, category_ids: Iterable[str] = (),
    predicate: str | None = None, object_value: Any = None,
    valid_from: str | None = None, valid_to: str | None = None,
    source_locator: dict[str, Any] | None = None,
) -> dict[str, Any]:
    defaults = architecture().get("claim_category_defaults", {})
    categories = {str(value).strip() for value in category_ids if str(value).strip()}
    mapped = defaults.get(str(claim.get("claim_type") or ""))
    if mapped:
        categories.add(str(mapped))
    if not categories:
        raise InformationRegistryError(
            f"Claim type {claim.get('claim_type')!r} has no governed category; "
            "pass category_ids or add an approved mapping"
        )
    payload = {**_stable_control_payload(claim), "predicate": predicate, "object_value": object_value}
    result, _ = register_information_object(
        conn, object_type="claim", canonical_subject_id=claim.get("canonical_subject_id"),
        domain_id="issuer", owner_id=str(claim.get("owner_id") or "research_synthesizer"),
        verification_state=_verification_from_quality(str(claim.get("verification_status") or "provisional")),
        lifecycle_state="reviewed" if str(claim.get("verification_status")) == "verified" else "normalized",
        source_kind="claim", source_id=str(claim["claim_id"]), payload=payload,
        schema_version=str(claim.get("schema") or "ecosystem.claim.v1"), as_of=claim.get("as_of"),
        recorded_at=(claim.get("created_at") or _control_plane_created_at(
            conn, "claim", str(claim["claim_id"])
        )),
        valid_from=valid_from, valid_to=valid_to, title=str(claim.get("claim_type") or "claim"),
        summary=str(claim.get("claim_text") or ""), source_locator=source_locator or {},
        category_ids=categories, tags=("claim",), assigned_by=str(claim.get("owner_id") or "research_synthesizer"),
    )
    for item in claim.get("evidence") or []:
        evidence_urn = object_urn_for_source(conn, str(item.get("kind")), str(item.get("id")))
        if not evidence_urn:
            continue
        relation = {
            "supporting": "supports", "contradicting": "contradicts", "context": "context_for",
        }.get(str(item.get("stance")), "context_for")
        link_information(
            conn, source_urn=evidence_urn, relationship_type=relation,
            target_urn=result["information_urn"], run_id=claim.get("run_id"),
            properties={"input_hash": item.get("hash")}, commit=False,
        )
    element_urn = str((source_locator or {}).get("document_element_urn") or "").strip()
    if element_urn:
        element = conn.execute(
            "SELECT object_type FROM ecosystem_information_object WHERE information_urn=?",
            (element_urn,),
        ).fetchone()
        if not element or element[0] != "document_element":
            raise InformationRegistryError(f"Invalid claim document element locator: {element_urn}")
        link_information(
            conn, source_urn=element_urn, relationship_type="supports",
            target_urn=result["information_urn"], run_id=claim.get("run_id"),
            properties={"locator": source_locator}, commit=False,
        )
    detect_claim_conflicts(conn, result["information_urn"])
    conn.commit()
    return result


def detect_claim_conflicts(conn: sqlite3.Connection, claim_urn: str) -> list[dict[str, Any]]:
    """Flag incompatible structured values for the same issuer, predicate and period.

    This is a review candidate, never an automatic factual verdict. Free-text claims
    are deliberately excluded because wording differences are not reliable conflicts.
    """
    init_schema(conn)
    claim = conn.execute(
        "SELECT * FROM ecosystem_information_object WHERE information_urn=? AND object_type='claim'",
        (claim_urn,),
    ).fetchone()
    if not claim:
        raise InformationRegistryError(f"Unknown claim: {claim_urn}")
    predicate, value = _structured_claim_fields(conn, claim)
    if not predicate or value is None or not claim["canonical_subject_id"]:
        return []
    if effective_information_state(conn, claim_urn)["verification_state"] in {"rejected", "superseded"}:
        return []
    found: list[dict[str, Any]] = []
    candidates = conn.execute(
        "SELECT * FROM ecosystem_information_object WHERE object_type='claim' "
        "AND canonical_subject_id=? AND information_urn!=?",
        (claim["canonical_subject_id"], claim_urn),
    ).fetchall()
    for other in candidates:
        other_predicate, other_value = _structured_claim_fields(conn, other)
        if other_predicate != predicate:
            continue
        if other_value is None or _canonical_json(value) == _canonical_json(other_value):
            continue
        if effective_information_state(conn, str(other["information_urn"]))["verification_state"] in {"rejected", "superseded"}:
            continue
        # Compare only a shared as-of date or overlapping explicit valid intervals.
        as_of = str(claim["as_of"] or "")
        other_as_of = str(other["as_of"] or "")
        start = str(claim["valid_from"] or "")
        end = str(claim["valid_to"] or "")
        other_start = str(other["valid_from"] or "")
        other_end = str(other["valid_to"] or "")
        shared_as_of = bool(as_of and other_as_of and as_of == other_as_of)
        interval_overlap = bool(start and other_start and (not end or other_start < end)
                                and (not other_end or start < other_end))
        if not shared_as_of and not interval_overlap:
            continue
        pair = sorted((claim_urn, str(other["information_urn"])))
        period_key = as_of if shared_as_of else f"{max(start, other_start)}..{min(end or '~', other_end or '~')}"
        conflict_id = "conflict_" + _digest(pair)[:24]
        rationale = "Different structured values for the same subject, predicate and overlapping period"
        conn.execute(
            "INSERT OR IGNORE INTO ecosystem_claim_conflict "
            "(conflict_id,claim_urn_a,claim_urn_b,predicate,period_key,status,rationale,detected_at) "
            "VALUES(?,?,?,?,?,'open',?,?)",
            (conflict_id, pair[0], pair[1], predicate, period_key, rationale, _now()),
        )
        found.append({"conflict_id": conflict_id, "claim_urn_a": pair[0],
                      "claim_urn_b": pair[1], "predicate": predicate,
                      "period_key": period_key})
    conn.commit()
    return found


def _structured_claim_fields(
    conn: sqlite3.Connection, row: sqlite3.Row,
) -> tuple[str, Any]:
    payload = json.loads(row["payload_json"])
    predicate = str(payload.get("predicate") or "").strip().lower()
    value = payload.get("object_value")
    if predicate and value is not None:
        return predicate, value
    semantics = conn.execute(
        "SELECT predicate,object_value_json FROM ecosystem_claim_semantics WHERE claim_id=?",
        (row["source_id"],),
    ).fetchone()
    if not semantics or semantics["object_value_json"] is None:
        return "", None
    return str(semantics["predicate"] or "").strip().lower(), json.loads(semantics["object_value_json"])


def _legacy_claim_semantics(claim_type: str, claim_text: str) -> tuple[str, Any, str] | None:
    """Decode only the exact output templates of the former fundamental producer."""
    coverage = re.fullmatch(r"Financial evidence coverage produced (high|medium|low) confidence\.", claim_text)
    if claim_type == "financial_coverage" and coverage:
        return "financial_evidence_confidence", coverage.group(1), "confidence"
    field = {
        "reported_margin": (r"Reported net margin is (-?(?:\d+(?:\.\d*)?|\.\d+))\.",
                            "reported_net_margin", "margin_analysis.reported_net_margin"),
        "balance_sheet": (r"Reported net cash is (-?(?:\d+(?:\.\d*)?|\.\d+))\.",
                          "reported_net_cash", "balance_sheet_analysis.net_cash"),
    }.get(claim_type)
    if not field:
        return None
    match = re.fullmatch(field[0], claim_text)
    if not match:
        return None
    value = float(match.group(1))
    return (field[1], value, field[2]) if math.isfinite(value) else None


def review_claim_conflict(
    conn: sqlite3.Connection, *, conflict_id: str, decision: str,
    reviewed_by: str, review_note: str,
) -> dict[str, Any]:
    """Record a human decision without rewriting either source claim."""
    init_schema(conn)
    if decision not in {"confirmed", "dismissed"}:
        raise InformationRegistryError("Conflict decision must be confirmed or dismissed")
    row = conn.execute(
        "SELECT status FROM ecosystem_claim_conflict WHERE conflict_id=?", (conflict_id,),
    ).fetchone()
    if not row:
        raise InformationRegistryError(f"Unknown claim conflict: {conflict_id}")
    if row[0] != "open":
        raise InformationRegistryError(f"Claim conflict is already {row[0]}")
    conn.execute(
        "UPDATE ecosystem_claim_conflict SET status=?,reviewed_by=?,review_note=?,reviewed_at=? "
        "WHERE conflict_id=?",
        (decision, _required(reviewed_by, "reviewed_by"),
         _required(review_note, "review_note"), _now(), conflict_id),
    )
    conn.commit()
    return {"conflict_id": conflict_id, "status": decision}


def sync_thesis(conn: sqlite3.Connection, thesis: dict[str, Any]) -> dict[str, Any]:
    result, _ = register_information_object(
        conn, object_type="thesis", canonical_subject_id=thesis.get("canonical_subject_id"),
        domain_id="issuer", owner_id="research_synthesizer",
        verification_state="provisional", lifecycle_state="published",
        source_kind="thesis", source_id=str(thesis["thesis_revision_id"]),
        payload=_stable_control_payload(thesis),
        schema_version=str(thesis.get("schema") or "ecosystem.thesis_revision.v1"),
        as_of=thesis.get("as_of"), recorded_at=(thesis.get("created_at") or _control_plane_created_at(
            conn, "thesis", str(thesis["thesis_revision_id"])
        )), title=f"Thesis revision {thesis.get('revision')}",
        summary=str(thesis.get("thesis_text") or ""),
        source_locator={"run_id": thesis.get("run_id")}, tags=("thesis",),
    )
    for claim_id in thesis.get("supporting_claim_ids") or []:
        claim_urn = object_urn_for_source(conn, "claim", str(claim_id))
        if claim_urn:
            link_information(
                conn, source_urn=claim_urn, relationship_type="supports",
                target_urn=result["information_urn"], run_id=thesis.get("run_id"), commit=False,
            )
    for claim_id in thesis.get("disconfirming_claim_ids") or []:
        claim_urn = object_urn_for_source(conn, "claim", str(claim_id))
        if claim_urn:
            link_information(
                conn, source_urn=claim_urn, relationship_type="contradicts",
                target_urn=result["information_urn"], run_id=thesis.get("run_id"), commit=False,
            )
    conn.commit()
    return result


def _document_element_nodes(value: Any, path: str = "root") -> list[tuple[str, dict[str, Any], str | None]]:
    """Return labeled document nodes with the closest labeled parent path."""
    nodes: list[tuple[str, dict[str, Any], str | None]] = []

    def visit(current: Any, current_path: str, labeled_parent: str | None) -> None:
        if isinstance(current, dict):
            label = str(current.get("label") or current.get("type") or "").strip()
            next_parent = labeled_parent
            if label:
                nodes.append((current_path, current, labeled_parent))
                next_parent = current_path
            for key, child in current.items():
                if isinstance(child, (dict, list)):
                    visit(child, f"{current_path}.{key}", next_parent)
        elif isinstance(current, list):
            for index, child in enumerate(current):
                visit(child, f"{current_path}[{index}]", labeled_parent)

    visit(value, path, None)
    return nodes


def register_document_extraction(
    conn: sqlite3.Connection, *, extraction: dict[str, Any], subject_id: str,
    run_id: str, document_type: str, observation_id: str,
) -> dict[str, Any]:
    """Materialize a document and its structural elements with exact parent lineage."""
    source = extraction.get("source") or {}
    source_hash = _required(source.get("sha256"), "source.sha256")
    source_uri = _required(source.get("uri"), "source.uri")
    source_key = f"{subject_id}:{source_hash}"
    if extraction.get("source_identity_uri"):
        source_key = f"{subject_id}:{_digest(source_uri)[:12]}:{source_hash}"
    observation_urn = object_urn_for_source(conn, "observation", observation_id)
    document, _ = register_information_object(
        conn, object_type="document", canonical_subject_id=subject_id, domain_id="issuer",
        owner_id=str(extraction.get("provider") or "document_evidence_adapter"),
        verification_state=str(extraction.get("verification_state") or (
            "supported" if str(extraction.get("markdown") or "").strip() else "unresolved"
        )),
        lifecycle_state="normalized", source_kind="source_document",
        source_id=source_key,
        payload={
            "document_type": document_type, "source": source,
            "metrics": extraction.get("metrics") or {}, "conversion_status": extraction.get("conversion_status"),
        },
        schema_version=str(extraction.get("schema") or "ecosystem.document_extraction.v1"),
        as_of=extraction.get("as_of"),
        title=str(source.get("name") or document_type),
        summary=f"{document_type} processed by {extraction.get('provider')}",
        source_locator={"uri": source.get("uri"), "sha256": source_hash},
        category_ids=("source_provenance",), tags=(document_type, "document"),
    )
    if observation_urn:
        link_information(
            conn, source_urn=document["information_urn"], relationship_type="derived_from",
            target_urn=observation_urn, run_id=run_id, commit=False,
        )

    registered: dict[str, str] = {}
    raw_nodes = _document_element_nodes(extraction.get("structure") or {})
    if not raw_nodes and str(extraction.get("markdown") or "").strip():
        raw_nodes = [("markdown", {"label": "narrative_text", "text": extraction["markdown"]}, None)]
    for path, node, parent_path in raw_nodes:
        label = str(node.get("label") or node.get("type") or "document_element")
        text = str(
            node.get("text") or node.get("orig") or node.get("content") or node.get("caption") or ""
        ).strip()
        scalar_metadata = {
            key: value for key, value in node.items()
            if not isinstance(value, (dict, list)) and key not in {"text", "orig", "content"}
        }
        locator = {"document_urn": document["information_urn"], "path": path}
        for key in ("page_no", "page", "page_number", "section", "category_depth",
                    "field", "char_start", "char_end", "url", "xbrl_concept"):
            if node.get(key) is not None:
                locator[key] = node.get(key)
        element, _ = register_information_object(
            conn, object_type="document_element", canonical_subject_id=subject_id,
            domain_id="issuer", owner_id=str(extraction.get("provider") or "document_evidence_adapter"),
            verification_state="observed", lifecycle_state="normalized",
            source_kind="document_element", source_id=f"{source_key}:{path}",
            payload={"element_type": label, "text": text, "metadata": scalar_metadata},
            schema_version="ecosystem.document_element.v1", as_of=extraction.get("as_of"), title=label,
            summary=text[:500] or f"{label} element", source_locator=locator,
            category_ids=("source_provenance",), tags=(label.lower(),),
        )
        registered[path] = element["information_urn"]
        parent_urn = registered.get(parent_path or "", document["information_urn"])
        link_information(
            conn, source_urn=parent_urn, relationship_type="contains",
            target_urn=element["information_urn"], run_id=run_id,
            properties={"path": path, "element_type": label}, commit=False,
        )
    conn.commit()
    return {
        "document_urn": document["information_urn"],
        "element_count": len(registered),
        "element_urns": list(registered.values()),
    }


def record_lineage_event(
    conn: sqlite3.Connection, *, event_type: str, job_id: str, run_id: str,
    producer: str, subject_id: str | None = None, parent_run_id: str | None = None,
    code_version: str | None = None, inputs: Sequence[dict[str, Any]] = (),
    outputs: Sequence[dict[str, Any]] = (), facets: dict[str, Any] | None = None,
    event_time: str | None = None,
) -> dict[str, Any]:
    init_schema(conn)
    event_type = _controlled("lineage_event_types", event_type)
    body = {
        "schema": "ecosystem.lineage_event.v1", "event_type": event_type,
        "event_time": str(event_time or "").strip() or _now(),
        "job_id": _required(job_id, "job_id"), "run_id": _required(run_id, "run_id"),
        "parent_run_id": str(parent_run_id or "").strip() or None,
        "subject_id": str(subject_id or "").strip() or None,
        "producer": _required(producer, "producer"),
        "code_version": str(code_version or "").strip() or None,
        "inputs": list(inputs), "outputs": list(outputs), "facets": facets or {},
    }
    content_hash = _digest(body)
    event_id = "lineage_" + content_hash[:24]
    conn.execute(
        "INSERT OR IGNORE INTO ecosystem_lineage_event VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            event_id, event_type, body["event_time"], body["job_id"], body["run_id"],
            body["parent_run_id"], body["subject_id"], body["producer"], body["code_version"],
            _canonical_json(body["inputs"]), _canonical_json(body["outputs"]),
            _canonical_json(body["facets"]), content_hash, _now(),
        ),
    )
    run_source_id = f"{body['run_id']}:{body['job_id']}:{body['subject_id'] or 'global'}"
    run_object, _ = register_information_object(
        conn, object_type="run", canonical_subject_id=body["subject_id"], domain_id="governance",
        owner_id=body["producer"], verification_state="observed", lifecycle_state="raw",
        source_kind="run", source_id=run_source_id,
        payload={"job_id": body["job_id"], "run_id": body["run_id"], "subject_id": body["subject_id"]},
        schema_version="ecosystem.run.v1", title=body["job_id"],
        summary=f"{body['job_id']} run {body['run_id']}", tags=("lineage",),
    )
    conn.commit()
    return {**body, "lineage_event_id": event_id, "information_urn": run_object["information_urn"], "content_hash": content_hash}


def propose_taxonomy_change(
    conn: sqlite3.Connection, *, scheme_id: str, base_version: str, operation: str,
    term_id: str, proposal: dict[str, Any], rationale: str, proposed_by: str,
) -> dict[str, Any]:
    init_schema(conn)
    if operation not in {"create", "update", "deprecate", "alias"}:
        raise InformationRegistryError(f"Unsupported taxonomy proposal operation: {operation}")
    body = {
        "scheme_id": _required(scheme_id, "scheme_id"),
        "base_version": _required(base_version, "base_version"),
        "operation": operation, "term_id": _required(term_id, "term_id"),
        "proposal": proposal, "rationale": _required(rationale, "rationale"),
        "proposed_by": _required(proposed_by, "proposed_by"), "status": "proposed",
    }
    content_hash = _digest(body)
    proposal_id = "taxonomy_proposal_" + content_hash[:24]
    conn.execute(
        "INSERT OR IGNORE INTO ecosystem_taxonomy_change_proposal VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            proposal_id, body["scheme_id"], body["base_version"], operation, body["term_id"],
            _canonical_json(proposal), body["rationale"], body["proposed_by"], "proposed",
            None, None, content_hash, _now(), None,
        ),
    )
    conn.commit()
    return {**body, "proposal_id": proposal_id, "content_hash": content_hash}


def review_taxonomy_proposal(
    conn: sqlite3.Connection, *, proposal_id: str, decision: str,
    reviewed_by: str, review_note: str,
) -> dict[str, Any]:
    init_schema(conn)
    if decision not in {"approved", "rejected"}:
        raise InformationRegistryError("Taxonomy proposal decision must be approved or rejected")
    row = conn.execute(
        "SELECT status FROM ecosystem_taxonomy_change_proposal WHERE proposal_id=?", (proposal_id,)
    ).fetchone()
    if not row:
        raise InformationRegistryError(f"Unknown taxonomy proposal: {proposal_id}")
    if str(row[0]) != "proposed":
        raise InformationRegistryError(f"Taxonomy proposal is already {row[0]}")
    reviewed_at = _now()
    conn.execute(
        "UPDATE ecosystem_taxonomy_change_proposal SET status=?,reviewed_by=?,review_note=?,reviewed_at=? "
        "WHERE proposal_id=?",
        (decision, _required(reviewed_by, "reviewed_by"), _required(review_note, "review_note"), reviewed_at, proposal_id),
    )
    conn.commit()
    return {"proposal_id": proposal_id, "status": decision, "reviewed_by": reviewed_by, "reviewed_at": reviewed_at}


def search_information(
    conn: sqlite3.Connection, *, query: str | None = None, subject_id: str | None = None,
    domain_id: str | None = None, object_type: str | None = None,
    category_id: str | None = None, verification_states: Iterable[str] = (),
    lifecycle_states: Iterable[str] = (), limit: int = 50,
) -> list[dict[str, Any]]:
    """Filter structured metadata first, then optionally apply full-text matching."""
    init_schema(conn)
    joins: list[str] = []
    clauses: list[str] = []
    values: list[Any] = []
    if category_id:
        joins.append(
            "JOIN ecosystem_information_classification c ON c.information_urn=o.information_urn "
            "AND c.valid_to IS NULL AND c.status!='superseded'"
        )
        clauses.append("c.term_id=?"); values.append(category_id)
    if query:
        joins.append("JOIN ecosystem_information_search s ON s.information_urn=o.information_urn")
        search_definition = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='ecosystem_information_search'"
        ).fetchone()
        is_fts = bool(search_definition and "fts5" in str(search_definition[0]).lower())
        if is_fts:
            # Treat input as one quoted phrase. Escaping quotes avoids exposing FTS syntax to callers.
            clauses.append("ecosystem_information_search MATCH ?")
            values.append('"' + str(query).replace('"', '""') + '"')
        else:
            clauses.append("(s.title LIKE ? OR s.summary LIKE ? OR s.search_text LIKE ?)")
            token = f"%{query}%"; values.extend([token, token, token])
    for column, value in (
        ("o.canonical_subject_id", subject_id), ("o.domain_id", domain_id), ("o.object_type", object_type),
    ):
        if value:
            clauses.append(f"{column}=?"); values.append(value)
    verification = sorted({str(value) for value in verification_states if str(value)})
    lifecycle = sorted({str(value) for value in lifecycle_states if str(value)})
    effective_verification = (
        "COALESCE((SELECT se.verification_state FROM ecosystem_information_state_event se "
        "WHERE se.information_urn=o.information_urn AND se.verification_state IS NOT NULL "
        "ORDER BY se.sequence DESC LIMIT 1),o.verification_state)"
    )
    effective_lifecycle = (
        "COALESCE((SELECT se.lifecycle_state FROM ecosystem_information_state_event se "
        "WHERE se.information_urn=o.information_urn AND se.lifecycle_state IS NOT NULL "
        "ORDER BY se.sequence DESC LIMIT 1),o.lifecycle_state)"
    )
    if verification:
        clauses.append(effective_verification + " IN (%s)" % ",".join("?" for _ in verification)); values.extend(verification)
    if lifecycle:
        clauses.append(effective_lifecycle + " IN (%s)" % ",".join("?" for _ in lifecycle)); values.extend(lifecycle)
    sql = (
        "SELECT DISTINCT o.*," + effective_verification + " AS effective_verification_state,"
        + effective_lifecycle + " AS effective_lifecycle_state "
        "FROM ecosystem_information_object o " + " ".join(joins)
        + (" WHERE " + " AND ".join(clauses) if clauses else "")
        + " ORDER BY o.recorded_at DESC,o.information_urn LIMIT ?"
    )
    values.append(max(1, min(int(limit), 500)))
    return [dict(row) for row in conn.execute(sql, values)]


def subject_dossier(
    conn: sqlite3.Connection, subject_id: str, *, as_of: str | None = None,
    limit_per_type: int = 100,
) -> dict[str, Any]:
    """Build a source-linked, review-aware view of one research subject."""
    init_schema(conn)
    subject_id = _required(subject_id, "subject_id")
    limit_per_type = max(1, min(int(limit_per_type), 500))
    rows = search_information(conn, subject_id=subject_id, limit=500)
    if as_of:
        rows = [row for row in rows if not row["as_of"] or str(row["as_of"])[:10] <= as_of[:10]]
    stale_sources = [row["information_urn"] for row in rows
                     if row["effective_lifecycle_state"] == "stale"
                     or row["effective_verification_state"] == "superseded"]
    impacted = {item["information_urn"] for urn in stale_sources
                for item in information_impact(conn, urn)["affected"]}
    open_conflicts = [dict(row) for row in conn.execute(
        "SELECT c.* FROM ecosystem_claim_conflict c "
        "JOIN ecosystem_information_object a ON a.information_urn=c.claim_urn_a "
        "WHERE a.canonical_subject_id=? AND c.status='open' ORDER BY c.detected_at,c.conflict_id",
        (subject_id,),
    )]
    conflicted = {urn for row in open_conflicts
                  for urn in (row["claim_urn_a"], row["claim_urn_b"])}
    def project(row: dict[str, Any]) -> dict[str, Any]:
        urn = row["information_urn"]
        locator = json.loads(row["source_locator_json"])
        if not locator and row["object_type"] == "claim":
            sidecar = conn.execute(
                "SELECT source_locator_json FROM ecosystem_claim_semantics WHERE claim_id=?",
                (row["source_id"],),
            ).fetchone()
            if sidecar:
                locator = json.loads(sidecar[0])
        categories = [item[0] for item in conn.execute(
            "SELECT term_id FROM ecosystem_information_classification "
            "WHERE information_urn=? AND valid_to IS NULL ORDER BY term_id", (urn,),
        )]
        evidence = [dict(item) for item in conn.execute(
            "SELECT e.source_urn,e.relationship_type,o.source_kind,o.source_id,"
            "o.source_locator_json FROM ecosystem_information_edge e "
            "JOIN ecosystem_information_object o ON o.information_urn=e.source_urn "
            "WHERE e.target_urn=? AND e.relationship_type IN "
            "('supports','contradicts','context_for') ORDER BY e.edge_id", (urn,),
        )]
        predicate, object_value = _structured_claim_fields(conn, row) if row["object_type"] == "claim" else ("", None)
        return {
            "information_urn": urn, "source_kind": row["source_kind"],
            "source_id": row["source_id"], "title": row["title"],
            "summary": row["summary"], "as_of": row["as_of"],
            "predicate": predicate or None, "object_value": object_value,
            "categories": categories, "source_locator": locator,
            "verification_state": row["effective_verification_state"],
            "lifecycle_state": row["effective_lifecycle_state"],
            "evidence": evidence, "affected_by_stale_input": urn in impacted,
            "open_conflict": urn in conflicted,
        }
    usable = [row for row in rows if row["effective_lifecycle_state"] not in {"stale", "archived"}
              and row["effective_verification_state"] not in {"rejected", "superseded"}]
    claims = [project(row) for row in usable if row["object_type"] == "claim"][:limit_per_type]
    packets = [project(row) for row in usable if row["object_type"] == "packet"][:limit_per_type]
    theses = [project(row) for row in usable if row["object_type"] == "thesis"][:limit_per_type]
    return {
        "schema": "ecosystem.subject_dossier.v1", "subject_id": subject_id,
        "as_of": as_of, "generated_at": _now(),
        "current_verified_claims": [item for item in claims
                                    if item["verification_state"] == "supported"
                                    and not item["affected_by_stale_input"]
                                    and not item["open_conflict"]],
        "review_claims": [item for item in claims
                          if item["verification_state"] != "supported"
                          or item["affected_by_stale_input"] or item["open_conflict"]],
        "packets": packets, "latest_thesis": theses[0] if theses else None,
        "open_conflicts": open_conflicts,
        "stale_sources": stale_sources,
        "affected_by_stale_inputs": sorted(impacted),
    }


def validate_registry(conn: sqlite3.Connection) -> dict[str, Any]:
    """Check source coverage, relationship integrity and local SQLite health."""
    init_schema(conn)
    source_tables = {
        "observation": ("ecosystem_observation_store", "observation_id"),
        "packet": ("ecosystem_evidence_packet_store", "packet_id"),
        "claim": ("ecosystem_claim_store", "claim_id"),
        "thesis": ("ecosystem_thesis_revision", "thesis_revision_id"),
    }
    missing: dict[str, int] = {}
    for source_kind, (table, key) in source_tables.items():
        missing[source_kind] = int(conn.execute(
            f"SELECT COUNT(*) FROM {table} s LEFT JOIN ecosystem_information_object o "
            f"ON o.source_kind=? AND o.source_id=s.{key} WHERE o.information_urn IS NULL",
            (source_kind,),
        ).fetchone()[0])
    foreign_keys = [dict(row) for row in conn.execute("PRAGMA foreign_key_check")]
    integrity = str(conn.execute("PRAGMA quick_check").fetchone()[0])
    duplicate_sources = int(conn.execute(
        "SELECT COUNT(*) FROM (SELECT source_kind,source_id FROM ecosystem_information_object "
        "GROUP BY source_kind,source_id HAVING COUNT(*)>1)"
    ).fetchone()[0])
    required_category_types = ("observation", "packet", "claim", "document", "document_element")
    missing_classifications = {
        object_type: int(conn.execute(
            "SELECT COUNT(*) FROM ecosystem_information_object o WHERE o.object_type=? "
            "AND NOT EXISTS (SELECT 1 FROM ecosystem_information_classification c "
            "WHERE c.information_urn=o.information_urn AND c.valid_to IS NULL)",
            (object_type,),
        ).fetchone()[0])
        for object_type in required_category_types
    }
    status = "pass" if (
        not any(missing.values()) and not any(missing_classifications.values())
        and not foreign_keys and integrity == "ok" and not duplicate_sources
    ) else "fail"
    return {
        "schema": "ecosystem.information_registry_validation.v1",
        "status": status,
        "missing_source_objects": missing,
        "missing_required_classifications": missing_classifications,
        "foreign_key_violations": foreign_keys,
        "sqlite_quick_check": integrity,
        "duplicate_source_pairs": duplicate_sources,
        "summary": registry_summary(conn),
    }


def backfill_control_plane(conn: sqlite3.Connection) -> dict[str, int]:
    """Idempotently project existing control-plane records into the semantic registry."""
    init_schema(conn)
    counts = {
        "observations": 0, "packets": 0, "claims": 0,
        "claim_classifications": 0, "legacy_claim_semantics": 0,
        "theses": 0, "lineage_events": 0,
    }
    for row in conn.execute("SELECT * FROM ecosystem_observation_store ORDER BY created_at,observation_id"):
        if object_urn_for_source(conn, "observation", str(row["observation_id"])):
            continue
        body = json.loads(row["payload_json"])
        sync_observation(conn, {**body, "observation_id": row["observation_id"], "created_at": row["created_at"]})
        counts["observations"] += 1
    for row in conn.execute("SELECT * FROM ecosystem_evidence_packet_store ORDER BY created_at,packet_id"):
        if object_urn_for_source(conn, "packet", str(row["packet_id"])):
            continue
        body = json.loads(row["payload_json"])
        sync_packet(conn, {**body, "packet_id": row["packet_id"], "created_at": row["created_at"]})
        counts["packets"] += 1
    has_claim_semantics = bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ecosystem_claim_semantics'"
    ).fetchone())
    for row in conn.execute("SELECT * FROM ecosystem_claim_store ORDER BY created_at,claim_id"):
        if object_urn_for_source(conn, "claim", str(row["claim_id"])):
            continue
        links = [
            {"kind": link["input_kind"], "id": link["input_id"], "hash": link["input_hash"], "stance": link["stance"]}
            for link in conn.execute("SELECT * FROM ecosystem_claim_evidence_link WHERE claim_id=?", (row["claim_id"],))
        ]
        claim = {
            "schema": "ecosystem.claim.v1", "claim_id": row["claim_id"],
            "canonical_subject_id": row["canonical_subject_id"], "run_id": row["run_id"],
            "claim_type": row["claim_type"], "claim_text": row["claim_text"],
            "verification_status": row["verification_status"], "confidence": row["confidence"],
            "as_of": row["as_of"], "evidence": links,
        }
        semantics = conn.execute(
            "SELECT * FROM ecosystem_claim_semantics WHERE claim_id=?", (row["claim_id"],),
        ).fetchone() if has_claim_semantics else None
        if semantics:
            claim["owner_id"] = semantics["owner_id"]
        sync_claim(
            conn, claim,
            category_ids=json.loads(semantics["category_ids_json"]) if semantics else (),
            predicate=semantics["predicate"] if semantics else None,
            object_value=json.loads(semantics["object_value_json"])
            if semantics and semantics["object_value_json"] is not None else None,
            valid_from=semantics["valid_from"] if semantics else None,
            valid_to=semantics["valid_to"] if semantics else None,
            source_locator=json.loads(semantics["source_locator_json"]) if semantics else None,
        )
        counts["claims"] += 1
    claim_defaults = architecture().get("claim_category_defaults", {})
    taxonomy_version = str(information_taxonomy()["version"])
    for row in conn.execute(
        "SELECT information_urn,title,recorded_at FROM ecosystem_information_object "
        "WHERE object_type='claim'"
    ):
        category_id = claim_defaults.get(str(row["title"]))
        if not category_id:
            continue
        classified = conn.execute(
            "SELECT 1 FROM ecosystem_information_classification WHERE information_urn=? "
            "AND scheme_id=? AND scheme_version=? AND term_id=? AND valid_to IS NULL",
            (row["information_urn"], CLASSIFICATION_SCHEME, taxonomy_version, category_id),
        ).fetchone()
        if classified:
            continue
        assign_classification(
            conn, information_urn=str(row["information_urn"]),
            scheme_id=CLASSIFICATION_SCHEME, scheme_version=taxonomy_version,
            term_id=str(category_id), status="verified", confidence="high",
            rationale="producer claim-type mapping", assigned_by="shared_registry",
            valid_from=str(row["recorded_at"]), commit=False,
        )
        counts["claim_classifications"] += 1
    for row in conn.execute(
        "SELECT * FROM ecosystem_claim_store ORDER BY created_at,claim_id"
    ):
        if conn.execute(
            "SELECT 1 FROM ecosystem_claim_semantics WHERE claim_id=?", (row["claim_id"],)
        ).fetchone():
            continue
        decoded = _legacy_claim_semantics(str(row["claim_type"]), str(row["claim_text"]))
        if not decoded:
            continue
        output_packet = conn.execute(
            "SELECT packet_id FROM ecosystem_evidence_packet_store "
            "WHERE run_id=? AND subject_id=? AND kind='fundamental_analysis' "
            "ORDER BY created_at DESC LIMIT 1",
            (row["run_id"], row["canonical_subject_id"]),
        ).fetchone()
        if not output_packet:
            continue
        predicate, object_value, field_path = decoded
        semantics = {
            "category_ids": [], "predicate": predicate, "object_value": object_value,
            "valid_from": None, "valid_to": None,
            "source_locator": {"packet_id": str(output_packet[0]), "field_path": field_path},
            "owner_id": "research_synthesizer",
        }
        conn.execute(
            "INSERT INTO ecosystem_claim_semantics VALUES(?,?,?,?,?,?,?,?,?)",
            (row["claim_id"], "[]", predicate, _canonical_json(object_value),
             None, None, _canonical_json(semantics["source_locator"]),
             semantics["owner_id"], _digest(semantics)),
        )
        counts["legacy_claim_semantics"] += 1
    for row in conn.execute(
        "SELECT information_urn FROM ecosystem_information_object WHERE object_type='claim'"
    ):
        detect_claim_conflicts(conn, str(row["information_urn"]))
    for row in conn.execute("SELECT * FROM ecosystem_thesis_revision ORDER BY created_at,thesis_revision_id"):
        if object_urn_for_source(conn, "thesis", str(row["thesis_revision_id"])):
            continue
        thesis = {
            "schema": "ecosystem.thesis_revision.v1", "thesis_revision_id": row["thesis_revision_id"],
            "canonical_subject_id": row["canonical_subject_id"], "run_id": row["run_id"],
            "revision": row["revision"], "thesis_state": row["thesis_state"],
            "thesis_text": row["thesis_text"],
            "supporting_claim_ids": json.loads(row["supporting_claim_ids_json"]),
            "disconfirming_claim_ids": json.loads(row["disconfirming_claim_ids_json"]),
            "expectation_map": json.loads(row["expectation_map_json"]),
            "stale_after": row["stale_after"], "must_refresh_if": json.loads(row["must_refresh_if_json"]),
        }
        sync_thesis(conn, thesis)
        counts["theses"] += 1
    for row in conn.execute(
        "SELECT * FROM ecosystem_workflow_checkpoint WHERE state='completed' ORDER BY created_at,checkpoint_id"
    ):
        existing = conn.execute(
            "SELECT facets_json FROM ecosystem_lineage_event WHERE event_type='COMPLETE' "
            "AND job_id=? AND run_id=? AND subject_id=?",
            (row["stage"], row["run_id"], row["subject_id"]),
        ).fetchall()
        if any(json.loads(item[0]).get("checkpoint_id") == row["checkpoint_id"] for item in existing):
            continue
        outputs = [{"kind": "packet", "id": row["output_packet_id"]}] if row["output_packet_id"] else []
        record_lineage_event(
            conn, event_type="COMPLETE", job_id=row["stage"], run_id=row["run_id"],
            subject_id=row["subject_id"], producer="research_control_plane",
            outputs=outputs, facets={"checkpoint_id": row["checkpoint_id"], "backfilled": True},
            event_time=row["completed_at"] or row["created_at"],
        )
        counts["lineage_events"] += 1
    conn.commit()
    return counts


def registry_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    init_schema(conn)
    return {
        "schema": "ecosystem.information_registry_summary.v1",
        "objects": conn.execute("SELECT COUNT(*) FROM ecosystem_information_object").fetchone()[0],
        "edges": conn.execute("SELECT COUNT(*) FROM ecosystem_information_edge").fetchone()[0],
        "classifications": conn.execute("SELECT COUNT(*) FROM ecosystem_information_classification").fetchone()[0],
        "taxonomy_versions": [dict(row) for row in conn.execute(
            "SELECT scheme_id,version,status FROM ecosystem_taxonomy_scheme ORDER BY scheme_id,version"
        )],
        "by_type": [dict(row) for row in conn.execute(
            "SELECT object_type,COUNT(*) AS count FROM ecosystem_information_object GROUP BY object_type ORDER BY object_type"
        )],
        "by_domain": [dict(row) for row in conn.execute(
            "SELECT domain_id,COUNT(*) AS count FROM ecosystem_information_object GROUP BY domain_id ORDER BY domain_id"
        )],
        "classification_coverage": [dict(row) for row in conn.execute(
            "SELECT o.object_type,COUNT(*) AS total,"
            "SUM(CASE WHEN EXISTS (SELECT 1 FROM ecosystem_information_classification c "
            "WHERE c.information_urn=o.information_urn AND c.valid_to IS NULL) "
            "THEN 1 ELSE 0 END) AS classified "
            "FROM ecosystem_information_object o GROUP BY o.object_type ORDER BY o.object_type"
        )],
        "lineage_events": conn.execute("SELECT COUNT(*) FROM ecosystem_lineage_event").fetchone()[0],
        "information_state_events": conn.execute(
            "SELECT COUNT(*) FROM ecosystem_information_state_event"
        ).fetchone()[0],
        "structured_claims": conn.execute(
            "SELECT COUNT(*) FROM ecosystem_claim_semantics "
            "WHERE predicate IS NOT NULL AND object_value_json IS NOT NULL"
        ).fetchone()[0],
        "open_claim_conflicts": conn.execute(
            "SELECT COUNT(*) FROM ecosystem_claim_conflict WHERE status='open'"
        ).fetchone()[0],
        "open_taxonomy_proposals": conn.execute(
            "SELECT COUNT(*) FROM ecosystem_taxonomy_change_proposal WHERE status='proposed'"
        ).fetchone()[0],
    }


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Canonical information registry")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "market_intel.db")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    backfill = sub.add_parser("backfill")
    backfill.add_argument("--backup", type=Path)
    sub.add_parser("summary")
    sub.add_parser("validate")
    search = sub.add_parser("search")
    search.add_argument("query", nargs="?")
    search.add_argument("--subject")
    search.add_argument("--domain")
    search.add_argument("--type")
    search.add_argument("--category")
    search.add_argument("--limit", type=int, default=25)
    dossier = sub.add_parser("dossier")
    dossier.add_argument("subject")
    dossier.add_argument("--as-of")
    impact = sub.add_parser("impact")
    impact.add_argument("information_urn")
    conflicts = sub.add_parser("conflicts")
    conflicts.add_argument("--subject")
    conflicts.add_argument("--status", choices=("open", "confirmed", "dismissed"), default="open")
    review_conflict = sub.add_parser("review-conflict")
    review_conflict.add_argument("conflict_id")
    review_conflict.add_argument("--decision", choices=("confirmed", "dismissed"), required=True)
    review_conflict.add_argument("--reviewer", required=True)
    review_conflict.add_argument("--note", required=True)
    args = parser.parse_args(argv)
    if args.command == "backfill" and args.backup:
        source = args.db.resolve()
        backup = args.backup.resolve()
        if not source.is_file():
            parser.error(f"Database does not exist: {source}")
        if backup.exists():
            parser.error(f"Backup already exists: {backup}")
        backup.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(source) as source_db, sqlite3.connect(backup) as backup_db:
            source_db.backup(backup_db)
    conn = _connect(args.db)
    try:
        if args.command == "init":
            result: Any = {"status": "initialized", **registry_summary(conn)}
        elif args.command == "backfill":
            result = {"backfill": backfill_control_plane(conn), "summary": registry_summary(conn)}
        elif args.command == "summary":
            result = registry_summary(conn)
        elif args.command == "validate":
            result = validate_registry(conn)
        elif args.command == "dossier":
            result = subject_dossier(conn, args.subject, as_of=args.as_of)
        elif args.command == "impact":
            result = information_impact(conn, args.information_urn)
        elif args.command == "conflicts":
            sql = "SELECT c.* FROM ecosystem_claim_conflict c"
            conditions = ["c.status=?"]
            values = [args.status]
            if args.subject:
                sql += " JOIN ecosystem_information_object o ON o.information_urn=c.claim_urn_a"
                conditions.append("o.canonical_subject_id=?")
                values.append(args.subject)
            result = [dict(row) for row in conn.execute(
                sql + " WHERE " + " AND ".join(conditions) + " ORDER BY c.detected_at,c.conflict_id", values,
            )]
        elif args.command == "review-conflict":
            result = review_claim_conflict(
                conn, conflict_id=args.conflict_id, decision=args.decision,
                reviewed_by=args.reviewer, review_note=args.note,
            )
        else:
            result = search_information(
                conn, query=args.query, subject_id=args.subject, domain_id=args.domain,
                object_type=args.type, category_id=args.category, limit=args.limit,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if args.command == "validate" and result["status"] != "pass" else 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
