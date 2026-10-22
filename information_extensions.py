"""Optional local extensions over the canonical information registry.

External text is imported only when a complete body is available. Vector ranking,
graph communities, review pages and lineage export operate on the same SQLite
information objects; none of them calls a broker, model API or network service.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
from html import escape
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Sequence
from uuid import NAMESPACE_URL, uuid5

import information_registry as registry
import research_control_plane as control


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "market_intel.db"
VECTOR_MODEL = "local_char_ngram_v1"
VECTOR_DIMS = 256
OPENLINEAGE_PRODUCER = "urn:ecosystem:information_extensions:openlineage_export:v1"


class _HTMLBlocks(HTMLParser):
    BLOCKS = {"h1": "title", "h2": "section_title", "h3": "section_title",
              "h4": "section_title", "p": "narrative_text", "li": "list_item",
              "tr": "table_row", "blockquote": "quote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict[str, str]] = []
        self.active_tag: str | None = None
        self.parts: list[str] = []
        self.skip = 0
        self.section: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self.skip += 1
        elif not self.skip and tag in self.BLOCKS:
            self._flush()
            self.active_tag = tag

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.skip:
            self.skip -= 1
        elif tag == self.active_tag:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self.skip and self.active_tag:
            self.parts.append(data)

    def _flush(self) -> None:
        if not self.active_tag:
            return
        value = re.sub(r"\s+", " ", " ".join(self.parts)).strip()
        if value:
            label = self.BLOCKS[self.active_tag]
            if label in {"title", "section_title"}:
                self.section = value
            self.blocks.append({"label": label, "text": value,
                                "section": self.section or ""})
        self.active_tag = None
        self.parts = []

    def finish(self) -> list[dict[str, str]]:
        self._flush()
        return self.blocks


def _sections(body: str, *, format_hint: str) -> tuple[str, list[dict[str, Any]]]:
    if format_hint == "html":
        parser = _HTMLBlocks()
        parser.feed(body)
        blocks = parser.finish()
        if not blocks:
            raise ValueError("HTML document has no readable text blocks")
    elif format_hint == "text":
        blocks = []
        section = ""
        for part in re.split(r"\n\s*\n", body):
            value = part.strip()
            if not value:
                continue
            label = "section_title" if value.startswith("#") else "narrative_text"
            if label == "section_title":
                section = value.lstrip("# ").strip()
            blocks.append({"label": label, "text": value, "section": section})
    else:
        raise ValueError("format_hint must be html or text")
    normalized = "\n\n".join(item["text"] for item in blocks)
    offset = 0
    elements = []
    for item in blocks:
        text = item["text"]
        elements.append({**item, "char_start": offset, "char_end": offset + len(text)})
        offset += len(text) + 2
    return normalized, elements


def ingest_external_document(
    conn: sqlite3.Connection, *, subject_id: str, run_id: str,
    provider: str, uri: str, title: str, body: str,
    document_type: str, format_hint: str = "text",
    published_at: str | None = None, observed_at: str | None = None,
) -> dict[str, Any]:
    """Publish complete IR/news body, structure, source observation and packet."""
    if document_type not in {"ir_document", "news_article"}:
        raise ValueError("document_type must be ir_document or news_article")
    for field, value in (("subject_id", subject_id), ("run_id", run_id),
                         ("provider", provider), ("uri", uri), ("title", title), ("body", body)):
        if not str(value or "").strip():
            raise ValueError(f"{field} is required")
    normalized, elements = _sections(body, format_hint=format_hint)
    raw_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    observed = observed_at or published_at or datetime.now(timezone.utc).isoformat()
    control.init_schema(conn)
    observation, _ = control.record_observation(
        conn, subject_id=subject_id, capability=f"document.{document_type}",
        provider=provider, source_ref=uri, observed_at=observed,
        as_of=published_at, run_id=run_id, status="available",
        payload={"title": title, "source_sha256": raw_hash, "document_type": document_type,
                 "format": format_hint, "section_count": len(elements), "complete_body": True},
    )
    extraction = {
        "schema": "ecosystem.external_document_extraction.v1",
        "source": {"uri": uri, "name": title, "sha256": raw_hash,
                   "published_at": published_at},
        "source_identity_uri": True, "provider": provider,
        "verification_state": "observed", "conversion_status": "complete_body",
        "as_of": published_at, "markdown": normalized,
        "structure": {"elements": [{**item, "url": uri} for item in elements]},
        "metrics": {"character_count": len(normalized), "section_count": len(elements)},
    }
    document = registry.register_document_extraction(
        conn, extraction=extraction, subject_id=subject_id, run_id=run_id,
        document_type=document_type, observation_id=observation["observation_id"],
    )
    packet, _ = control.build_packet(
        conn, kind="document_evidence", subject_id=subject_id, run_id=run_id,
        producer_name="information_extensions", producer_version="v1",
        as_of=published_at, quality_state="provisional",
        inputs=[("observation", observation["observation_id"])],
        payload={"document_type": document_type, "document_urn": document["document_urn"],
                 "element_urns": document["element_urns"], "source_sha256": raw_hash,
                 "source_uri": uri, "complete_body": True},
    )
    packet_urn = registry.object_urn_for_source(conn, "packet", packet["packet_id"])
    if packet_urn:
        registry.link_information(
            conn, source_urn=packet_urn, relationship_type="derived_from",
            target_urn=document["document_urn"], run_id=run_id,
        )
    registry.record_lineage_event(
        conn, event_type="COMPLETE", job_id=f"ingest_{document_type}",
        run_id=run_id, subject_id=subject_id, producer="information_extensions",
        inputs=[{"kind": "source", "id": uri}],
        outputs=[{"kind": "packet", "id": packet["packet_id"]},
                 {"kind": "document", "id": document["document_urn"]}],
        facets={"source_sha256": raw_hash, "section_count": len(elements)},
        event_time=observed,
    )
    return {"observation_id": observation["observation_id"],
            "packet_id": packet["packet_id"], **document,
            "source_sha256": raw_hash}


def _init_vector_schema(conn: sqlite3.Connection) -> None:
    registry.init_schema(conn)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ecosystem_information_vector ("
        "information_urn TEXT NOT NULL REFERENCES ecosystem_information_object(information_urn) "
        "ON DELETE CASCADE, model_id TEXT NOT NULL, dimensions INTEGER NOT NULL, "
        "vector_json TEXT NOT NULL, text_sha256 TEXT NOT NULL, created_at TEXT NOT NULL, "
        "PRIMARY KEY(information_urn,model_id))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_information_vector_model "
        "ON ecosystem_information_vector(model_id,information_urn)"
    )
    conn.commit()


def _local_vector(text: str) -> list[float]:
    """Dependency-free lexical vector; callers may supply a local semantic model."""
    vector = [0.0] * VECTOR_DIMS
    words = re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE)
    for word in words:
        token = f"^{word}$"
        grams = [token[index:index + 3] for index in range(max(1, len(token) - 2))]
        for gram in grams:
            digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=4).digest()
            index = int.from_bytes(digest, "big") % VECTOR_DIMS
            vector[index] += 1.0
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def _vectorize(
    text: str, model_id: str,
    embedding_fn: Callable[[str], Sequence[float]] | None,
) -> list[float]:
    if model_id == VECTOR_MODEL and embedding_fn is None:
        return _local_vector(text)
    if embedding_fn is None:
        raise ValueError("A local embedding_fn is required for a custom model_id")
    vector = [float(value) for value in embedding_fn(text)]
    if not vector or not all(math.isfinite(value) for value in vector):
        raise ValueError("Embedding must be a nonempty finite vector")
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def _indexable_text(conn: sqlite3.Connection, row: sqlite3.Row) -> str | None:
    state = registry.effective_information_state(conn, str(row["information_urn"]))
    if state["lifecycle_state"] in {"stale", "archived"} or state["verification_state"] in {"rejected", "superseded"}:
        return None
    if row["object_type"] == "claim":
        return str(row["summary"] or "").strip() if state["verification_state"] == "supported" else None
    if row["object_type"] != "document_element":
        return None
    locator = json.loads(row["source_locator_json"])
    parent = conn.execute(
        "SELECT payload_json,verification_state FROM ecosystem_information_object "
        "WHERE information_urn=? AND object_type='document'",
        (locator.get("document_urn"),),
    ).fetchone()
    if not parent or parent["verification_state"] not in {"observed", "supported"}:
        return None
    parent_state = registry.effective_information_state(conn, str(locator["document_urn"]))
    if parent_state["lifecycle_state"] in {"stale", "archived"} or parent_state["verification_state"] in {"rejected", "superseded"}:
        return None
    if json.loads(parent["payload_json"]).get("conversion_status") == "candidate_excerpt_only":
        return None
    return str(json.loads(row["payload_json"]).get("text") or "").strip() or None


def index_selected_vectors(
    conn: sqlite3.Connection, *, subject_id: str, model_id: str = VECTOR_MODEL,
    embedding_fn: Callable[[str], Sequence[float]] | None = None,
    max_objects: int = 1000,
) -> dict[str, Any]:
    """Index only a named research subject and verified/complete text."""
    if not subject_id.strip():
        raise ValueError("subject_id is required for bounded vector indexing")
    _init_vector_schema(conn)
    rows = conn.execute(
        "SELECT * FROM ecosystem_information_object WHERE canonical_subject_id=? "
        "AND object_type IN ('claim','document_element') ORDER BY recorded_at DESC LIMIT ?",
        (subject_id, max(1, min(int(max_objects), 5000))),
    ).fetchall()
    indexed = skipped = 0
    for row in rows:
        text = _indexable_text(conn, row)
        if not text:
            skipped += 1
            continue
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        existing = conn.execute(
            "SELECT text_sha256 FROM ecosystem_information_vector "
            "WHERE information_urn=? AND model_id=?",
            (row["information_urn"], model_id),
        ).fetchone()
        if existing:
            if existing[0] != text_hash:
                raise registry.InformationRegistryError("Vector source text changed for immutable object")
            continue
        vector = _vectorize(text, model_id, embedding_fn)
        conn.execute(
            "INSERT INTO ecosystem_information_vector VALUES(?,?,?,?,?,?)",
            (row["information_urn"], model_id, len(vector),
             json.dumps(vector, separators=(",", ":")), text_hash,
             datetime.now(timezone.utc).isoformat()),
        )
        indexed += 1
    conn.commit()
    return {"subject_id": subject_id, "model_id": model_id,
            "indexed": indexed, "skipped": skipped, "eligible_seen": len(rows)}


def vector_search(
    conn: sqlite3.Connection, *, subject_id: str, query: str,
    model_id: str = VECTOR_MODEL,
    embedding_fn: Callable[[str], Sequence[float]] | None = None,
    domain_id: str | None = None, category_id: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Apply metadata and state filters before vector scoring."""
    if not subject_id.strip() or not query.strip():
        raise ValueError("subject_id and query are required")
    _init_vector_schema(conn)
    category_filter = (
        "AND EXISTS (SELECT 1 FROM ecosystem_information_classification c "
        "WHERE c.information_urn=o.information_urn AND c.term_id=? "
        "AND c.valid_to IS NULL AND c.status!='superseded')"
    ) if category_id else ""
    clauses = ["o.canonical_subject_id=?", "v.model_id=?"]
    values: list[Any] = [subject_id, model_id]
    if domain_id:
        clauses.append("o.domain_id=?")
        values.append(domain_id)
    if category_id:
        clauses.append(category_filter.removeprefix("AND "))
        values.append(category_id)
    candidate_sql = (
        "SELECT o.*,v.dimensions,v.vector_json FROM ecosystem_information_vector v "
        "JOIN ecosystem_information_object o ON o.information_urn=v.information_urn "
        "WHERE " + " AND ".join(clauses) + " ORDER BY o.recorded_at DESC LIMIT 5000"
    )
    candidates = conn.execute(candidate_sql, values).fetchall()
    stale_dependencies = set(registry.subject_dossier(conn, subject_id)["affected_by_stale_inputs"])
    query_vector = _vectorize(query, model_id, embedding_fn)
    ranked = []
    for candidate in candidates:
        urn = str(candidate["information_urn"])
        if urn in stale_dependencies or candidate["object_type"] not in {"claim", "document_element"}:
            continue
        if not _indexable_text(conn, candidate):
            continue
        if int(candidate["dimensions"]) != len(query_vector):
            continue
        vector = json.loads(candidate["vector_json"])
        score = sum(a * b for a, b in zip(query_vector, vector))
        ranked.append({"information_urn": urn, "object_type": candidate["object_type"],
                       "summary": candidate["summary"], "score": round(score, 6),
                       "source_locator": json.loads(candidate["source_locator_json"])})
    ranked.sort(key=lambda row: (-row["score"], row["information_urn"]))
    if ranked:
        return {"model_id": model_id, "mode": "vector", "results": ranked[:max(1, min(limit, 100))]}
    fallback = registry.search_information(
        conn, query=query, subject_id=subject_id, domain_id=domain_id,
        category_id=category_id, limit=limit,
        lifecycle_states=("normalized", "reviewed", "published"),
        verification_states=("observed", "supported"),
    )
    return {"model_id": model_id, "mode": "full_text_fallback",
            "results": [{"information_urn": row["information_urn"],
                         "object_type": row["object_type"], "summary": row["summary"],
                         "source_locator": json.loads(row["source_locator_json"])}
                        for row in fallback if row["object_type"] in {"claim", "document_element"}
                        and row["information_urn"] not in stale_dependencies
                        and _indexable_text(conn, row)]}


