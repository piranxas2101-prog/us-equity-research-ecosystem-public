from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from common import load_json


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def as_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def summarize_results(payload: dict[str, Any]) -> dict[str, Any]:
    rows = as_list(payload.get("results"))
    by_playbook: dict[str, dict[str, int]] = {}
    for row in rows:
        playbook = str(row.get("playbook_name") or "unknown")
        verdict = str(row.get("signal_verdict") or "unknown")
        by_playbook.setdefault(playbook, {"candidate": 0, "watch": 0, "reject": 0, "unknown": 0})
        by_playbook[playbook][verdict if verdict in by_playbook[playbook] else "unknown"] += 1
    top_candidates = [row for row in rows if row.get("signal_verdict") == "candidate"][:10]
    top_watch = [row for row in rows if row.get("signal_verdict") == "watch"][:10]
    return {
        "run_timestamp": payload.get("run_timestamp"),
        "count": payload.get("count", len(rows)),
        "by_playbook": by_playbook,
        "top_candidates": top_candidates,
        "top_watch": top_watch,
    }


def coverage_by_agent(backtest_payload: dict[str, Any], agent_name: str) -> list[dict[str, Any]]:
    rows = as_list(backtest_payload.get("signal_coverage"))
    return [row for row in rows if str(row.get("agent_name") or "") == agent_name]


def scorecards_by_agent(backtest_payload: dict[str, Any], agent_name: str) -> list[dict[str, Any]]:
    rows = as_list(backtest_payload.get("playbook_summary"))
    return [row for row in rows if str(row.get("agent_name") or "") == agent_name]


def compare_slice(compare_payload: dict[str, Any], agent_name: str) -> dict[str, Any]:
    return compare_payload.get(agent_name, {}) if isinstance(compare_payload, dict) else {}


def template_questions() -> list[str]:
    return [
        "Πόσο κοντά ήταν η τιμή στα τοπικά low της όταν δόθηκε το signal;",
        "Υπήρχε πραγματική επιβεβαίωση από volume ή μόνο οπτικό setup;",
        "Ο κλάδος ξυπνούσε ή το symbol ήταν μόνο του;",
        "Το support ήταν καθαρό και κοντινό ή ασαφές;",
        "Υπήρχε dilution / structural risk που θα έπρεπε να κόψει το confidence;",
    ]


def training_focus(
    agent_name: str,
    result_summary: dict[str, Any],
    compare_payload: dict[str, Any],
    backtest_payload: dict[str, Any],
) -> list[str]:
    lines: list[str] = []
    cmp = compare_slice(compare_payload, agent_name)
    new_candidates = as_list(cmp.get("new_candidates"))
    dropped_candidates = as_list(cmp.get("dropped_candidates"))
    new_watch = as_list(cmp.get("new_watch"))
    if new_candidates:
        lines.append(
            f"Νέα candidate setups μετά την αλλαγή λογικής: {', '.join(str(row.get('ticker')) for row in new_candidates[:5])}."
        )
    if dropped_candidates:
        lines.append(
            f"Candidates που χάθηκαν και θέλουν εξήγηση: {', '.join(str(row.get('ticker')) for row in dropped_candidates[:5])}."
        )
    if new_watch and not new_candidates:
        lines.append(
            f"Νέα watch setups που χρειάζονται refinement: {', '.join(str(row.get('ticker')) for row in new_watch[:5])}."
        )

    coverage = coverage_by_agent(backtest_payload, agent_name)
    summaries = scorecards_by_agent(backtest_payload, agent_name)
    if coverage:
        parts = []
        for row in coverage[:4]:
            parts.append(
                f"{row.get('playbook_name')} {row.get('signal_verdict')}={row.get('signal_count')}"
            )
        lines.append("Τρέχουσα κάλυψη signals: " + ", ".join(parts) + ".")
    if not summaries:
        lines.append("Δεν υπάρχουν ακόμη ώριμα 20d outcomes για σοβαρό scorecard, άρα η εκπαίδευση μένει rule-based και review-first.")
    else:
        best = summaries[0]
        lines.append(
            f"Καλύτερο ώριμο scorecard τώρα: {best.get('playbook_name')} / {best.get('signal_verdict')} στο {best.get('primary_horizon_days')}d."
        )

    playbooks = result_summary.get("by_playbook", {})
    for playbook, counts in playbooks.items():
        watch_count = int(counts.get("watch", 0))
        candidate_count = int(counts.get("candidate", 0))
        if candidate_count == 0 and watch_count > 0:
            lines.append(f"Το {playbook} βγάζει κυρίως watch και όχι candidate, άρα θέλει πιο καθαρό trigger definition.")
    return lines[:6]


