"""Controlled UNKNOWN follow-up benchmark runner.

This is an offline/shadow tool.  It does not scan the market, call a broker, or
modify historical agent artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import catalyst_content_review
import unknown_data


ROOT = Path(__file__).resolve().parent
RETRYABLE = {"rate_limited", "timeout", "temporarily_unavailable", "server_error"}
FAILURE_CASES = {
    "401": {"sequence": ["auth_failed", "ok"], "expected_attempts": 1, "action": "disable_and_review_credentials"},
    "403": {"sequence": ["forbidden_or_plan", "ok"], "expected_attempts": 1, "action": "review_entitlement"},
    "429": {"sequence": ["rate_limited", "rate_limited", "ok"], "expected_attempts": 3, "action": "bounded_backoff"},
    "timeout": {"sequence": ["timeout", "ok"], "expected_attempts": 2, "action": "bounded_backoff"},
    "empty_response": {"sequence": ["empty_response", "ok"], "expected_attempts": 1, "action": "reason_code_no_coverage"},
    "stale_response": {"sequence": ["stale", "ok"], "expected_attempts": 1, "action": "retain_stale_and_reduce_confidence"},
    "malformed_json": {"sequence": ["invalid_json", "ok"], "expected_attempts": 1, "action": "schema_incident"},
    "schema_error": {"sequence": ["schema_error", "ok"], "expected_attempts": 1, "action": "schema_incident"},
    "symbol_cik_mismatch": {"sequence": ["symbol_mapping_error", "ok"], "expected_attempts": 1, "action": "quarantine_identity"},
    "source_conflict": {"sequence": ["conflicting_sources", "ok"], "expected_attempts": 1, "action": "preserve_both_and_manual_review"},
}


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def run_failure_sequence(sequence: list[str], max_attempts: int = 3) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for index, status in enumerate(sequence[:max_attempts], start=1):
        retryable = status in RETRYABLE
        attempts.append(
            {
                "attempt": index,
                "status": status,
                "retryable": retryable,
                "backoff_slot": index - 1 if retryable else None,
            }
        )
        if status == "ok" or not retryable:
            break
    final = attempts[-1]["status"] if attempts else "no_attempt"
    return {
        "attempt_count": len(attempts),
        "attempts": attempts,
        "final_status": final,
        "recovered": final == "ok",
        "exhausted": final in RETRYABLE and len(attempts) >= max_attempts,
    }


def failure_injection_report() -> dict[str, Any]:
    rows = []
    for case_id, spec in FAILURE_CASES.items():
        observed = run_failure_sequence(spec["sequence"])
        passed = observed["attempt_count"] == spec["expected_attempts"]
        rows.append(
            {
                "case_id": case_id,
                "expected_attempts": spec["expected_attempts"],
                "required_action": spec["action"],
                "observed": observed,
                "pass": passed,
            }
        )
    return {
        "schema": "ecosystem.provider_failure_injection.v1",
        "status": "pass" if all(row["pass"] for row in rows) else "fail",
        "case_count": len(rows),
        "passed": sum(1 for row in rows if row["pass"]),
        "rows": rows,
        "live_requests_made": False,
    }


def _artifact_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("shortlist", "results", "rows"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def shadow_grade(path: Path, agent: str, contract: dict[str, Any]) -> dict[str, Any]:
    payload = load(path)
    rows = _artifact_rows(payload)
    expected_total = 0
    observed_total = 0
    valid_reason = 0
    valid_event_id = 0
    source_as_of = 0
    disagreements: list[dict[str, Any]] = []
    valid_codes = set(contract["reason_codes"])
    for row in rows:
        expected = unknown_data.events_for_row(
            agent, row, run_id=payload.get("run_id"), contract=contract
        )
        observed = [event for event in (row.get("unknown_events") or []) if isinstance(event, dict)]
        observed_by_field = {str(event.get("field")): event for event in observed}
        expected_total += len(expected)
        for event in expected:
            actual = observed_by_field.get(event["field"])
            if actual is None:
                disagreements.append(
                    {
                        "ticker": row.get("ticker") or row.get("symbol"),
                        "field": event["field"],
                        "issue": "missing_observed_unknown_event",
                        "reconstructed_reason": event["reason_code"],
                    }
                )
                continue
            observed_total += 1
            valid_reason += int(actual.get("reason_code") in valid_codes)
            valid_event_id += int(str(actual.get("unknown_event_id") or "").startswith("unknown_"))
            source_as_of += int(bool(actual.get("source_as_of")))
            if actual.get("reason_code") != event.get("reason_code"):
                disagreements.append(
                    {
                        "ticker": row.get("ticker") or row.get("symbol"),
                        "field": event["field"],
                        "issue": "reason_disagreement",
                        "observed_reason": actual.get("reason_code"),
                        "reconstructed_reason": event["reason_code"],
                    }
                )
    def pct(count, total, empty=None):
        return round(count / total * 100.0, 2) if total else empty
    conformance = pct(observed_total, expected_total, 100.0)
    return {
        "agent": agent,
        "artifact": str(path),
        "artifact_sha256": sha256_file(path),
        "run_id": payload.get("run_id"),
        "row_count": len(rows),
        "expected_missing_events": expected_total,
        "observed_matching_events": observed_total,
        "contract_observation_status": "legacy_pre_contract_artifact"
        if expected_total and not observed_total
        else "observable",
        "observable_contract_conformance_pct": conformance,
        "valid_reason_code_pct": pct(valid_reason, observed_total),
        "immutable_event_id_pct": pct(valid_event_id, observed_total),
        "source_as_of_pct": pct(source_as_of, observed_total),
        "disagreement_count": len(disagreements),
        "disagreements": disagreements[:100],
    }


def build_frozen_manifest(baseline_path: Path, source_paths: list[Path]) -> dict[str, Any]:
    baseline = load(baseline_path)
    records = baseline.get("records") or []
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    high_value_fields = {
        "forward_pe", "revenue_growth", "earnings_growth", "gross_margin",
        "profit_margin", "roe", "debt_to_equity", "current_ratio",
        "catalyst_category_id", "catalyst_direction", "cash", "debt",
        "fundamental_coverage", "priced_in_status",
    }
    for record in records:
        key = (str(record.get("field")), str(record.get("reason_code")))
        if record.get("field") in high_value_fields and key not in seen:
            selected.append(
                {
                    "case_type": "historical_unknown",
                    "agent": record.get("agent"),
                    "run_id": record.get("run_id"),
                    "ticker": record.get("ticker"),
                    "field": record.get("field"),
                    "reason_code": record.get("reason_code"),
                    "provider": record.get("provider"),
                    "unknown_event_id": record.get("unknown_event_id"),
                }
            )
            seen.add(key)
    scenarios = [
        {"case_type": "synthetic_guard", "scenario": "profitable_domestic_issuer", "expected": "facts_selected_point_in_time"},
        {"case_type": "synthetic_guard", "scenario": "loss_making_forward_pe", "expected": "not_applicable"},
        {"case_type": "synthetic_guard", "scenario": "financial_company_ratios", "expected": "not_applicable_where_contract_declares"},
        {"case_type": "synthetic_guard", "scenario": "foreign_issuer_ifrs", "expected": "ifrs_full_then_reason_coded_unknown"},
        {"case_type": "synthetic_guard", "scenario": "missing_cik", "expected": "quarantine_no_guess"},
        {"case_type": "synthetic_guard", "scenario": "conflicting_sec_fmp", "expected": "sec_wins_conflict_visible"},
        {"case_type": "synthetic_guard", "scenario": "stale_filing", "expected": "stale_reason_no_interpolation"},
        {"case_type": "synthetic_guard", "scenario": "genuinely_undisclosed", "expected": "unknown_is_valid"},
    ]
    body = {
        "schema": "ecosystem.unknown_frozen_benchmark_manifest.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline_generated_at": baseline.get("generated_at"),
        "baseline_contract_sha256": baseline.get("contract_sha256"),
        "historical_cases": selected,
        "guard_scenarios": scenarios,
        "sources": [
            {"path": str(path), "sha256": sha256_file(path)}
            for path in source_paths
            if path.is_file()
        ],
        "historical_artifacts_modified": False,
    }
    return {**body, "manifest_sha256": unknown_data.digest(body)}


def render_failure(payload: dict[str, Any]) -> str:
    lines = [
        "# Provider Failure-Injection Report", "",
        f"- Status: **{payload['status'].upper()}**",
        f"- Cases: **{payload['passed']}/{payload['case_count']}**",
        "- Live requests: **No**", "",
        "| Case | Attempts | Final | Action | Pass |", "|---|---:|---|---|---|",
    ]
    for row in payload["rows"]:
        lines.append(
            f"| `{row['case_id']}` | {row['observed']['attempt_count']} | "
            f"{row['observed']['final_status']} | {row['required_action']} | {row['pass']} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_shadow(payload: dict[str, Any]) -> str:
    def metric(value):
        return "N/A" if value is None else f"{value:.2f}%"
    lines = [
        "# Shadow Agent UNKNOWN Conformance", "",
        "Historical reports were read-only and were not rewritten.", "",
        "| Agent | Rows | Expected UNKNOWN | Observable conformance | Valid reasons | Source as-of | Disagreements |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["agents"]:
        lines.append(
            f"| {row['agent'].title()} | {row['row_count']} | {row['expected_missing_events']} | "
            f"{metric(row['observable_contract_conformance_pct'])} | {metric(row['valid_reason_code_pct'])} | "
            f"{metric(row['source_as_of_pct'])} | {row['disagreement_count']} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_catalyst(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Catalyst Resolver Benchmark", "",
            f"- Fixtures: **{payload['fixture_count']}**",
            f"- Category accuracy: **{payload['category_accuracy_pct']:.2f}%**",
            f"- False direction: **{payload['false_direction_pct']:.2f}%**",
            f"- Unresolved: **{payload['unresolved_pct']:.2f}%**",
            "- Execution authority: **No**", "",
            "Unresolved evidence remains UNKNOWN and is treated as a correct safe output.", "",
        ]
    )


def run(output_dir: Path) -> dict[str, Any]:
    contract = unknown_data.load_contract(ROOT / "unknown_data_contract.json")
    baseline_path = ROOT / "output" / "unknown_analysis" / "unknown_baseline_latest.json"
    artifacts = {
        "babis": ROOT / "output" / "babis_shortlist_latest.json",
        "giannis": ROOT / "output" / "giannis_rule_based_latest.json",
        "petros": ROOT / "output" / "petros_review_latest.json",
    }
    fixture_paths = [
        ROOT / "tests" / "fixtures" / "sec_companyfacts_point_in_time.json",
        ROOT / "tests" / "fixtures" / "catalyst_resolver_labelled.json",
    ]
    manifest = build_frozen_manifest(
        baseline_path, [baseline_path, *artifacts.values(), *fixture_paths]
    )
    failure = failure_injection_report()
    fixtures = load(fixture_paths[1])
    catalyst = catalyst_content_review.evaluate_labelled_fixture(fixtures)
    agent_rows = [shadow_grade(path, agent, contract) for agent, path in artifacts.items()]
    shadow = {
        "schema": "ecosystem.unknown_shadow_agent_conformance.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "agents": agent_rows,
        "historical_artifacts_modified": False,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    dump(output_dir / "frozen_benchmark_manifest.json", manifest)
    dump(output_dir / "failure_injection_report.json", failure)
    (output_dir / "failure_injection_report.md").write_text(render_failure(failure), encoding="utf-8")
    dump(output_dir / "catalyst_resolver_benchmark.json", catalyst)
    (output_dir / "catalyst_resolver_benchmark.md").write_text(render_catalyst(catalyst), encoding="utf-8")
    dump(output_dir / "shadow_agent_conformance_report.json", shadow)
    (output_dir / "shadow_agent_conformance_report.md").write_text(render_shadow(shadow), encoding="utf-8")
    summary = {
        "schema": "ecosystem.unknown_followup_summary.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": manifest["manifest_sha256"],
        "failure_injection_status": failure["status"],
        "catalyst_accuracy_pct": catalyst["category_accuracy_pct"],
        "catalyst_false_direction_pct": catalyst["false_direction_pct"],
        "shadow_conformance": {
            row["agent"]: row["observable_contract_conformance_pct"] for row in agent_rows
        },
        "trading_or_full_pipeline_started": False,
    }
    dump(output_dir / "unknown_followup_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run controlled UNKNOWN follow-up benchmarks")
    parser.add_argument(
        "--output-dir", default="output/unknown_analysis/followup_20260902"
    )
    args = parser.parse_args()
    target = Path(args.output_dir)
    if not target.is_absolute():
        target = ROOT / target
    run(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