def graph_communities(
    conn: sqlite3.Connection, *, subjects: Sequence[str] = (),
    max_edges: int = 5000,
) -> dict[str, Any]:
    """Summarize connected peer/member groups in selected research evidence."""
    registry.init_schema(conn)
    selected = {value.strip() for value in subjects if value.strip()}
    rows = conn.execute(
        "SELECT a.canonical_subject_id AS source_subject,"
        "b.canonical_subject_id AS target_subject,e.relationship_type "
        "FROM ecosystem_information_edge e "
        "JOIN ecosystem_information_object a ON a.information_urn=e.source_urn "
        "JOIN ecosystem_information_object b ON b.information_urn=e.target_urn "
        "WHERE e.relationship_type IN ('peer_of','member_of') "
        "ORDER BY e.edge_id LIMIT ?",
        (max(1, min(int(max_edges), 50000)),),
    ).fetchall()
    adjacency: dict[str, set[str]] = defaultdict(set)
    edge_counts: Counter[tuple[str, str]] = Counter()
    for row in rows:
        left = str(row["source_subject"] or "")
        right = str(row["target_subject"] or "")
        if not left or not right or left == right:
            continue
        adjacency[left].add(right)
        adjacency[right].add(left)
        edge_counts[tuple(sorted((left, right)))] += 1
    visited: set[str] = set()
    communities = []
    for start in sorted(adjacency):
        if start in visited:
            continue
        stack = [start]
        component: set[str] = set()
        while stack:
            subject = stack.pop()
            if subject in visited:
                continue
            visited.add(subject)
            component.add(subject)
            stack.extend(sorted(adjacency[subject] - visited))
        if len(component) < 2:
            continue
        if selected and not component.intersection(selected):
            continue
        names = sorted(component)
        categories: Counter[str] = Counter()
        claim_count = 0
        for subject in names:
            claim_count += int(conn.execute(
                "SELECT COUNT(*) FROM ecosystem_information_object "
                "WHERE canonical_subject_id=? AND object_type='claim'", (subject,),
            ).fetchone()[0])
            categories.update(row[0] for row in conn.execute(
                "SELECT DISTINCT c.term_id FROM ecosystem_information_classification c "
                "JOIN ecosystem_information_object o ON o.information_urn=c.information_urn "
                "WHERE o.canonical_subject_id=? AND c.valid_to IS NULL", (subject,),
            ))
        edge_count = sum(count for pair, count in edge_counts.items()
                         if pair[0] in component and pair[1] in component)
        communities.append({
            "community_id": "community_" + hashlib.sha256(
                json.dumps(names, separators=(",", ":")).encode("utf-8")
            ).hexdigest()[:20],
            "subjects": names, "subject_count": len(names), "edge_count": edge_count,
            "claim_count": claim_count,
            "top_categories": [term for term, _ in categories.most_common(8)],
            "summary": f"{len(names)} connected research subjects; {edge_count} peer/member links; "
                       f"{claim_count} claims.",
        })
    communities.sort(key=lambda item: (-item["subject_count"], item["community_id"]))
    return {"schema": "ecosystem.graph_communities.v1", "community_count": len(communities),
            "communities": communities, "bounded_edge_count": len(rows)}