def build_agent_payload(
    agent_name: str,
    result_summary: dict[str, Any],
    compare_payload: dict[str, Any],
    backtest_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "agent_name": agent_name,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "run_timestamp": result_summary.get("run_timestamp"),
        "count": result_summary.get("count"),
        "playbook_breakdown": result_summary.get("by_playbook", {}),
        "top_candidates": result_summary.get("top_candidates", []),
        "top_watch": result_summary.get("top_watch", []),
        "compare": compare_slice(compare_payload, agent_name),
        "backtest_signal_coverage": coverage_by_agent(backtest_payload, agent_name),
        "backtest_summary": scorecards_by_agent(backtest_payload, agent_name),
        "training_focus": training_focus(agent_name, result_summary, compare_payload, backtest_payload),
        "review_questions": template_questions(),
    }


def render_agent_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# {payload['agent_name'].title()} Strategy Training Card",
        "",
        f"- Generated at: {payload.get('generated_at')}",
        f"- Source run: {payload.get('run_timestamp')}",
        f"- Coverage: {payload.get('count')}",
        "",
        "## Playbook Breakdown",
        "",
    ]
    breakdown = payload.get("playbook_breakdown", {})
    if isinstance(breakdown, dict) and breakdown:
        for playbook, counts in breakdown.items():
            lines.append(
                f"- {playbook} | candidate={counts.get('candidate', 0)} | watch={counts.get('watch', 0)} | reject={counts.get('reject', 0)}"
            )
    else:
        lines.append("- No playbook breakdown available.")

    lines.extend(["", "## Training Focus", ""])
    focus = payload.get("training_focus", [])
    if focus:
        for item in focus:
            lines.append(f"- {item}")
    else:
        lines.append("- No training focus generated.")

    lines.extend(["", "## Review Questions", ""])
    for item in payload.get("review_questions", []):
        lines.append(f"- {item}")

    lines.extend(["", "## Current Top Names", ""])
    current_rows = payload.get("top_candidates") or payload.get("top_watch") or []
    if current_rows:
        for row in current_rows[:8]:
            lines.append(
                f"- {row.get('ticker')} | verdict={row.get('signal_verdict')} | score={row.get('signal_score')} | playbook={row.get('playbook_name')}"
            )
    else:
        lines.append("- No current top names available.")
    return "\n".join(lines)


