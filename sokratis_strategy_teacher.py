from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_context import attach_run_context, require_explicit_compatible_runs


DEFAULT_SKILL_MODULES = [
    {
        "name": "risk_reward_validator",
        "strategy_families": ["risk_sizing_holding_period"],
        "math_topics": ["expected_value", "reward_to_risk", "win_rate_needed", "drawdown"],
        "primary_students": ["yankos", "periklis"],
        "starting_status": "training_only",
    },
    {
        "name": "candle_feature_extractor",
        "strategy_families": ["candle_pattern_microstructure_confirmation"],
        "math_topics": ["body_to_range_ratio", "wick_ratio", "close_location_value", "pattern_frequency"],
        "primary_students": ["periklis", "yankos"],
        "starting_status": "training_only",
    },
    {
        "name": "pattern_event_tester",
        "strategy_families": ["support_resistance_reclaim", "breakout_range_expansion", "mean_reversion_fade"],
        "math_topics": ["conditional_returns", "baseline_comparison", "sample_size", "event_study"],
        "primary_students": ["yankos", "periklis"],
        "starting_status": "research_note",
    },
    {
        "name": "regime_detector",
        "strategy_families": ["trend_momentum_continuation", "mean_reversion_fade", "gap_strategy"],
        "math_topics": ["realized_volatility", "range_state", "trend_state", "liquidity_state"],
        "primary_students": ["periklis", "yankos"],
        "starting_status": "research_note",
    },
    {
        "name": "overfit_guard",
        "strategy_families": ["backtest_overfit_promotion_control"],
        "math_topics": ["in_sample_out_of_sample", "variant_count", "data_snooping", "forward_paper_validation"],
        "primary_students": ["yankos", "periklis", "petros"],
        "starting_status": "training_only",
    },
]


PROMOTION_PATH = [
    "research_note",
    "training_only",
    "paper_candidate",
    "active_paper_playbook",
    "paused_or_retired",
]


def load_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8-sig")


