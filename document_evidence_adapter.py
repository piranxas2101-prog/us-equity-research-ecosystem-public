"""On-demand local document evidence adapter using optional Docling.

The adapter preserves the full structured export and Markdown rendering, then
publishes an immutable packet into the existing control plane.  It never calls
an LLM and accepts local files only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from control_plane_store import DEFAULT_CONTROL_PLANE_DB, connect
import information_registry
import research_control_plane


EXTRACTION_SCHEMA = "ecosystem.document_extraction.v1"
SUPPORTED_SUFFIXES = {".pdf", ".docx", ".pptx", ".html", ".htm", ".md", ".txt", ".png", ".jpg", ".jpeg"}


def _count_labels(value: Any) -> tuple[int, set[int]]:
    tables = 0
    pages: set[int] = set()
    if isinstance(value, dict):
        label = str(value.get("label") or value.get("type") or "").lower()
        tables += int(label == "table" or label.endswith("tableitem"))
        for key in ("page_no", "page", "page_number"):
            raw = value.get(key)
            if isinstance(raw, int):
                pages.add(raw)
        for child in value.values():
            child_tables, child_pages = _count_labels(child)
            tables += child_tables; pages.update(child_pages)
    elif isinstance(value, list):
        for child in value:
            child_tables, child_pages = _count_labels(child)
            tables += child_tables; pages.update(child_pages)
    return tables, pages


def convert_local_document(source: Path, *, converter: Any | None = None) -> dict[str, Any]:
    path = Path(source).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported document type: {path.suffix}")
    raw = path.read_bytes()
    if converter is None:
        try:
            from docling.document_converter import DocumentConverter
        except ImportError as exc:
            raise RuntimeError(
                "Docling extension is not installed; install requirements-extensions.txt first."
            ) from exc
        converter = DocumentConverter()
    result = converter.convert(str(path))
    document = getattr(result, "document", None)
    if document is None:
        raise RuntimeError("Docling conversion did not return a document")
    structure = document.export_to_dict()
    markdown = document.export_to_markdown()
    if not isinstance(structure, dict) or not isinstance(markdown, str):
        raise RuntimeError("Docling returned an incompatible document representation")
    table_count, pages = _count_labels(structure)
    return {
        "schema": EXTRACTION_SCHEMA,
        "source": {
            "uri": path.as_uri(), "name": path.name, "suffix": path.suffix.lower(),
            "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
        },
        "provider": "docling_local",
        "conversion_status": str(getattr(result, "status", "completed")),
        "markdown": markdown,
        "structure": structure,
        "metrics": {
            "markdown_chars": len(markdown), "table_count": table_count,
            "page_numbers": sorted(pages), "page_count_observed": len(pages),
        },
        "llm_calls": 0,
    }


def publish_document_evidence(
    *, source: Path, subject_id: str, run_id: str, document_type: str,
    db_path: Path = DEFAULT_CONTROL_PLANE_DB, converter: Any | None = None,
) -> dict[str, Any]:
    extraction = convert_local_document(source, converter=converter)
    db = connect(db_path)
    try:
        source_data = extraction["source"]
        observation, _ = research_control_plane.record_observation(
            db, subject_id=subject_id, capability="document.evidence",
            provider="docling_local", source_ref=source_data["uri"],
            observed_at=datetime.fromtimestamp(Path(source).stat().st_mtime, timezone.utc).isoformat(),
            as_of=None, run_id=run_id,
            status="available" if extraction["markdown"].strip() else "missing",
            reason_code=None if extraction["markdown"].strip() else "empty_document",
            payload=extraction,
        )
        document_registry = information_registry.register_document_extraction(
            db, extraction=extraction, subject_id=subject_id, run_id=run_id,
            document_type=document_type, observation_id=observation["observation_id"],
        )
        inputs = [("observation", observation["observation_id"])]
        fingerprint = research_control_plane.input_fingerprint(
            stage="document_evidence", subject_id=subject_id, inputs=inputs,
            config={"document_type": document_type, "source_sha256": source_data["sha256"]},
            implementation_version="document_evidence_adapter.v1",
        )
        checkpoint = research_control_plane.acquire_checkpoint(
            db, run_id=run_id, stage="document_evidence", subject_id=subject_id,
            fingerprint=fingerprint, details={"source": source_data["uri"], "document_type": document_type},
        )
        if checkpoint["status"] == "reused":
            return {
                "status": "reused", "observation_id": observation["observation_id"],
                "control_packet_id": checkpoint["output_packet_id"],
                "checkpoint_id": checkpoint["checkpoint_id"],
                "document_registry": document_registry,
            }
        quality = "ready" if extraction["markdown"].strip() else "evidence_gap"
        if quality != "ready":
            research_control_plane.record_evidence_gap(
                db, subject_id=subject_id, run_id=run_id, field="document_content",
                reason_code="empty_document", owner="document_evidence_adapter",
                source_event_id=observation["observation_id"], payload={"source": source_data["uri"]},
            )
        packet, created = research_control_plane.build_packet(
            db, kind="document_evidence", subject_id=subject_id, run_id=run_id,
            producer_name="document_evidence_adapter", producer_version="v1",
            quality_state=quality, inputs=inputs,
            payload={"document_type": document_type, "extraction": extraction},
        )
        packet_urn = information_registry.object_urn_for_source(db, "packet", packet["packet_id"])
        if packet_urn:
            information_registry.link_information(
                db, source_urn=packet_urn, relationship_type="derived_from",
                target_urn=document_registry["document_urn"], run_id=run_id,
            )
        completed = research_control_plane.complete_checkpoint(db, checkpoint["checkpoint_id"], packet)
        return {
            "status": completed["status"], "observation_id": observation["observation_id"],
            "control_packet_id": packet["packet_id"], "checkpoint_id": completed["checkpoint_id"],
            "created": created, "metrics": extraction["metrics"],
            "document_registry": document_registry,
        }
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path); parser.add_argument("--subject-id", required=True)
    parser.add_argument("--run-id", required=True); parser.add_argument("--document-type", required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_CONTROL_PLANE_DB)
    args = parser.parse_args(argv)
    result = publish_document_evidence(
        source=args.source, subject_id=args.subject_id, run_id=args.run_id,
        document_type=args.document_type, db_path=args.db,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