def render_joint_markdown(
    compare_payload: dict[str, Any],
    yankos_payload: dict[str, Any],
    periklis_payload: dict[str, Any],
    backtest_payload: dict[str, Any],
) -> str:
    lines = [
        "# Strategy Upgrade Review And Training",
        "",
        f"- Generated at: {datetime.now(timezone.utc).astimezone().isoformat()}",
        "",
        "## Upgrade Compare",
        "",
    ]
    for agent_name in ("yankos", "periklis"):
        cmp = compare_slice(compare_payload, agent_name)
        lines.append(f"- {agent_name}:")
        lines.append(
            f"  new candidates={', '.join(str(row.get('ticker')) for row in as_list(cmp.get('new_candidates'))[:5]) or 'none'}"
        )
        lines.append(
            f"  dropped candidates={', '.join(str(row.get('ticker')) for row in as_list(cmp.get('dropped_candidates'))[:5]) or 'none'}"
        )
        lines.append(
            f"  new watch={', '.join(str(row.get('ticker')) for row in as_list(cmp.get('new_watch'))[:5]) or 'none'}"
        )

    lines.extend(["", "## Joint Reading", ""])
    lines.append("- Ο Γιανγκος πρέπει να ελέγξει αν το νέο breakout logic προσθέτει έγκαιρα setups χωρίς να χαλάει το anti-chase φίλτρο.")
    lines.append("- Ο Περικλής πρέπει να ελέγξει αν παραμένει σωστά αυστηρός στα early entries χωρίς να χάνει υπερβολικά καλές περιπτώσεις.")
    lines.append("- Οι δύο agents πρέπει να συγκρίνουν πού συμφωνούν, πού διαφωνούν, και ποια διαφωνία αργότερα δικαιώνεται από τα outcomes.")

    lines.extend(["", "## Backtest Status", ""])
    lines.append(f"- Tracked signals: {backtest_payload.get('tracked_signal_count', 0)}")
    lines.append(f"- Simulated rows with matured outcomes: {backtest_payload.get('trade_row_count', 0)}")
    if int(backtest_payload.get("trade_row_count", 0)) == 0:
        lines.append("- Δεν υπάρχει ακόμη ώριμο 20d ιστορικό, άρα η σημερινή προπόνηση είναι κυρίως review-based και rule-based.")

    lines.extend(["", "## Agent Cards", ""])
    lines.append("- Yankos card: output/yankos_strategy_training_latest.md")
    lines.append("- Periklis card: output/periklis_strategy_training_latest.md")
    return "\n".join(lines)


def command_build(args: argparse.Namespace) -> None:
    compare_payload = load_json(Path(args.compare))
    yankos_payload_raw = load_json(Path(args.yankos))
    periklis_payload_raw = load_json(Path(args.periklis))
    backtest_payload = load_json(Path(args.backtest))

    yankos_payload = build_agent_payload("yankos", summarize_results(yankos_payload_raw), compare_payload, backtest_payload)
    periklis_payload = build_agent_payload("periklis", summarize_results(periklis_payload_raw), compare_payload, backtest_payload)
    joint_payload = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "compare_file": args.compare,
        "backtest_file": args.backtest,
        "agents": {
            "yankos": yankos_payload,
            "periklis": periklis_payload,
        },
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    joint_json = output_dir / "strategy_training_review_latest.json"
    joint_md = output_dir / "strategy_training_review_latest.md"
    yankos_md = output_dir / "yankos_strategy_training_latest.md"
    periklis_md = output_dir / "periklis_strategy_training_latest.md"

    joint_json.write_text(as_json(joint_payload), encoding="utf-8")
    joint_md.write_text(render_joint_markdown(compare_payload, yankos_payload, periklis_payload, backtest_payload), encoding="utf-8")
    yankos_md.write_text(render_agent_markdown(yankos_payload), encoding="utf-8")
    periklis_md.write_text(render_agent_markdown(periklis_payload), encoding="utf-8")
    print(f"[OUTPUT] JSON: {joint_json}")
    print(f"[OUTPUT] MD: {joint_md}")
    print(f"[OUTPUT] Yankos card: {yankos_md}")
    print(f"[OUTPUT] Periklis card: {periklis_md}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate post-upgrade strategy training review for Yankos and Periklis.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="Build strategy training review outputs.")
    build.add_argument("--compare", default="output/scan_compare_strategy_upgrade_latest.json")
    build.add_argument("--yankos", default="output/yankos_strategy_latest.json")
    build.add_argument("--periklis", default="output/periklis_strategy_latest.json")
    build.add_argument("--backtest", default="output/strategy_backtest_latest.json")
    build.add_argument("--output-dir", default="output")
    build.set_defaults(func=command_build)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