def sha256_path(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_record(source_id: str, path: Path, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    return {
        "source_id": source_id,
        "path": str(path),
        "exists": path.exists(),
        "sha256": sha256_path(path),
        "run_id": data.get("run_id"),
        "generated_at": data.get("generated_at") or data.get("run_timestamp"),
    }


def decision_links(source_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    agent = str(payload.get("agent_name") or source_id)
    for row in as_list(payload.get("results")):
        if str(row.get("signal_verdict") or "") not in {"candidate", "watch"}:
            continue
        links.append(
            {
                "source_id": source_id,
                "agent_name": agent,
                "ticker": row.get("ticker"),
                "playbook_name": row.get("playbook_name"),
                "trade_direction": row.get("trade_direction"),
                "signal_verdict": row.get("signal_verdict"),
                "decision_id": row.get("decision_id"),
                "position_id": row.get("position_id"),
                "trade_id": row.get("trade_id"),
            }
        )
    return links


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def as_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def summarize_trader_strategy(path: Path) -> dict[str, Any]:
    payload = load_json(path, default={})
    rows = as_list(payload.get("results"))
    breakdown: dict[str, dict[str, int]] = {}
    top_rows: list[dict[str, Any]] = []
    for row in rows:
        playbook = str(row.get("playbook_name") or "unknown")
        verdict = str(row.get("signal_verdict") or "unknown")
        breakdown.setdefault(playbook, {"candidate": 0, "watch": 0, "reject": 0, "unknown": 0})
        breakdown[playbook][verdict if verdict in breakdown[playbook] else "unknown"] += 1
        if verdict in {"candidate", "watch"} and len(top_rows) < 8:
            top_rows.append(
                {
                    "ticker": row.get("ticker"),
                    "playbook_name": row.get("playbook_name"),
                    "signal_verdict": row.get("signal_verdict"),
                    "signal_score": row.get("signal_score"),
                    "trade_direction": row.get("trade_direction"),
                    "confidence": row.get("confidence"),
                }
            )
    return {
        "source_file": str(path),
        "run_timestamp": payload.get("run_timestamp"),
        "agent_name": payload.get("agent_name") or path.stem,
        "result_count": len(rows),
        "playbook_breakdown": breakdown,
        "top_current_rows": top_rows,
    }


def risk_label_for_backtest(row: dict[str, Any]) -> str:
    sample_count = int(row.get("sample_count") or 0)
    avg_return = row.get("avg_return_pct")
    win_rate = row.get("win_rate_pct")
    if sample_count < 20:
        return "too_few_samples"
    if avg_return is None or win_rate is None:
        return "incomplete"
    if float(avg_return) <= 0:
        return "negative_or_flat_edge"
    if float(win_rate) < 45:
        return "low_win_rate"
    if sample_count < 50:
        return "promising_but_small_sample"
    return "reviewable"


def build_backtest_gate(backtest_payload: dict[str, Any]) -> dict[str, Any]:
    summary_rows = as_list(backtest_payload.get("playbook_summary"))
    gated_rows = []
    for row in summary_rows:
        gated_rows.append(
            {
                "agent_name": row.get("agent_name"),
                "playbook_name": row.get("playbook_name"),
                "signal_verdict": row.get("signal_verdict"),
                "primary_horizon_days": row.get("primary_horizon_days"),
                "sample_count": row.get("sample_count"),
                "avg_return_pct": row.get("avg_return_pct"),
                "win_rate_pct": row.get("win_rate_pct"),
                "promotion_gate": risk_label_for_backtest(row),
            }
        )
    return {
        "tracked_signal_count": backtest_payload.get("tracked_signal_count", 0),
        "trade_row_count": backtest_payload.get("trade_row_count", 0),
        "gate_policy": [
            "Backtest results are evidence, not permission to trade.",
            "Small samples stay training-only even when win rate looks good.",
            "Any strategy promoted from backtest must pass overfit_guard and forward paper validation.",
        ],
        "playbook_gates": gated_rows,
    }


def build_strategy_research_notes(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    for item in as_list(manifest.get("strategies")):
        if str(item.get("owner_agent") or "").lower() != "sokratis":
            continue
        notes.append(
            {
                "name": item.get("name"),
                "status": item.get("status"),
                "style": item.get("style"),
                "library_doc": item.get("library_doc"),
                "backtest_plan_doc": item.get("backtest_plan_doc"),
                "backtest_priority": item.get("backtest_priority"),
                "source_mode": item.get("source_mode"),
                "next_step": "trader_explanation_then_backtest_plan",
            }
        )
    return notes


def build_skill_proposal(module: dict[str, Any], backtest_gate: dict[str, Any]) -> dict[str, Any]:
    return {
        "skill_name": module["name"],
        "status": module["starting_status"],
        "owner_agent": "sokratis",
        "students": module["primary_students"],
        "strategy_families": module["strategy_families"],
        "math_topics": module["math_topics"],
        "outputs": [
            "training lesson for yankos/periklis",
            "plain-language trader explanation prompt",
            "backtest request or interpretation checklist",
            "promotion gate recommendation",
        ],
        "allowed_actions": [
            "draft training-only strategy lessons",
            "explain math behind strategy families",
            "request or interpret backtests",
            "recommend research_note/training_only/paper_candidate status",
        ],
        "blocked_actions": [
            "submit orders",
            "change active playbooks without review",
            "promote a strategy directly to paper trading",
            "override thymios, koulis, yankos, or periklis",
        ],
        "promotion_requirements": [
            "source approved in allowlist or documented as internal synthesis",
            "objective setup, confirmation, invalidation, and review fields",
            "backtest gate reviewed",
            "overfit_guard review completed",
            "forward paper evidence collected before active paper playbook",
        ],
        "current_backtest_context": {
            "tracked_signal_count": backtest_gate.get("tracked_signal_count"),
            "trade_row_count": backtest_gate.get("trade_row_count"),
        },
    }


def pick_lessons_for_trader(agent_name: str) -> list[str]:
    if agent_name == "yankos":
        return [
            "risk_reward_validator",
            "pattern_event_tester",
            "regime_detector",
            "overfit_guard",
        ]
    if agent_name == "periklis":
        return [
            "candle_feature_extractor",
            "pattern_event_tester",
            "regime_detector",
            "overfit_guard",
        ]
    return ["risk_reward_validator", "overfit_guard"]


def build_trader_assignment(
    agent_name: str,
    strategy_summary: dict[str, Any],
    proposals: list[dict[str, Any]],
    backtest_gate: dict[str, Any],
) -> dict[str, Any]:
    lesson_names = pick_lessons_for_trader(agent_name)
    proposal_by_name = {str(item.get("skill_name")): item for item in proposals}
    available_lessons = [proposal_by_name[name] for name in lesson_names if name in proposal_by_name]
    gates = [
        row
        for row in as_list(backtest_gate.get("playbook_gates"))
        if str(row.get("agent_name") or "").lower() == agent_name
    ]
    return {
        "agent_name": agent_name,
        "connected_to": "sokratis",
        "connection_type": "teacher_to_trader_training_loop",
        "board_policy": (
            f"{agent_name} keeps an agent-owned symbol board and execution logic. "
            "Sokratis lessons are shared knowledge filters, not trade instructions or merged symbol lists."
        ),
        "strategy_summary": strategy_summary,
        "assigned_skill_lessons": available_lessons,
        "backtest_items_to_review": gates[:8],
        "required_trader_response": [
            "Explain the lesson in your own words.",
            "State which strategy family it improves.",
            "State what data confirms the setup.",
            "State what invalidates the setup.",
            "Request backtest evidence before asking for promotion.",
        ],
        "blocked_actions": [
            "Do not open trades from a Sokratis lesson alone.",
            "Do not inherit another trader's symbol board, thesis, or execution decision from Sokratis training.",
            "Do not promote a lesson without overfit_guard and review.",
        ],
    }


def build_change_proposals(
    proposals: list[dict[str, Any]],
    trader_assignments: dict[str, Any],
    backtest_gate: dict[str, Any],
) -> list[dict[str, Any]]:
    change_items: list[dict[str, Any]] = []
    playbook_gates = as_list(backtest_gate.get("playbook_gates"))
    risky_gates = [
        row
        for row in playbook_gates
        if str(row.get("promotion_gate") or "") in {"negative_or_flat_edge", "low_win_rate", "too_few_samples"}
    ]
    if risky_gates:
        change_items.append(
            {
                "proposal_id": "review_negative_or_small_sample_playbooks",
                "proposal_type": "analysis_task",
                "affected_agents": sorted({str(row.get("agent_name")) for row in risky_gates if row.get("agent_name")}),
                "reason": "Backtest gate found playbooks with negative/flat edge, low win rate, or too few samples.",
                "suggested_change": "Review whether these playbooks need stricter confirmation, regime filtering, or should stay training-only.",
                "required_evidence": [
                    "latest strategy_backtest report",
                    "trader explanation of failed/weak playbooks",
                    "sample count and forward paper evidence",
                ],
                "approval_status": "pending_next_day_review",
                "code_change_allowed": False,
            }
        )
    for proposal in proposals:
        change_items.append(
            {
                "proposal_id": f"draft_{proposal.get('skill_name')}",
                "proposal_type": "skill_proposal",
                "affected_agents": proposal.get("students", []),
                "reason": f"Sokratis assigned {proposal.get('skill_name')} as an advisory lesson for trader training.",
                "suggested_change": "If trader explanations and backtest evidence support it, draft an internal advisory skill from this lesson.",
                "required_evidence": [
                    "trader end-of-day explanation",
                    "backtest or event-test readout",
                    "overfit_guard review",
                ],
                "approval_status": "pending_next_day_review",
                "code_change_allowed": False,
            }
        )
    if trader_assignments:
        change_items.append(
            {
                "proposal_id": "review_trader_sokratis_training_responses",
                "proposal_type": "meeting_task",
                "affected_agents": list(trader_assignments.keys()),
                "reason": "Traders must explain Sokratis lessons before any lesson becomes a code or playbook change.",
                "suggested_change": "Schedule next-day review of trader responses and convert approved lessons into explicit code/playbook tasks.",
                "required_evidence": [
                    "Sokratis report",
                    "Yankos explanation",
                    "Periklis explanation",
                    "paper-trading behavior from the day",
                ],
                "approval_status": "pending_next_day_review",
                "code_change_allowed": False,
            }
        )
    return change_items


BACKDROP_MIN_SIGNALS = 100   # fewer lab signals in a state: too few to teach anything
BACKDROP_EDGE = 1.0          # percentage points against a random entry in the same state


def build_backdrop_lesson(lab: dict[str, Any], macro: dict[str, Any]) -> dict[str, Any]:
    """The market-backdrop lesson: in today's market state, which of each trader's daily strategies held up in the
    five-year lab (edge against a random entry in the same state) and which did not. Advisory only."""
    state = ((macro or {}).get("market") or {}).get("state")
    rows = (lab or {}).get("by_market_state") or []
    if not state or not rows:
        return {"status": "unavailable", "state": state,
                "reason": "no market state today" if not state else "the strategy lab has no market-state breakdown"}
    try:
        import daily_strategies as ds
        owners = {name: strategy.trader for name, strategy in ds.STRATEGIES.items()}
    except ImportError:
        owners = {}
    traders: dict[str, dict[str, Any]] = {}
    for row in rows:
        trader = owners.get(row.get("strategy"))
        if trader is None:
            continue
        cell = (row.get("by_state") or {}).get(state) or {}
        edge, count = cell.get("edge_vs_random_pct_points"), int(cell.get("n") or 0)
        if edge is None or count < BACKDROP_MIN_SIGNALS:
            verdict = "too_few_signals"
        elif edge >= BACKDROP_EDGE:
            verdict = "works_in_state"
        elif edge <= -BACKDROP_EDGE:
            verdict = "weak_in_state"
        else:
            verdict = "no_edge_in_state"
        traders.setdefault(trader, {"strategies": []})["strategies"].append({
            "strategy_side": f"{row['strategy']} {row['side']}", "horizon_days": row.get("horizon_days"),
            "edge_vs_random_pct_points": edge, "signals": count, "verdict": verdict})
    for view in traders.values():
        view["strategies"].sort(key=lambda item: -(item["edge_vs_random_pct_points"] or -99))
    return {"status": "ok", "state": state, "lab_generated_at": (lab or {}).get("generated_at"),
            "market_state_days": (lab or {}).get("market_state_days"), "traders": traders,
            "authority": "advisory_only: the trader's own playbook still decides; nothing is switched on or off"}


def build_paper_trade_lesson(evaluation: dict[str, Any]) -> dict[str, Any]:
    """What the real paper trades and all candidates (shadow trades) taught each trader about his own strategies:
    live results per strategy and the category findings that passed the luck checks. Advisory only."""
    if not evaluation:
        return {"status": "unavailable", "reason": "no paper-learning evaluation yet"}
    findings = (evaluation.get("category_findings") or {}).get("findings") or []
    traders: dict[str, dict[str, Any]] = {}
    for source in ("strategies",):
        for name, cell in (evaluation.get(source) or {}).items():
            agent = name.split(" · ")[0]
            traders.setdefault(agent, {"live_trades": {}, "signals": {}, "findings": []})["live_trades"][name] = {
                k: cell.get(k) for k in ("known", "days", "win_rate_pct", "mean_pct", "beat_spy_pct", "status")}
    for name, cell in ((evaluation.get("signals") or {}).get("strategies") or {}).items():
        agent = name.split(" · ")[0]
        traders.setdefault(agent, {"live_trades": {}, "signals": {}, "findings": []})["signals"][name] = {
            k: cell.get(k) for k in ("known", "days", "beat_spy_pct", "mean_pct", "status")}
    for finding in findings:
        agent = finding["strategy"].split(" · ")[0]
        traders.setdefault(agent, {"live_trades": {}, "signals": {}, "findings": []})["findings"].append(finding)
    # how long each strategy should stay open: the shadow multi-day holds (fixed and thesis exit per class)
    for name, cells in ((evaluation.get("holding_shadow") or {}).get("strategies") or {}).items():
        agent = name.split(" · ")[0]
        view = traders.setdefault(agent, {"live_trades": {}, "signals": {}, "findings": []})
        view.setdefault("holding", {})[name] = {
            cell: {k: value.get(k) for k in ("known", "days", "mean_excess_pct", "beat_spy_pct", "status")}
            for cell, value in cells.items() if value.get("known")}
    return {"status": "ok", "sessions": evaluation.get("sessions"), "traders": traders,
            "authority": "advisory_only: the trader's own playbook still decides; nothing is switched on or off"}


def build_results_lesson(journal: dict[str, Any]) -> dict[str, Any]:
    """What the results journal taught about trading around results (owner 2026-10-22): for the traders, the drift
    after results by what the results said; for the position monitors, the reaction to a release. Advisory only."""
    if not journal:
        return {"status": "unavailable", "reason": "no results journal yet"}
    findings = journal.get("findings") or []
    return {"status": "ok", "events": journal.get("events"), "tests": journal.get("tests"),
            "for_traders": [f for f in findings if f.get("outcome") in ("drift5", "drift20")],
            "for_position_monitors": findings,
            "authority": "advisory_only: the trader's own playbook still decides; nothing is switched on or off"}


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = Path(args.manifest)
    backtest_path = Path(args.backtest)
    taxonomy_path = Path(args.taxonomy)
    curriculum_path = Path(args.curriculum)
    intake_path = Path(args.intake)
    yankos_path = Path(args.yankos)
    periklis_path = Path(args.periklis)
    manifest = load_json(manifest_path)
    backtest = load_json(backtest_path, default={})
    yankos_payload = load_json(yankos_path, default={})
    periklis_payload = load_json(periklis_path, default={})
    run_id = require_explicit_compatible_runs(yankos_payload, periklis_payload)
    taxonomy_text = read_text(taxonomy_path)
    curriculum_text = read_text(curriculum_path)
    intake_text = read_text(intake_path)
    backtest_gate = build_backtest_gate(backtest)
    strategy_research_notes = build_strategy_research_notes(manifest)
    proposals = [build_skill_proposal(module, backtest_gate) for module in DEFAULT_SKILL_MODULES]
    yankos_summary = summarize_trader_strategy(yankos_path)
    periklis_summary = summarize_trader_strategy(periklis_path)
    sources = [
        source_record("strategy_manifest", manifest_path, manifest),
        source_record("strategy_backtest", backtest_path, backtest),
        source_record("strategy_taxonomy", taxonomy_path),
        source_record("trader_curriculum", curriculum_path),
        source_record("research_intake", intake_path),
        source_record("yankos_decisions", yankos_path, yankos_payload),
        source_record("periklis_decisions", periklis_path, periklis_payload),
    ]
    linked_decisions = decision_links("yankos_decisions", yankos_payload) + decision_links(
        "periklis_decisions", periklis_payload
    )
    for proposal in proposals:
        proposal["evidence_source_ids"] = ["strategy_manifest", "strategy_backtest"]
        proposal["decision_links"] = linked_decisions
    trader_assignments = {
        "yankos": build_trader_assignment("yankos", yankos_summary, proposals, backtest_gate),
        "periklis": build_trader_assignment("periklis", periklis_summary, proposals, backtest_gate),
    }
    change_proposals = build_change_proposals(proposals, trader_assignments, backtest_gate)
    lab_path = Path(getattr(args, "lab", None) or "output/strategy_lab/strategy_lab_latest.json")
    macro_path = Path(getattr(args, "macro", None) or "output/macro_environment_latest.json")
    backdrop_lesson = build_backdrop_lesson(load_json(lab_path, default={}), load_json(macro_path, default={}))
    paper_path = Path(getattr(args, "paper_learning", None) or "output/paper_learning/evaluation_latest.json")
    paper_lesson = build_paper_trade_lesson(load_json(paper_path, default={}))
    journal_path = Path(getattr(args, "earnings_journal", None) or "output/earnings_journal_latest.json")
    results_lesson = build_results_lesson(load_json(journal_path, default={}))
    sources += [source_record(name, path) for name, path in (("strategy_lab", lab_path), ("macro_environment", macro_path))
                if path.is_file()]
    for proposal in change_proposals:
        proposal["evidence_source_ids"] = ["strategy_manifest", "strategy_backtest", "yankos_decisions", "periklis_decisions"]
        proposal["decision_links"] = linked_decisions
    payload = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "agent_name": "sokratis",
        "role": "Central Strategy Specialist / Skill Creator / Teacher",
        "execution_authority": "advisory_only",
        "manifest_file": args.manifest,
        "taxonomy_file": args.taxonomy,
        "curriculum_file": args.curriculum,
        "intake_file": args.intake,
        "backtest_file": args.backtest,
        "yankos_file": args.yankos,
        "periklis_file": args.periklis,
        "promotion_path": manifest.get("research_framework", {}).get("promotion_path", PROMOTION_PATH),
        "source_policy": manifest.get("policy"),
        "trader_board_policy": (
            "Sokratis is a shared strategy teacher. Yankos and Periklis can both learn from him, "
            "but each trader keeps a separate symbol board, separate playbook logic, and separate "
            "execution responsibility. Sokratis cannot merge their boards or make the trade decision."
        ),
        "workflow": [
            "Sokratis researches or receives strategy ideas, maps them to math families, and converts them into research_note or training_only lessons.",
            "Yankos and Periklis explain the lesson in their own words before applying it to their own separate boards.",
            "A Sokratis lesson can inform a trader's reasoning, but the trader's own playbook remains responsible for symbol selection, entry, exit, and justification.",
            "During the live session, Sokratis only provides lightweight reminders and does not interrupt execution.",
            "Deep trader training happens after the day ends and trader-owned positions are flat.",
            "The backtest layer measures historical behavior when enough data exists.",
            "Overfit guard reviews sample size, variant count, and out-of-sample/forward evidence.",
            "Only reviewed ideas can become paper_candidate; active paper playbooks require explicit approval.",
        ],
        "training_schedule": {
            "during_session": "light_reminders_only",
            "pre_close_prep_from_athens": "22:00",
            "pre_close_prep_scope": "collect_decisions_waiting_setups_lesson_matches_and_draft_questions",
            "end_of_day": "full_training_after_positions_flat",
            "morning": "optional_recap_before_new_scan_or_before_14_30_training_block",
        },
        "loaded_context": {
            "taxonomy_available": bool(taxonomy_text.strip()),
            "curriculum_available": bool(curriculum_text.strip()),
            "intake_available": bool(intake_text.strip()),
        },
        "source_manifest": sources,
        "decision_links": linked_decisions,
        "backtest_gate": backtest_gate,
        "strategy_research_notes": strategy_research_notes,
        "skill_proposals": proposals,
        "trader_assignments": trader_assignments,
        "change_proposals": change_proposals,
        "market_backdrop_lesson": backdrop_lesson,
        "paper_trade_lesson": paper_lesson,
        "results_lesson": results_lesson,
    }
    return attach_run_context(
        payload,
        stage="sokratis_strategy_teacher",
        run_id=run_id,
        source_run_ids=[run_id],
    )


def fmt(value: Any) -> str:
    if value is None or value == "":
        return "N/A"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Sokratis Strategy Teacher Report",
        "",
        f"- Generated at: {payload.get('generated_at')}",
        f"- Run ID: {payload.get('run_id')}",
        f"- Role: {payload.get('role')}",
        f"- Authority: {payload.get('execution_authority')}",
        f"- Source policy: {payload.get('source_policy')}",
        f"- Trader board policy: {payload.get('trader_board_policy')}",
        "",
        "## Workflow",
        "",
    ]
    for item in payload.get("workflow", []):
        lines.append(f"- {item}")

    lines.extend(["", "## Source Identity", ""])
    for source in payload.get("source_manifest", []):
        lines.append(
            f"- {source.get('source_id')} | run_id={source.get('run_id') or 'N/A'} | "
            f"sha256={source.get('sha256') or 'missing'} | {source.get('path')}"
        )

    lines.extend(["", "## Backtest Gate", ""])
    gate = payload.get("backtest_gate", {})
    lines.append(f"- Tracked signals: {gate.get('tracked_signal_count', 0)}")
    lines.append(f"- Matured trade rows: {gate.get('trade_row_count', 0)}")
    for item in gate.get("gate_policy", []):
        lines.append(f"- {item}")

    schedule = payload.get("training_schedule", {})
    lines.extend(["", "## Training Schedule", ""])
    lines.append(f"- During session: {schedule.get('during_session')}")
    lines.append(f"- Pre-close prep from Athens: {schedule.get('pre_close_prep_from_athens')}")
    lines.append(f"- Pre-close prep scope: {schedule.get('pre_close_prep_scope')}")
    lines.append(f"- End of day: {schedule.get('end_of_day')}")
    lines.append(f"- Morning: {schedule.get('morning')}")

    playbook_gates = gate.get("playbook_gates", [])
    lines.extend(["", "## Playbook Promotion Read", ""])
    if playbook_gates:
        for row in playbook_gates[:12]:
            lines.append(
                f"- {row.get('agent_name')} / {row.get('playbook_name')} / {row.get('signal_verdict')} | "
                f"samples={fmt(row.get('sample_count'))} | avg={fmt(row.get('avg_return_pct'))}% | "
                f"win={fmt(row.get('win_rate_pct'))}% | gate={row.get('promotion_gate')}"
            )
    else:
        lines.append("- No matured backtest gates available yet.")

    lines.extend(["", "## Sokratis Strategy Research Notes", ""])
    research_notes = payload.get("strategy_research_notes", [])
    if research_notes:
        for item in research_notes:
            lines.append(
                f"- {item.get('name')} | status={item.get('status')} | style={item.get('style')} | "
                f"backtest_priority={fmt(item.get('backtest_priority'))} | "
                f"next={item.get('next_step')} | doc={item.get('library_doc')} | "
                f"plan={fmt(item.get('backtest_plan_doc'))}"
            )
    else:
        lines.append("- No Sokratis-owned strategy research notes found.")

    lines.extend(["", "## Skill Proposals", ""])
    for proposal in payload.get("skill_proposals", []):
        lines.append(f"### {proposal.get('skill_name')}")
        lines.append("")
        lines.append(f"- Status: {proposal.get('status')}")
        lines.append(f"- Students: {', '.join(proposal.get('students', []))}")
        lines.append(f"- Strategy families: {', '.join(proposal.get('strategy_families', []))}")
        lines.append(f"- Math topics: {', '.join(proposal.get('math_topics', []))}")
        lines.append("- Blocked from broker actions: yes")
        lines.append("")
    lines.extend(["", "## Change Proposals For Next-Day Review", ""])
    change_proposals = payload.get("change_proposals", [])
    if change_proposals:
        for item in change_proposals:
            lines.append(f"### {item.get('proposal_id')}")
            lines.append("")
            lines.append(f"- Type: {item.get('proposal_type')}")
            lines.append(f"- Affected agents: {', '.join(str(agent) for agent in item.get('affected_agents', []))}")
            lines.append(f"- Reason: {item.get('reason')}")
            lines.append(f"- Suggested change: {item.get('suggested_change')}")
            lines.append(f"- Approval status: {item.get('approval_status')}")
            lines.append(f"- Code change allowed now: {item.get('code_change_allowed')}")
            lines.append("")
    else:
        lines.append("- No change proposals generated.")

    lines.extend(["", "## Trader Connections", ""])
    assignments = payload.get("trader_assignments", {})
    if isinstance(assignments, dict):
        for agent_name in ("yankos", "periklis"):
            assignment = assignments.get(agent_name, {})
            if not isinstance(assignment, dict):
                continue
            summary = assignment.get("strategy_summary", {})
            lines.append(f"### {agent_name}")
            lines.append("")
            lines.append(f"- Connected to: {assignment.get('connected_to')}")
            lines.append(f"- Board policy: {assignment.get('board_policy')}")
            lines.append(f"- Latest strategy file: {summary.get('source_file')}")
            lines.append(f"- Current rows: {summary.get('result_count')}")
            lesson_names = [item.get("skill_name") for item in assignment.get("assigned_skill_lessons", [])]
            lines.append(f"- Assigned lessons: {', '.join(str(item) for item in lesson_names) or 'none'}")
            top_rows = summary.get("top_current_rows", [])
            if top_rows:
                for row in top_rows[:4]:
                    lines.append(
                        f"- Current setup to explain: {row.get('ticker')} | {row.get('playbook_name')} | "
                        f"{row.get('signal_verdict')} | {row.get('trade_direction')}"
                    )
            for item in assignment.get("required_trader_response", [])[:3]:
                lines.append(f"- Required response: {item}")
            lines.append("")
    lesson = payload.get("market_backdrop_lesson") or {}
    lines.extend(["## Market Backdrop Lesson", ""])
    if lesson.get("status") != "ok":
        lines.append(f"- Unavailable: {lesson.get('reason')}")
    else:
        lines.append(f"- Today's market state: {lesson.get('state')} (lab days per state: {lesson.get('market_state_days')})")
        for trader, view in lesson.get("traders", {}).items():
            for item in view.get("strategies", []):
                lines.append(f"- {trader} | {item['strategy_side']} | edge {fmt(item['edge_vs_random_pct_points'])} "
                             f"| signals {item['signals']} | {item['verdict']}")
    paper = payload.get("paper_trade_lesson") or {}
    lines.extend(["", "## Paper Trade Lesson", ""])
    if paper.get("status") != "ok":
        lines.append(f"- Unavailable: {paper.get('reason')}")
    else:
        for trader, view in paper.get("traders", {}).items():
            for name, cell in view.get("signals", {}).items():
                lines.append(f"- {trader} | signals {name} | {cell.get('known')} | beat SPY "
                             f"{fmt(cell.get('beat_spy_pct'))} | {cell.get('status')}")
            for finding in view.get("findings", []):
                lines.append(f"- {trader} | finding {finding['strategy']} | {finding['tag']}={finding['value']} "
                             f"| {finding['effect_pct_points']:+} points | q={finding['q_value']}")
            for name, cells in view.get("holding", {}).items():
                lines.append(f"- {trader} | holding {name} | " + "; ".join(
                    f"{cell} {value.get('known')} excess {fmt(value.get('mean_excess_pct'))} {value.get('status')}"
                    for cell, value in cells.items()))
    results = payload.get("results_lesson") or {}
    lines.extend(["", "## Results Lesson", ""])
    if results.get("status") != "ok":
        lines.append(f"- Unavailable: {results.get('reason')}")
    else:
        lines.append(f"- Journal: {results.get('events')} results events, {results.get('tests')} tests")
        for finding in results.get("for_position_monitors", []):
            lines.append(f"- {finding['outcome']} | {finding['tag']}={finding['value']} | beat SPY "
                         f"{finding['beat_spy_pct']}% vs {finding['rest_beat_spy_pct']}% | q={finding['q_value']}")
    lines.append("")
    return "\n".join(lines)


def command_build(args: argparse.Namespace) -> None:
    payload = build_payload(args)
    out_prefix = Path(args.out_prefix)
    out_json = Path(f"{out_prefix}_latest.json")
    out_md = Path(f"{out_prefix}_latest.md")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(as_json(payload), encoding="utf-8")
    out_md.write_text(render_markdown(payload), encoding="utf-8")
    print(f"[OUTPUT] JSON: {out_json}")
    print(f"[OUTPUT] MD: {out_md}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sokratis strategy teacher and internal skill proposal generator.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="Build advisory skill proposals for trader training.")
    build.add_argument("--manifest", default="strategy_library/strategy_manifest.json")
    build.add_argument("--taxonomy", default="strategy_library/math_strategy_taxonomy.md")
    build.add_argument("--curriculum", default="strategy_library/trader_math_skill_curriculum.md")
    build.add_argument("--intake", default="strategy_library/strategy_research_intake.md")
    build.add_argument("--backtest", default="output/strategy_backtest_latest.json")
    build.add_argument("--yankos", default="output/yankos_strategy_latest.json")
    build.add_argument("--periklis", default="output/periklis_strategy_latest.json")
    build.add_argument("--lab", default="output/strategy_lab/strategy_lab_latest.json")
    build.add_argument("--macro", default="output/macro_environment_latest.json")
    build.add_argument("--paper-learning", default="output/paper_learning/evaluation_latest.json")
    build.add_argument("--earnings-journal", default="output/earnings_journal_latest.json")
    build.add_argument("--out-prefix", default="output/sokratis_strategy_teacher")
    build.set_defaults(func=command_build)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
