"""Optional Dagster projection of the local research packet graph.

Dagster is imported only when ``build_definitions`` is called.  The existing
pipeline and tests therefore remain usable without installing the extension.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from control_plane_store import DEFAULT_CONTROL_PLANE_DB, connect
from research_asset_graph import evaluate_packet, latest_packet


def _snapshot(kind: str, db_path: Path) -> dict[str, Any]:
    db = connect(db_path)
    try:
        packet = latest_packet(db, kind)
        if not packet:
            raise RuntimeError(f"No {kind} control packet is available")
        checks = evaluate_packet(db, packet, persist=True)
        return {
            "packet_id": packet["packet_id"], "kind": kind, "subject_id": packet["subject_id"],
            "run_id": packet["run_id"], "content_hash": packet["content_hash"],
            "quality_state": packet["quality_state"], "checks": checks,
        }
    finally:
        db.close()


def build_definitions(db_path: Path = DEFAULT_CONTROL_PLANE_DB):
    """Build four software-defined assets over existing immutable packets."""
    try:
        import dagster as dg
    except ImportError as exc:
        raise RuntimeError(
            "Dagster extension is not installed; install requirements-extensions.txt first."
        ) from exc

    def metadata(snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            "packet_id": snapshot["packet_id"], "subject_id": snapshot["subject_id"],
            "run_id": snapshot["run_id"], "content_hash": snapshot["content_hash"],
            "quality_state": snapshot["quality_state"],
            "failed_checks": sum(item["status"] == "failed" for item in snapshot["checks"]),
        }

    @dg.asset(group_name="research_control_plane", compute_kind="sqlite", code_version="v1")
    def company_profile(context):
        value = _snapshot("company_profile", db_path)
        context.add_output_metadata(metadata(value)); return value

    @dg.asset(group_name="research_control_plane", compute_kind="sqlite", code_version="v1")
    def financial_evidence(context, company_profile):
        value = _snapshot("financial_evidence", db_path)
        context.add_output_metadata(metadata(value)); return value

    @dg.asset(group_name="research_control_plane", compute_kind="sqlite", code_version="v2")
    def fundamental_analysis(context, financial_evidence):
        value = _snapshot("fundamental_analysis", db_path)
        context.add_output_metadata(metadata(value)); return value

    @dg.asset(group_name="research_control_plane", compute_kind="sqlite", code_version="v1")
    def fundamental_research_packet(context, fundamental_analysis):
        value = _snapshot("fundamental_research_packet", db_path)
        context.add_output_metadata(metadata(value)); return value

    def aggregate_check(kind: str):
        value = _snapshot(kind, db_path)
        failed = [item for item in value["checks"] if item["status"] == "failed"]
        blocking = [item for item in failed if item["severity"] == "error"]
        return dg.AssetCheckResult(
            passed=not blocking,
            metadata={
                "packet_id": value["packet_id"], "failed_checks": [item["check_name"] for item in failed],
                "blocking_checks": [item["check_name"] for item in blocking],
            },
        )

    @dg.asset_check(asset=company_profile, name="control_plane_contract")
    def company_profile_check(): return aggregate_check("company_profile")

    @dg.asset_check(asset=financial_evidence, name="control_plane_contract")
    def financial_evidence_check(): return aggregate_check("financial_evidence")

    @dg.asset_check(asset=fundamental_analysis, name="control_plane_contract")
    def fundamental_analysis_check(): return aggregate_check("fundamental_analysis")

    @dg.asset_check(asset=fundamental_research_packet, name="control_plane_contract")
    def fundamental_research_packet_check(): return aggregate_check("fundamental_research_packet")

    return dg.Definitions(
        assets=[company_profile, financial_evidence, fundamental_analysis, fundamental_research_packet],
        asset_checks=[company_profile_check, financial_evidence_check,
                      fundamental_analysis_check, fundamental_research_packet_check],
    )


try:
    import dagster as _dagster  # noqa: F401
except ImportError:
    defs = None
else:
    # Dagster CLI discovers this object when the optional extension is installed.
    defs = build_definitions()
