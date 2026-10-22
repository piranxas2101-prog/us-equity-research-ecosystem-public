from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import unknown_data


ARTIFACT_ROWS = {
    "babis": "shortlist",
    "giannis": "results",
    "petros": "results",
}
AVOIDABLE_REASONS = {
    "insufficient_source_detail", "provider_no_coverage", "temporarily_unavailable",
    "stale", "symbol_mapping_error", "parse_or_schema_error", "contract_mismatch",
    "unnecessary_requirement",
}


def load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def artifact_rows(payload: dict[str, Any], agent: str) -> list[dict[str, Any]]:
    values = payload.get(ARTIFACT_ROWS.get(agent, "results"), [])
    return [row for row in values if isinstance(row, dict)] if isinstance(values, list) else []


def literal_unknown_fields(row: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    for field, value in row.items():
        if isinstance(value, str) and value.strip().lower() in unknown_data.UNKNOWN_STRINGS:
            fields.append(str(field))
    return fields


def collect_artifact_events(
    path: Path,
    agent: str,
    contract: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = load(path)
    run_id = str(payload.get("run_id") or "historical_unknown_run")
    rows = artifact_rows(payload, agent)
    records: list[dict[str, Any]] = []
    declared = contract["agents"][agent]["fields"]
    for row in rows:
        events = unknown_data.events_for_row(agent, row, run_id=run_id, contract=contract)
        known_fields = {event["field"] for event in events}
        for field in literal_unknown_fields(row):
            if field in known_fields:
                continue
            spec = declared.get(field) or {
                "requirement": "diagnostic_only",
                "provider_route": ["upstream_contract"],
                "decision_impact": "diagnostic_visibility",
                "on_missing": "reason_code_required",
            }
            reason = unknown_data.infer_reason(agent, field, row, spec)
            events.append(unknown_data.make_event(agent, field, row, spec, reason, contract, run_id=run_id))
        for event in events:
            records.append({
                "artifact": str(path),
                "agent": agent,
                "run_id": run_id,
                "ticker": event["ticker"],
                "field": event["field"],
                "reason_code": event["reason_code"],
                "requirement": event["requirement"],
                "provider": event["provider"],
                "retryable": event["retryable"],
                "coverage_included": event["coverage_included"],
                "penalty_weight": event["penalty_weight"],
                "decision_impact": event["decision_impact"],
                "default_action": event["default_action"],
                "avoidable": event["reason_code"] in AVOIDABLE_REASONS,
                "unknown_event_id": event["unknown_event_id"],
            })
    operational_errors = payload.get("errors") or []
    return records, {
        "agent": agent,
        "artifact": str(path),
        "run_id": run_id,
        "row_count": len(rows),
        "operational_error_count": len(operational_errors) if isinstance(operational_errors, list) else 0,
    }


def build_baseline(artifacts: list[tuple[str, Path]], contract: dict[str, Any]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    artifact_summaries: list[dict[str, Any]] = []
    for agent, path in artifacts:
        found, summary = collect_artifact_events(path, agent, contract)
        records.extend(found)
        artifact_summaries.append(summary)

    by_field = Counter(record["field"] for record in records)
    by_reason = Counter(record["reason_code"] for record in records)
    by_agent = Counter(record["agent"] for record in records)
    by_provider = Counter(str(record["provider"] or "unassigned") for record in records)
    impact = Counter(record["decision_impact"] for record in records if record["coverage_included"])
    avoidable_total = sum(1 for record in records if record["avoidable"])
    cumulative = 0
    pareto = []
    for field, count in by_field.most_common():
        cumulative += count
        pareto.append({
            "field": field,
            "count": count,
            "pct_of_all": round(count / len(records) * 100.0, 2) if records else 0.0,
            "cumulative_pct": round(cumulative / len(records) * 100.0, 2) if records else 0.0,
        })
    return {
        "schema": "ecosystem.unknown_baseline.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "contract_version": contract["version"],
        "contract_sha256": unknown_data.digest(contract),
        "artifact_summaries": artifact_summaries,
        "event_count": len(records),
        "avoidable_event_count": avoidable_total,
        "avoidable_pct": round(avoidable_total / len(records) * 100.0, 2) if records else 0.0,
        "coverage_affecting_count": sum(1 for record in records if record["coverage_included"]),
        "not_applicable_count": by_reason.get("not_applicable", 0),
        "operational_error_count": sum(item["operational_error_count"] for item in artifact_summaries),
        "by_agent": dict(by_agent.most_common()),
        "by_reason": dict(by_reason.most_common()),
        "by_field": dict(by_field.most_common()),
        "by_provider": dict(by_provider.most_common()),
        "coverage_decision_impacts": dict(impact.most_common()),
        "field_pareto": pareto,
        "records": records,
        "interpretation": "Historical artifacts were not rewritten. Events were reconstructed under the v1 reason taxonomy to establish a frozen baseline.",
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def render_baseline(payload: dict[str, Any]) -> str:
    lines = [
        "# UNKNOWN Baseline And Pareto Report", "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Reason-coded events: **{payload['event_count']}**",
        f"- Avoidable/recoverable candidates: **{payload['avoidable_event_count']} ({payload['avoidable_pct']:.2f}%)**",
        f"- Coverage-affecting events: **{payload['coverage_affecting_count']}**",
        f"- Legitimate `not_applicable`: **{payload['not_applicable_count']}**",
        f"- Operational errors kept separate: **{payload['operational_error_count']}**", "",
        "## By Agent", "", "| Agent | Events |", "|---|---:|",
    ]
    lines.extend(f"| {agent.title()} | {count} |" for agent, count in payload["by_agent"].items())
    lines.extend(["", "## By Reason", "", "| Reason | Events |", "|---|---:|"])
    lines.extend(f"| `{reason}` | {count} |" for reason, count in payload["by_reason"].items())
    lines.extend(["", "## Field Pareto", "", "| Field | Count | % | Cumulative % |", "|---|---:|---:|---:|"])
    for row in payload["field_pareto"]:
        lines.append(f"| `{row['field']}` | {row['count']} | {row['pct_of_all']:.2f} | {row['cumulative_pct']:.2f} |")
        if row["cumulative_pct"] >= 85:
            break
    lines.extend(["", "## Interpretation", "", payload["interpretation"], "", "`UNKNOWN` remains valid. The target is to reduce avoidable causes and correctly exclude not-applicable fields, not to manufacture complete-looking data.", ""])
    return "\n".join(lines)


def field_matrix(contract: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for agent, agent_spec in contract["agents"].items():
        for field, spec in agent_spec["fields"].items():
            rows.append({
                "agent": agent,
                "field": field,
                "requirement": spec["requirement"],
                "applicability": spec.get("applicability", "general"),
                "provider_route": " -> ".join(spec["provider_route"]),
                "decision_impact": spec["decision_impact"],
                "on_missing": spec["on_missing"],
                "must_be_true": bool(spec.get("must_be_true")),
            })
    return rows


def render_field_matrix(rows: list[dict[str, Any]]) -> str:
    lines = ["# Agent-By-Field UNKNOWN Contract", "", "| Agent | Field | Requirement | Applicability | Provider route | Decision impact | Missing action |", "|---|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['agent']} | `{row['field']}` | {row['requirement']} | {row['applicability']} | {row['provider_route']} | {row['decision_impact']} | {row['on_missing']} |")
    lines.append("")
    return "\n".join(lines)


def provider_matrix(contract: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    observed = status.get("sources") or {}
    rows = []
    for provider, spec in contract["providers"].items():
        state = observed.get(provider) or {}
        rows.append({
            "provider": provider,
            "authority": spec["authority"],
            "capabilities": list(spec["capabilities"]),
            "fallbacks": list(spec["fallbacks"]),
            "observed_status": state.get("status", "not_probed"),
            "status_as_of": status.get("retrieved_at"),
        })
    return {"schema": "ecosystem.provider_capability_matrix.v1", "generated_at": datetime.now(timezone.utc).isoformat(), "credentials_exposed": False, "rows": rows}


def render_provider_matrix(payload: dict[str, Any]) -> str:
    lines = ["# Provider Capability And Fallback Matrix", "", f"- Status snapshot: `{payload['rows'][0]['status_as_of'] if payload['rows'] else 'not_available'}`", "- Credentials exposed: **No**", "", "| Provider | Authority | Capabilities | Fallbacks | Status |", "|---|---|---|---|---|"]
    for row in payload["rows"]:
        lines.append(f"| `{row['provider']}` | {row['authority']} | {', '.join(row['capabilities'])} | {', '.join(row['fallbacks']) or 'none'} | {row['observed_status']} |")
    lines.extend(["", "SEC remains authoritative for issuer claims. FMP and OpenBB/yfinance are supplementary and may not override conflicting SEC evidence. Benzinga remains disabled while authentication fails.", ""])
    return "\n".join(lines)


def render_backlog(backlog: dict[str, Any]) -> str:
    lines = ["# Ranked UNKNOWN Upgrade Backlog", "", "| Priority | ID | Status | Upgrade | Measurement | Risk |", "|---|---|---|---|---|---|"]
    order = {"must_fix": 0, "pilot": 1, "defer": 2, "reject": 3}
    for item in sorted(backlog["items"], key=lambda value: (order.get(value["priority"], 99), value["id"])):
        lines.append(f"| {item['priority']} | `{item['id']}` | {item.get('status', 'planned')} | {item['title']} | {item['measurement']} | {item['risk']} |")
    lines.append("")
    return "\n".join(lines)


def run(
    artifacts: list[tuple[str, Path]],
    output_dir: Path,
    contract_path: Path,
    provider_status_path: Path,
    backlog_path: Path,
) -> dict[str, Any]:
    contract = unknown_data.load_contract(contract_path)
    errors = unknown_data.validate_contract(contract)
    if errors:
        raise ValueError("; ".join(errors))
    for _, path in artifacts:
        if not path.exists():
            raise FileNotFoundError(path)
    output_dir.mkdir(parents=True, exist_ok=True)
    baseline = build_baseline(artifacts, contract)
    fields = field_matrix(contract)
    providers = provider_matrix(contract, load(provider_status_path))
    backlog = load(backlog_path)
    (output_dir / "unknown_baseline_latest.json").write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "unknown_baseline_latest.md").write_text(render_baseline(baseline), encoding="utf-8")
    write_csv(output_dir / "unknown_events_latest.csv", baseline["records"])
    write_csv(output_dir / "agent_field_matrix_latest.csv", fields)
    (output_dir / "agent_field_matrix_latest.md").write_text(render_field_matrix(fields), encoding="utf-8")
    (output_dir / "provider_capability_matrix_latest.json").write_text(json.dumps(providers, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "provider_capability_matrix_latest.md").write_text(render_provider_matrix(providers), encoding="utf-8")
    (output_dir / "unknown_upgrade_backlog_latest.json").write_text(json.dumps(backlog, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "unknown_upgrade_backlog_latest.md").write_text(render_backlog(backlog), encoding="utf-8")
    return baseline


def main() -> int:
    parser = argparse.ArgumentParser(description="Build UNKNOWN baseline, field matrix, provider matrix and upgrade backlog.")
    parser.add_argument("--babis", default="output/babis_shortlist_latest.json")
    parser.add_argument("--giannis", default="output/giannis_rule_based_latest.json")
    parser.add_argument("--petros", default="output/petros_review_latest.json")
    parser.add_argument("--contract", default="unknown_data_contract.json")
    parser.add_argument("--provider-status", default="provider_status_snapshot_20260901.json")
    parser.add_argument("--backlog", default="unknown_upgrade_backlog.json")
    parser.add_argument("--output-dir", default="output/unknown_analysis")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent

    def resolve(value):
        return Path(value) if Path(value).is_absolute() else root / value

    baseline = run(
        [("babis", resolve(args.babis)), ("giannis", resolve(args.giannis)), ("petros", resolve(args.petros))],
        resolve(args.output_dir), resolve(args.contract), resolve(args.provider_status), resolve(args.backlog),
    )
    print(json.dumps({key: baseline[key] for key in ("event_count", "avoidable_event_count", "avoidable_pct", "coverage_affecting_count", "operational_error_count", "by_agent", "by_reason")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
