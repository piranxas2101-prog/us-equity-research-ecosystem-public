"""Optional EdgarTools filing-catalog adapter for the shared evidence plane."""
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
import os
from pathlib import Path
from typing import Any, Protocol

from control_plane_store import DEFAULT_CONTROL_PLANE_DB, connect
import research_control_plane


CATALOG_SCHEMA = "ecosystem.sec_filing_catalog.v1"
DEFAULT_FORMS = ("10-K", "10-Q", "8-K", "20-F", "40-F", "4")


def _date_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    text = str(value).strip()
    return text[:10] if len(text) >= 10 else None


def normalize_filing_catalog(
    records: list[dict[str, Any]], *, identifier: str, as_of: str,
    forms: tuple[str, ...] = DEFAULT_FORMS,
) -> dict[str, Any]:
    cutoff = _date_text(as_of)
    if not cutoff:
        raise ValueError("as_of must contain an ISO date")
    allowed = set(forms)
    normalized = []
    for raw in records:
        form = str(raw.get("form") or "").upper().strip()
        filing_date = _date_text(raw.get("filing_date") or raw.get("filed"))
        if form not in allowed or not filing_date or filing_date > cutoff:
            continue
        accession = str(raw.get("accession_number") or raw.get("accession_no") or raw.get("accession") or "").strip() or None
        normalized.append({
            "form": form, "filing_date": filing_date, "accession_number": accession,
            "primary_document": raw.get("primary_document") or raw.get("primary_doc"),
            "is_xbrl": raw.get("is_xbrl"), "is_inline_xbrl": raw.get("is_inline_xbrl"),
            "company": raw.get("company") or raw.get("company_name"),
        })
    normalized.sort(key=lambda row: (row["filing_date"], row["form"], row["accession_number"] or ""), reverse=True)
    return {
        "schema": CATALOG_SCHEMA, "identifier": identifier, "as_of": cutoff,
        "forms": list(forms), "filings": normalized,
        "unknown_fields": [] if normalized else [{"field": "filings", "reason_code": "no_data"}],
        "provider": "sec_edgar_via_edgartools", "sec_precedence_enforced": True, "llm_calls": 0,
    }


class FilingCatalogClient(Protocol):
    def filing_catalog(self, identifier: str, forms: tuple[str, ...]) -> list[dict[str, Any]]: ...


class EdgarToolsClient:
    def __init__(self, identity: str | None = None) -> None:
        self.identity = str(identity or os.getenv("SEC_USER_AGENT") or "").strip()

    def filing_catalog(self, identifier: str, forms: tuple[str, ...]) -> list[dict[str, Any]]:
        if not self.identity:
            raise RuntimeError("SEC_USER_AGENT is required for EdgarTools")
        try:
            from edgar import Company, set_identity
        except ImportError as exc:
            raise RuntimeError(
                "EdgarTools extension is not installed; install requirements-extensions.txt first."
            ) from exc
        set_identity(self.identity)
        frame = Company(identifier).get_filings(form=list(forms)).to_pandas()
        return list(frame.to_dict(orient="records"))


def publish_filing_catalog(
    *, identifier: str, subject_id: str, run_id: str, as_of: str,
    db_path: Path = DEFAULT_CONTROL_PLANE_DB, client: FilingCatalogClient | None = None,
    forms: tuple[str, ...] = DEFAULT_FORMS,
) -> dict[str, Any]:
    client = client or EdgarToolsClient()
    catalog = normalize_filing_catalog(
        client.filing_catalog(identifier, forms), identifier=identifier, as_of=as_of, forms=forms,
    )
    db = connect(db_path)
    try:
        source_ref = f"edgar://company/{identifier}/filings?as_of={catalog['as_of']}"
        observation, _ = research_control_plane.record_observation(
            db, subject_id=subject_id, capability="sec.filing_catalog",
            provider="sec_edgar_via_edgartools", source_ref=source_ref,
            observed_at=catalog["as_of"] + "T23:59:59+00:00",
            as_of=catalog["as_of"], run_id=run_id,
            status="available" if catalog["filings"] else "missing",
            reason_code=None if catalog["filings"] else "no_data", payload=catalog,
        )
        inputs = [("observation", observation["observation_id"])]
        fingerprint = research_control_plane.input_fingerprint(
            stage="sec_filing_catalog", subject_id=subject_id, inputs=inputs,
            config={"forms": list(forms), "as_of": catalog["as_of"]},
            implementation_version="edgartools_evidence_adapter.v1",
        )
        checkpoint = research_control_plane.acquire_checkpoint(
            db, run_id=run_id, stage="sec_filing_catalog", subject_id=subject_id,
            fingerprint=fingerprint, details={"identifier": identifier},
        )
        if checkpoint["status"] == "reused":
            return {
                "status": "reused", "observation_id": observation["observation_id"],
                "control_packet_id": checkpoint["output_packet_id"],
                "checkpoint_id": checkpoint["checkpoint_id"],
            }
        quality = "ready" if catalog["filings"] else "evidence_gap"
        if not catalog["filings"]:
            research_control_plane.record_evidence_gap(
                db, subject_id=subject_id, run_id=run_id, field="sec_filings",
                reason_code="no_data", owner="edgartools_evidence_adapter",
                source_event_id=observation["observation_id"], payload={"identifier": identifier},
            )
        packet, created = research_control_plane.build_packet(
            db, kind="sec_filing_catalog", subject_id=subject_id, run_id=run_id,
            producer_name="edgartools_evidence_adapter", producer_version="v1",
            as_of=catalog["as_of"], quality_state=quality, inputs=inputs, payload=catalog,
        )
        completed = research_control_plane.complete_checkpoint(db, checkpoint["checkpoint_id"], packet)
        return {
            "status": completed["status"], "observation_id": observation["observation_id"],
            "control_packet_id": packet["packet_id"], "checkpoint_id": completed["checkpoint_id"],
            "created": created, "filing_count": len(catalog["filings"]),
        }
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identifier", required=True); parser.add_argument("--subject-id", required=True)
    parser.add_argument("--run-id", required=True); parser.add_argument("--as-of", required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_CONTROL_PLANE_DB)
    args = parser.parse_args(argv)
    result = publish_filing_catalog(
        identifier=args.identifier, subject_id=args.subject_id, run_id=args.run_id,
        as_of=args.as_of, db_path=args.db,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
