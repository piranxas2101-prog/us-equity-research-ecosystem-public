"""Build safe, append-only coordination events for external cockpit sync.

The trading pipeline must not contain Slack or Notion credentials.  This
module therefore writes a small, immutable outbox record at the end of a
controlled run.  A separate, read-only Codex automation can publish the
record through the already-authorized connectors.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "ecosystem.integration_outbox.v1"
DEFAULT_ARTIFACTS = {
    "babis": "output/babis_shortlist_latest.json",
    "giannis": "output/giannis_rule_based_latest.json",
    "yankos": "output/yankos_strategy_latest.json",
    "periklis": "output/periklis_strategy_latest.json",
    "petros": "output/petros_review_latest.json",
    "team": "output/team_decision_latest.json",
    "trader": "output/trader_paper_trading_run_latest.json",
}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _candidate_summary(payload: dict[str, Any]) -> dict[str, int]:
    candidates = [
        row
        for row in _items(payload.get("results"))
        if str(row.get("signal_verdict", "")).lower() == "candidate"
    ]
    tickers = {
        str(row.get("ticker", "")).upper()
        for row in candidates
        if row.get("ticker")
    }
    return {"candidate_rows": len(candidates), "candidate_symbols": len(tickers)}


def _petros_candidate_count(payload: dict[str, Any]) -> int:
    return sum(
        1
        for row in _items(payload.get("results"))
        if str(row.get("petros_verdict", "")).lower() == "candidate"
    )


def _artifact_run_ids(payloads: dict[str, dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for agent, payload in payloads.items():
        run_id = payload.get("run_id")
        if run_id:
            result[agent] = str(run_id)
    return result


def _relative(project_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _handoffs(
    *,
    run_id: str,
    project_root: Path,
    paths: dict[str, Path],
    artifact_run_ids: dict[str, str],
) -> list[dict[str, Any]]:
    edges = [
        ("Babis", "Giannis", "babis"),
        ("Giannis", "Yankos", "giannis"),
        ("Giannis", "Periklis", "giannis"),
        ("Yankos", "Petros", "yankos"),
        ("Periklis", "Petros", "periklis"),
        ("Petros", "Koulis", "team"),
        ("Koulis", "Thymios", "team"),
        ("Thymios", "Miltos", "trader"),
    ]
    records: list[dict[str, Any]] = []
    for sender, receiver, artifact_key in edges:
        artifact = paths[artifact_key]
        observed_run_id = artifact_run_ids.get(artifact_key)
        exists = artifact.exists()
        records.append(
            {
                "from_agent": sender,
                "to_agent": receiver,
                "artifact": _relative(project_root, artifact),
                "status": "ready" if exists else "blocked",
                "contract_valid": bool(exists and observed_run_id == run_id),
                "blocker": None
                if exists and observed_run_id == run_id
                else "missing_artifact_or_run_id_mismatch",
            }
        )
    return records


def _digest_markdown(event: dict[str, Any]) -> str:
    metrics = event["metrics"]
    lines = [
        f"*Daily Research Digest* — `{event['run_id']}`",
        f"• Session: {event['session_date']}",
        (
            "• Coverage: "
            f"{metrics['coverage_pct']:.2f}% "
            f"({metrics['usable_symbols']}/{metrics['processed_symbols']}); "
            f"errors: {metrics['data_errors']}"
        ),
        (
            "• Candidates: "
            f"Yankos {metrics['yankos']['candidate_symbols']} symbols / "
            f"{metrics['yankos']['candidate_rows']} rows; "
            f"Periklis {metrics['periklis']['candidate_symbols']} symbols / "
            f"{metrics['periklis']['candidate_rows']} rows; "
            f"Petros {metrics['petros_candidates']}"
        ),
        (
            "• Team buckets: "
            f"focus now {metrics['focus_now']}, "
            f"review next {metrics['review_next']}, "
            f"selected {metrics['selected_companies']}"
        ),
        f"• Trader handoff: `{event['trader_handoff_status']}`",
        f"• Paper requested: {'yes' if event['paper_requested'] else 'no'}",
        f"• Contract valid: {'yes' if event['contract_valid'] else 'NO'}",
    ]
    if event["warnings"]:
        lines.append(f"• Warnings: {len(event['warnings'])}")
    return "\n".join(lines)


def build_event(
    *,
    project_root: Path,
    run_id: str,
    generated_at: str,
    trader_handoff_status: str,
    paper_requested: bool,
    warnings: Iterable[str] = (),
    summary_path: str | None = None,
) -> dict[str, Any]:
    paths = {
        key: project_root / relative_path
        for key, relative_path in DEFAULT_ARTIFACTS.items()
    }
    payloads = {key: _read_json(path) for key, path in paths.items()}
    run_ids = _artifact_run_ids(payloads)
    required_keys = ("babis", "giannis", "yankos", "periklis", "petros", "team")
    identity_valid = all(run_ids.get(key) == run_id for key in required_keys)

    babis = payloads["babis"]
    runtime = babis.get("runtime_summary")
    runtime = runtime if isinstance(runtime, dict) else {}
    processed = int(runtime.get("processed_symbols") or babis.get("universe_total") or 0)
    errors = len(_items(babis.get("errors")))
    usable = max(processed - errors, 0)
    coverage_pct = round((usable / processed * 100.0), 4) if processed else 0.0

    team = payloads["team"]
    handoffs = _handoffs(
        run_id=run_id,
        project_root=project_root,
        paths=paths,
        artifact_run_ids=run_ids,
    )
    contract_valid = identity_valid and all(row["contract_valid"] for row in handoffs[:-1])
    # The final paper handoff is optional for research-only runs.
    if paper_requested:
        contract_valid = contract_valid and handoffs[-1]["contract_valid"]

    event: dict[str, Any] = {
        "schema": SCHEMA,
        "event_type": "daily_research_digest",
        "run_id": run_id,
        "session_date": generated_at[:10],
        "generated_at": generated_at,
        "contract_valid": contract_valid,
        "artifact_run_ids": run_ids,
        "paper_requested": paper_requested,
        "trader_handoff_status": trader_handoff_status,
        "metrics": {
            "processed_symbols": processed,
            "usable_symbols": usable,
            "coverage_pct": coverage_pct,
            "data_errors": errors,
            "shortlist_total": int(babis.get("shortlist_total") or 0),
            "yankos": _candidate_summary(payloads["yankos"]),
            "periklis": _candidate_summary(payloads["periklis"]),
            "petros_candidates": _petros_candidate_count(payloads["petros"]),
            "focus_now": len(_items(team.get("focus_now"))),
            "review_next": len(_items(team.get("review_next"))),
            "selected_companies": len(_items(team.get("selected_companies"))),
        },
        "warnings": [str(item) for item in warnings if str(item).strip()],
        "handoffs": handoffs,
        "artifacts": {
            key: _relative(project_root, path)
            for key, path in paths.items()
            if path.exists()
        },
        "summary_artifact": summary_path,
        "routing": {
            "notion": {"run_log": True, "handoff_log": True},
            "slack": {
                "daily_channel": "daily-research",
                "alert_channel": "ecosystem-alerts",
            },
        },
    }
    event["summary_markdown"] = _digest_markdown(event)

    alert_reasons = list(event["warnings"])
    if not contract_valid:
        alert_reasons.append("agent_handoff_contract_invalid")
    if trader_handoff_status == "failed":
        alert_reasons.append("trader_handoff_failed")
    error_rate = (errors / processed) if processed else 0.0
    if error_rate >= 0.10:
        alert_reasons.append("data_error_rate_at_or_above_10_percent")
    event["alert"] = {
        "required": bool(alert_reasons),
        "severity": "critical"
        if (not contract_valid or trader_handoff_status == "failed")
        else "warning",
        "reasons": list(dict.fromkeys(alert_reasons)),
    }
    digest_basis = json.dumps(event, ensure_ascii=False, sort_keys=True).encode("utf-8")
    event["event_sha256"] = hashlib.sha256(digest_basis).hexdigest()
    return event


def write_once(path: Path, payload: dict[str, Any]) -> tuple[Path, bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path, False
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        temp_path = Path(temp_name)
        if temp_path.exists():
            temp_path.unlink()
    return path, True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--generated-at", default=datetime.now(timezone.utc).isoformat())
    parser.add_argument("--trader-handoff-status", default="unknown")
    parser.add_argument("--paper-requested", action="store_true")
    parser.add_argument("--warning", action="append", default=[])
    parser.add_argument("--summary-path")
    parser.add_argument("--outbox-dir", default="output/integration_outbox")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.project_root).resolve()
    event = build_event(
        project_root=root,
        run_id=args.run_id,
        generated_at=args.generated_at,
        trader_handoff_status=args.trader_handoff_status,
        paper_requested=bool(args.paper_requested),
        warnings=args.warning,
        summary_path=args.summary_path,
    )
    path = root / args.outbox_dir / f"{args.run_id}_daily_research_digest.json"
    path, created = write_once(path, event)
    state = "created" if created else "exists"
    print(f"[COORDINATION_OUTBOX] {state}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