def openlineage_events(
    conn: sqlite3.Connection, *, run_id: str | None = None, limit: int = 10000,
) -> list[dict[str, Any]]:
    """Map stored events to OpenLineage RunEvent core fields without network I/O."""
    registry.init_schema(conn)
    sql = "SELECT * FROM ecosystem_lineage_event"
    values: list[Any] = []
    if run_id:
        sql += " WHERE run_id=?"
        values.append(run_id)
    sql += " ORDER BY event_time,lineage_event_id LIMIT ?"
    values.append(max(1, min(int(limit), 100000)))
    events = []
    for row in conn.execute(sql, values):
        subject = str(row["subject_id"] or "global")
        stable_run = str(uuid5(NAMESPACE_URL,
                               f"ecosystem.local/{row['job_id']}/{subject}/{row['run_id']}"))
        def datasets(raw: str) -> list[dict[str, str]]:
            result = []
            for item in json.loads(raw):
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("kind") or "unknown")
                identifier = str(item.get("id") or "")
                if identifier:
                    result.append({"namespace": "ecosystem://local",
                                   "name": f"{kind}/{identifier}"})
            return result
        events.append({
            "eventType": row["event_type"], "eventTime": row["event_time"],
            "run": {"runId": stable_run},
            "job": {"namespace": "ecosystem.local", "name": row["job_id"]},
            "inputs": datasets(row["inputs_json"]),
            "outputs": datasets(row["outputs_json"]),
            "producer": OPENLINEAGE_PRODUCER,
        })
    return events


