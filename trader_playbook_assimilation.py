from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import playbook_framework


EXAM_SCHEMA = "ecosystem.trader_playbook_exam.v1"
RESPONSE_SCHEMA = "ecosystem.trader_playbook_exam_response.v1"
REPORT_SCHEMA = "ecosystem.trader_playbook_assimilation_report.v1"
TRADERS = {"yankos": "yankos_playbooks.json", "periklis": "periklis_playbooks.json"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def load_exam(path: Path) -> dict[str, Any]:
    exam = load_json(path)
    if exam.get("schema") != EXAM_SCHEMA:
        raise ValueError(f"Unsupported playbook exam schema: {exam.get('schema')!r}")
    weights = exam.get("weights") or {}
    if round(sum(float(value) for value in weights.values()), 6) != 100:
        raise ValueError("Exam weights must sum to 100.")
    if not exam.get("cases"):
        raise ValueError("Playbook exam has no cases.")
    return exam


def portfolio_assignment(portfolio: list[dict[str, Any]], playbook_id: str) -> dict[str, Any]:
    for item in portfolio:
        if str(item.get("playbook_id")) == playbook_id:
            return item
    raise ValueError(f"Portfolio does not contain {playbook_id}")


def confirmation_state(playbook: dict[str, Any], row: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    required = [str(item) for item in playbook.get("entry_confirmation", [])]
    if playbook.get("sides") == ["flat"] or required == ["none; this playbook explicitly recommends abstention"]:
        return "not_applicable", [], []
    observed = [str(item) for item in row.get("confirmed_entry_conditions", [])]
    missing = [item for item in required if item not in observed]
    return ("confirmed" if not missing else "unconfirmed"), observed, missing


def select_primary(
    case: dict[str, Any], catalog: dict[str, Any], portfolio: list[dict[str, Any]], perspectives: list[dict[str, Any]]
) -> dict[str, Any]:
    forced = str(case.get("forced_playbook_id") or "")
    if forced:
        return next(item for item in perspectives if item["playbook_id"] == forced)
    candidate_ids = {str(item) for item in case.get("candidate_playbooks", [])}
    candidates = [item for item in perspectives if not candidate_ids or item["playbook_id"] in candidate_ids]
    if not candidates:
        raise ValueError(f"No candidates for case {case.get('case_id')}")
    readiness_rank = {"strong_match": 4, "watch_match": 3, "needs_data": 2, "blocked": 1, "not_applicable": 0}
    return max(
        candidates,
        key=lambda item: (
            readiness_rank.get(str(item.get("readiness")), 0),
            float(item.get("agent_priority_score") or 0),
        ),
    )


def select_counterview(primary: dict[str, Any], perspectives: list[dict[str, Any]]) -> dict[str, Any] | None:
    primary_side = str(primary.get("preferred_side"))
    if primary_side == "flat":
        eligible = [item for item in perspectives if item.get("preferred_side") in {"long", "short"}]
    else:
        opposite = "short" if primary_side == "long" else "long"
        # An explicit opposing thesis is more informative than a generic FLAT
        # guard.  FLAT is only the fallback when no opposing view exists.
        eligible = [item for item in perspectives if item.get("preferred_side") == opposite]
        if not eligible:
            eligible = [item for item in perspectives if item.get("preferred_side") == "flat"]
    eligible = [item for item in eligible if item.get("playbook_id") != primary.get("playbook_id")]
    if not eligible:
        return None
    return max(eligible, key=lambda item: float(item.get("agent_priority_score") or 0))


def decide_action(
    case: dict[str, Any], primary: dict[str, Any], state: str, missing_confirmation: list[str]
) -> str:
    row = case["row"]
    if case.get("lifecycle_case") and (
        row.get("triggered_invalidators") or row.get("time_stop_reached")
    ):
        return "manage_existing_exit"
    if case.get("must_abstain"):
        return "no_trade"
    if primary.get("data_quality_blockers"):
        if "shortability" in primary.get("unresolved_short_gates", []) and row.get("shortable") is False:
            return "no_trade"
        return "needs_data"
    if primary.get("missing_required_data"):
        return "needs_data"
    if state == "unconfirmed" or missing_confirmation:
        return "watch"
    status = str(primary.get("portfolio_status"))
    if status == "active":
        return "eligible_for_specialized_evaluator"
    return "perspective_only"


def specialized_observation(
    agent: str, playbooks: list[dict[str, Any]], playbook_id: str, row: dict[str, Any]
) -> dict[str, Any] | None:
    specialized = next((item for item in playbooks if str(item.get("name")) == playbook_id), None)
    if specialized is None:
        return None
    prepared = dict(row)
    prepared.setdefault(
        "technical",
        {
            key: row.get(key) for key in (
                "price_position_20d_pct", "price_position_60d_pct", "distance_from_20d_low_pct",
                "distance_from_60d_low_pct", "technical_timing", "candlestick_patterns",
                "candlestick_bias", "candlestick_score",
            )
        },
    )
    if agent == "yankos":
        import yankos_strategy_agent as agent_module
    else:
        import periklis_strategy_agent as agent_module
    result = agent_module.evaluate_playbook(specialized, prepared)
    return {
        "playbook_id": result.get("playbook_name"),
        "direction": result.get("trade_direction"),
        "verdict": result.get("signal_verdict"),
        "signal_score": result.get("signal_score"),
        "long_score": result.get("long_signal_score"),
        "short_score": result.get("short_signal_score"),
    }


def build_response(
    agent: str,
    case: dict[str, Any],
    catalog: dict[str, Any],
    portfolio: list[dict[str, Any]],
    specialized_playbooks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    perspectives = playbook_framework.evaluate_portfolio(catalog, portfolio, [case["row"]], agent)
    primary = select_primary(case, catalog, portfolio, perspectives)
    playbook = playbook_framework.catalog_by_id(catalog)[primary["playbook_id"]]
    state, observed, missing_confirmation = confirmation_state(playbook, case["row"])
    counter = select_counterview(primary, perspectives)
    missing_data = sorted(set(primary.get("missing_required_data", []) + primary.get("data_quality_blockers", [])))
    action = decide_action(case, primary, state, missing_confirmation)
    observation = None
    if primary.get("portfolio_status") == "active":
        observation = specialized_observation(
            agent, specialized_playbooks or [], primary["playbook_id"], case["row"]
        )
    fact_ids = sorted(
        key for key, value in case["row"].items()
        if value not in (None, "", [], {}) and key not in {"ticker"}
    )
    return {
        "schema": RESPONSE_SCHEMA,
        "agent": agent,
        "case_id": case["case_id"],
        "primary_playbook_id": primary["playbook_id"],
        "primary_side": primary["preferred_side"],
        "portfolio_status": primary["portfolio_status"],
        "match_score": primary["match_score"],
        "data_state": "stale" if case["row"].get("fresh_primary_data") is False else ("incomplete" if missing_data else "complete"),
        "missing_or_invalid_fields": missing_data,
        "confirmation_state": state,
        "confirmation_fact_ids": observed,
        "missing_confirmations": missing_confirmation,
        "invalidators": list(primary.get("invalidators", [])),
        "triggered_invalidators": list(case["row"].get("triggered_invalidators", [])),
        "exit_logic": list(primary.get("exit_logic", [])),
        "holding_horizon": primary.get("holding_horizon"),
        "short_gates": list(primary.get("short_gates", [])),
        "unresolved_short_gates": list(primary.get("unresolved_short_gates", [])),
        "counterview": None if counter is None else {
            "playbook_id": counter["playbook_id"],
            "side": counter["preferred_side"],
            "status": counter["portfolio_status"],
            "readiness": counter["readiness"],
            "missing_or_invalid_fields": sorted(set(counter.get("missing_required_data", []) + counter.get("data_quality_blockers", []))),
        },
        "action": action,
        "specialized_evaluator_observation": observation,
        "execution_authority": False,
        "reason_fact_ids": fact_ids,
        "frozen_rationale_preserved": bool(case.get("lifecycle_case")),
        "note": "Exam response only; no order creation, sizing or broker access.",
    }


def expected_action(case: dict[str, Any], response: dict[str, Any]) -> str:
    expected = str(case.get("expected_action"))
    if expected != "status_aware":
        return expected
    return "eligible_for_specialized_evaluator" if response.get("portfolio_status") == "active" else "perspective_only"


def grade_case(case: dict[str, Any], response: dict[str, Any], weights: dict[str, Any]) -> dict[str, Any]:
    side_ok = response["primary_side"] in case.get("expected_sides", [])
    routing_ok = response["primary_playbook_id"] in case.get("expected_playbooks", []) and side_ok
    observation = response.get("specialized_evaluator_observation")
    if case.get("active_specialization_case") and response.get("portfolio_status") == "active":
        routing_ok = routing_ok and bool(observation) and observation.get("direction") == response.get("primary_side")
    counter = response.get("counterview") or {}
    if response["primary_side"] == "flat":
        counter_ok = counter.get("side") in {"long", "short"}
    elif case.get("require_opposite_counterview"):
        counter_ok = counter.get("side") == ("short" if response["primary_side"] == "long" else "long")
    else:
        counter_ok = bool(counter) and counter.get("side") != response["primary_side"]
    required_missing = set(case.get("required_missing", []))
    actual_missing = set(response.get("missing_or_invalid_fields", []))
    data_ok = required_missing.issubset(actual_missing)
    if not required_missing:
        data_ok = response["action"] not in {"eligible_for_specialized_evaluator"} or not actual_missing
    action_ok = response["action"] == expected_action(case, response)
    confirmation_ok = bool(response.get("confirmation_state")) and (
        response["action"] not in {"eligible_for_specialized_evaluator", "perspective_only"}
        or response["confirmation_state"] == "confirmed"
    )
    invalidator_ok = bool(response.get("invalidators")) and (
        not case.get("lifecycle_case") or bool(response.get("triggered_invalidators") or case["row"].get("time_stop_reached"))
    )
    exit_ok = bool(response.get("exit_logic")) and bool(response.get("holding_horizon"))
    short_gate_ok = True
    if response["primary_side"] == "short":
        short_gate_ok = response["action"] not in {"eligible_for_specialized_evaluator"} or not response.get("unresolved_short_gates")
        if case.get("must_abstain"):
            short_gate_ok = short_gate_ok and response["action"] == "no_trade"
    no_trade_ok = not case.get("must_abstain") or response["action"] == "no_trade"
    authority_ok = response.get("execution_authority") is False
    if response.get("portfolio_status") != "active":
        authority_ok = authority_ok and response["action"] != "eligible_for_specialized_evaluator"
    trace_ok = bool(response.get("reason_fact_ids"))
    checks = {
        "routing_side": routing_ok,
        "counterview": counter_ok,
        "data_freshness": data_ok,
        "entry_confirmation": confirmation_ok,
        "invalidators": invalidator_ok,
        "exit_horizon": exit_ok,
        "short_gates": short_gate_ok,
        "no_trade_discipline": no_trade_ok and action_ok,
        "authority_status": authority_ok and action_ok,
        "fact_traceability": trace_ok,
    }
    critical: list[str] = []
    if response.get("execution_authority") is not False:
        critical.append("execution_authority_claimed")
    if response.get("portfolio_status") != "active" and response["action"] == "eligible_for_specialized_evaluator":
        critical.append("non_active_playbook_escalated")
    if response["action"] == "eligible_for_specialized_evaluator" and actual_missing:
        critical.append("action_with_missing_or_invalid_data")
    if response["action"] == "eligible_for_specialized_evaluator" and response.get("confirmation_state") != "confirmed":
        critical.append("action_before_entry_confirmation")
    if response["primary_side"] == "short" and response["action"] == "eligible_for_specialized_evaluator" and response.get("unresolved_short_gates"):
        critical.append("short_escalated_with_unresolved_gates")
    if case.get("must_abstain") and response["action"] != "no_trade":
        critical.append("mandatory_no_trade_ignored")
    if case.get("lifecycle_case") and response["action"] != "manage_existing_exit":
        critical.append("frozen_invalidator_or_time_stop_ignored")
    score = sum(float(weights[name]) for name, passed in checks.items() if passed)
    return {"case_id": case["case_id"], "score": round(score, 2), "checks": checks, "critical_failures": critical}


def run_agent_exam(project_root: Path, agent: str, exam_path: Path, config_path: Path) -> dict[str, Any]:
    if agent not in TRADERS:
        raise ValueError(f"Unsupported trader: {agent}")
    exam = load_exam(exam_path)
    config = load_json(config_path)
    catalog_path = project_root / str(config.get("catalog_file") or "playbook_catalog_v2.json")
    catalog = playbook_framework.load_catalog(catalog_path)
    portfolio = playbook_framework.as_list(config.get("playbook_portfolio"))
    playbook_framework.validate_portfolio(catalog, portfolio)
    catalog_ids = set(playbook_framework.catalog_by_id(catalog))
    portfolio_ids = {str(item.get("playbook_id")) for item in portfolio}
    coverage = len(portfolio_ids & catalog_ids) / max(1, len(catalog_ids))
    responses = [
        build_response(agent, case, catalog, portfolio, playbook_framework.as_list(config.get("playbooks")))
        for case in exam["cases"]
    ]
    grades = [grade_case(case, response, exam["weights"]) for case, response in zip(exam["cases"], responses)]
    domain_values: dict[str, list[bool]] = defaultdict(list)
    for grade in grades:
        for name, passed in grade["checks"].items():
            domain_values[name].append(bool(passed))
    domain_scores = {name: round(100 * sum(values) / len(values), 2) for name, values in domain_values.items()}
    critical = [f"{grade['case_id']}:{item}" for grade in grades for item in grade["critical_failures"]]
    average = round(sum(grade["score"] for grade in grades) / len(grades), 2)
    floor_failures = {
        name: {"score": domain_scores.get(name, 0.0), "required": float(floor)}
        for name, floor in exam.get("required_domain_floors", {}).items()
        if domain_scores.get(name, 0.0) < float(floor)
    }
    active_case_failures = [
        grade["case_id"] for case, grade in zip(exam["cases"], grades)
        if case.get("active_specialization_case") and not all(grade["checks"].values())
    ]
    if not critical and not floor_failures and not active_case_failures and average >= float(exam["pass_threshold"]) and coverage == 1.0:
        status = "pass"
    elif not critical and average >= float(exam["remediation_threshold"]):
        status = "remediation_required"
    else:
        status = "fail"
    source_sha256 = file_digest(project_root / f"{agent}_strategy_agent.py")
    assessment_id = digest({
        "agent": agent,
        "catalog": digest(catalog),
        "portfolio": digest(config),
        "exam": digest(exam),
        "agent_source": source_sha256,
    })
    return {
        "schema": REPORT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "assessment_id": assessment_id,
        "status": status,
        "score": average,
        "pass_threshold": exam["pass_threshold"],
        "catalog_coverage_pct": round(coverage * 100, 2),
        "catalog_sha256": digest(catalog),
        "portfolio_sha256": digest(config),
        "exam_sha256": digest(exam),
        "agent_source_sha256": source_sha256,
        "domain_scores": domain_scores,
        "floor_failures": floor_failures,
        "active_specialization_case_failures": active_case_failures,
        "critical_failures": critical,
        "case_grades": grades,
        "responses": responses,
        "paper_freedom_unlocked": status == "pass",
        "broker_execution_authorized": False,
        "interpretation": "This proves observable contract application on deterministic scenarios, not market skill or profitability.",
    }


def render_agent_report(report: dict[str, Any]) -> str:
    lines = [
        f"# {report['agent'].title()} — Playbook Assimilation Exam", "",
        f"- Result: **{report['status']}**", f"- Score: **{report['score']:.2f}/100**",
        f"- Assessment ID: `{report['assessment_id']}`",
        f"- Catalog coverage: **{report['catalog_coverage_pct']:.2f}%**",
        f"- Critical failures: **{len(report['critical_failures'])}**",
        f"- Paper freedom unlocked by this gate: **{report['paper_freedom_unlocked']}**",
        "- Broker execution authorized: **False**", "",
        "## Domain scores", "",
    ]
    lines.extend(f"- {name}: {score:.2f}%" for name, score in sorted(report["domain_scores"].items()))
    lines.extend(["", "## Scenario results", "", "| Case | Score | Action | Playbook | Side |", "|---|---:|---|---|---|"])
    by_case = {item["case_id"]: item for item in report["responses"]}
    for grade in report["case_grades"]:
        response = by_case[grade["case_id"]]
        lines.append(f"| {grade['case_id']} | {grade['score']:.0f} | {response['action']} | {response['primary_playbook_id']} | {response['primary_side']} |")
    lines.extend(["", "## Gaps", ""])
    if report["floor_failures"]:
        lines.extend(f"- {name}: {item['score']:.2f}% < required {item['required']:.2f}%" for name, item in report["floor_failures"].items())
    if report["active_specialization_case_failures"]:
        lines.append("- Active specialization cases failed: " + ", ".join(report["active_specialization_case_failures"]))
    if report["critical_failures"]:
        lines.extend(f"- CRITICAL: {item}" for item in report["critical_failures"])
    if not report["floor_failures"] and not report["active_specialization_case_failures"] and not report["critical_failures"]:
        lines.append("- No blocking assimilation gaps detected in this deterministic exam.")
    lines.extend(["", "## Interpretation", "", report["interpretation"], ""])
    return "\n".join(lines)


def render_gate_report(gate: dict[str, Any]) -> str:
    lines = [
        "# Yankos & Periklis — Playbook Assimilation Gate", "",
        f"- Overall result: **{gate['status']}**",
        f"- Free paper experiment gate: **{'OPEN' if gate['paper_freedom_unlocked'] else 'BLOCKED'}**",
        "- Broker execution authority created by this exam: **No**", "",
        "| Trader | Result | Score | Catalog coverage | Critical failures |", "|---|---|---:|---:|---:|",
    ]
    for item in gate["agents"]:
        lines.append(
            f"| {item['agent'].title()} | {item['status']} | {item['score']:.2f} | "
            f"{item['catalog_coverage_pct']:.2f}% | {len(item['critical_failures'])} |"
        )
    lines.extend([
        "", "## What this result means", "",
        "Both traders were tested against synthetic, frozen scenarios. The gate checks data semantics and freshness, LONG/SHORT/FLAT routing, explicit counterviews, entry confirmation, invalidators, exits, holding horizon, global SHORT gates, no-trade discipline, active-versus-advisory status and frozen lifecycle exits.",
        "", "P&L and win rate were deliberately excluded. A pass means the current code applies the playbook contract observably; it does not prove profitability, market skill, or readiness for live capital.",
        "", "## Next controlled stage", "",
        "The next stage may be a separate free paper experiment in which trader-owned choices and mistakes are preserved. Initial rationale, data snapshot, playbook/version, status, sizing, confirmations, invalidators, exits and every lifecycle event must be frozen before outcome review.", "",
    ])
    return "\n".join(lines)


def run_all(project_root: Path, exam_path: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    for agent, filename in TRADERS.items():
        report = run_agent_exam(project_root, agent, exam_path, project_root / filename)
        reports.append(report)
        (output_dir / f"{agent}_playbook_assimilation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / f"{agent}_playbook_assimilation.md").write_text(render_agent_report(report), encoding="utf-8")
    overall = "pass" if all(item["status"] == "pass" for item in reports) else "blocked"
    combined = {
        "schema": "ecosystem.trader_playbook_assimilation_gate.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": overall,
        "paper_freedom_unlocked": overall == "pass",
        "broker_execution_authorized": False,
        "agents": [{key: item[key] for key in ("agent", "assessment_id", "status", "score", "catalog_coverage_pct", "critical_failures", "floor_failures")} for item in reports],
    }
    (output_dir / "trader_playbook_assimilation_gate.json").write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "trader_playbook_assimilation_summary.md").write_text(render_gate_report(combined), encoding="utf-8")
    return {"gate": combined, "reports": reports}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the offline trader playbook assimilation exam.")
    parser.add_argument("--agent", choices=tuple(TRADERS))
    parser.add_argument("--exam", default="trader_playbook_exam_v1.json")
    parser.add_argument("--output-dir", default="output/playbook_assimilation")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    exam_path = (root / args.exam).resolve()
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = (root / output_dir).resolve()
    if args.agent:
        report = run_agent_exam(root, args.agent, exam_path, root / TRADERS[args.agent])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / f"{args.agent}_playbook_assimilation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / f"{args.agent}_playbook_assimilation.md").write_text(render_agent_report(report), encoding="utf-8")
        print(json.dumps({key: report[key] for key in ("agent", "status", "score", "critical_failures", "floor_failures")}, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "pass" else 2
    result = run_all(root, exam_path, output_dir)
    print(json.dumps(result["gate"], ensure_ascii=False, indent=2))
    return 0 if result["gate"]["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