def review_view(conn: sqlite3.Connection) -> str:
    """Create a local, escaped HTML inbox for governance review."""
    registry.init_schema(conn)
    conflicts = [dict(row) for row in conn.execute(
        "SELECT c.*,a.canonical_subject_id FROM ecosystem_claim_conflict c "
        "JOIN ecosystem_information_object a ON a.information_urn=c.claim_urn_a "
        "WHERE c.status='open' ORDER BY c.detected_at,c.conflict_id"
    )]
    proposals = [dict(row) for row in conn.execute(
        "SELECT proposal_id,scheme_id,base_version,operation,term_id,rationale,proposed_by "
        "FROM ecosystem_taxonomy_change_proposal WHERE status='proposed' "
        "ORDER BY created_at,proposal_id"
    )]
    unclassified = [dict(row) for row in conn.execute(
        "SELECT o.information_urn,o.object_type,o.canonical_subject_id,o.title "
        "FROM ecosystem_information_object o WHERE o.object_type IN "
        "('observation','packet','claim','document','document_element') "
        "AND NOT EXISTS (SELECT 1 FROM ecosystem_information_classification c "
        "WHERE c.information_urn=o.information_urn AND c.valid_to IS NULL) "
        "ORDER BY o.recorded_at DESC LIMIT 200"
    )]
    def row(cells: Sequence[Any]) -> str:
        return "<tr class='review-row'>" + "".join(
            f"<td>{escape(str(value if value is not None else ''))}</td>" for value in cells
        ) + "</tr>"
    conflict_rows = "".join(row((item["conflict_id"], item["canonical_subject_id"],
                                 item["predicate"], item["period_key"],
                                 item["claim_urn_a"], item["claim_urn_b"]))
                            for item in conflicts)
    proposal_rows = "".join(row((item["proposal_id"], item["scheme_id"],
                                 item["operation"], item["term_id"], item["rationale"],
                                 item["proposed_by"])) for item in proposals)
    unclassified_rows = "".join(row((item["information_urn"], item["object_type"],
                                     item["canonical_subject_id"], item["title"]))
                                for item in unclassified)
    return """<!doctype html><html lang="en"><meta charset="utf-8"><title>Information review</title>
<style>body{font:15px system-ui;margin:2rem;max-width:110rem;color:#18212b;background:#f7f9fb}
h1,h2{color:#14283e}input{padding:.7rem;width:min(32rem,90%);border:1px solid #9aa8b7;border-radius:.4rem}
table{border-collapse:collapse;width:100%;background:#fff;margin:1rem 0 2rem}th,td{border:1px solid #d6dfe8;padding:.55rem;text-align:left;vertical-align:top;overflow-wrap:anywhere}
th{background:#eaf0f6}code{background:#eaf0f6;padding:.15rem .3rem}</style>
<h1>Information review inbox</h1><p>Local read-only view. Decisions use the registry CLI and keep source records immutable.</p>
<input id="filter" aria-label="Filter rows" placeholder="Filter rows" oninput="for(const r of document.querySelectorAll('.review-row'))r.hidden=!r.textContent.toLowerCase().includes(this.value.toLowerCase())">
<h2>Open claim conflicts</h2><table><tr><th>ID</th><th>Subject</th><th>Predicate</th><th>Period</th><th>Claim A</th><th>Claim B</th></tr>""" + conflict_rows + """</table>
<p>Review command: <code>information_registry.py review-conflict ID --decision confirmed|dismissed --reviewer NAME --note REASON</code></p>
<h2>Taxonomy proposals</h2><table><tr><th>ID</th><th>Scheme</th><th>Operation</th><th>Term</th><th>Rationale</th><th>Proposed by</th></tr>""" + proposal_rows + """</table>
<p>Review command: <code>information_extensions.py review-taxonomy ID --decision approved|rejected --reviewer NAME --note REASON</code></p>
<h2>Unclassified objects</h2><table><tr><th>URN</th><th>Type</th><th>Subject</th><th>Title</th></tr>""" + unclassified_rows + """</table></html>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local information architecture extensions")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest-text", help="Import a complete local IR/news body")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--subject", required=True)
    ingest.add_argument("--run-id", required=True)
    ingest.add_argument("--provider", required=True)
    ingest.add_argument("--uri", required=True)
    ingest.add_argument("--title", required=True)
    ingest.add_argument("--type", choices=("ir_document", "news_article"), required=True)
    ingest.add_argument("--format", choices=("text", "html"))
    ingest.add_argument("--published-at")

    index = sub.add_parser("index-vectors", help="Index eligible text for one subject")
    index.add_argument("--subject", required=True)
    index.add_argument("--limit", type=int, default=1000)

    search = sub.add_parser("vector-search", help="Rank subject evidence locally")
    search.add_argument("query")
    search.add_argument("--subject", required=True)
    search.add_argument("--domain")
    search.add_argument("--category")
    search.add_argument("--limit", type=int, default=10)

    communities = sub.add_parser("communities", help="Summarize peer/member groups")
    communities.add_argument("--subject", action="append", default=[])
    communities.add_argument("--max-edges", type=int, default=5000)

    lineage = sub.add_parser("export-lineage", help="Write JSONL RunEvents")
    lineage.add_argument("--out", type=Path, required=True)
    lineage.add_argument("--run-id")
    lineage.add_argument("--limit", type=int, default=10000)

    review = sub.add_parser("review-view", help="Write escaped local HTML review inbox")
    review.add_argument("--out", type=Path, required=True)

    decision = sub.add_parser("review-taxonomy", help="Record a taxonomy proposal decision")
    decision.add_argument("proposal_id")
    decision.add_argument("--decision", choices=("approved", "rejected"), required=True)
    decision.add_argument("--reviewer", required=True)
    decision.add_argument("--note", required=True)

    args = parser.parse_args(argv)
    args.db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(args.db)) as conn:
        conn.row_factory = sqlite3.Row
        if args.command == "ingest-text":
            source_path = args.path.resolve(strict=True)
            format_hint = args.format or ("html" if source_path.suffix.lower() in
                                           {".html", ".htm"} else "text")
            result = ingest_external_document(
                conn, subject_id=args.subject, run_id=args.run_id,
                provider=args.provider, uri=args.uri, title=args.title,
                body=source_path.read_text(encoding="utf-8"), document_type=args.type,
                format_hint=format_hint, published_at=args.published_at,
                observed_at=datetime.fromtimestamp(
                    source_path.stat().st_mtime, timezone.utc).isoformat(),
            )
        elif args.command == "index-vectors":
            result = index_selected_vectors(conn, subject_id=args.subject,
                                            max_objects=args.limit)
        elif args.command == "vector-search":
            result = vector_search(conn, subject_id=args.subject, query=args.query,
                                   domain_id=args.domain, category_id=args.category,
                                   limit=args.limit)
        elif args.command == "communities":
            result = graph_communities(conn, subjects=args.subject, max_edges=args.max_edges)
        elif args.command == "export-lineage":
            events = openlineage_events(conn, run_id=args.run_id, limit=args.limit)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n"
                                        for event in events), encoding="utf-8")
            result = {"event_count": len(events), "path": str(args.out)}
        elif args.command == "review-view":
            page = review_view(conn)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(page, encoding="utf-8")
            result = {"path": str(args.out), "bytes": len(page.encode("utf-8"))}
        else:
            result = registry.review_taxonomy_proposal(
                conn, proposal_id=args.proposal_id, decision=args.decision,
                reviewed_by=args.reviewer, review_note=args.note,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
