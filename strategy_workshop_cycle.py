from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from common import load_json


def now_local_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def run_python(script: str, *args: str) -> None:
    cmd = [sys.executable, script, *args]
    result = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(cmd)}")


def as_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def docs_for_agent(manifest: dict[str, Any], agent_name: str) -> list[str]:
    docs: list[str] = []
    for row in as_list(manifest.get("strategies")):
        owner = str(row.get("owner_agent") or "")
        if owner in {agent_name, "training_candidate"}:
            doc = str(row.get("library_doc") or "").strip()
            if doc:
                docs.append(doc)
    return docs


def sokratis_research_notes(manifest: dict[str, Any]) -> list[dict[str, str]]:
    notes: list[dict[str, str]] = []
    for row in as_list(manifest.get("strategies")):
        owner = str(row.get("owner_agent") or "").lower()
        status = str(row.get("status") or "").lower()
        if owner != "sokratis" or status not in {"research_note", "training_only"}:
            continue
        notes.append(
            {
                "name": str(row.get("name") or "unknown"),
                "status": status,
                "style": str(row.get("style") or "unknown"),
                "doc": str(row.get("library_doc") or ""),
            }
        )
    return notes


def short_row(row: dict[str, Any]) -> str:
    ticker = str(row.get("ticker") or "?")
    verdict = str(row.get("signal_verdict") or "unknown")
    score = row.get("signal_score")
    playbook = str(row.get("playbook_name") or "unknown")
    return f"- {ticker} | verdict={verdict} | score={score} | playbook={playbook}"


def compact_row(row: dict[str, Any]) -> str:
    ticker = str(row.get("ticker") or "?")
    verdict = str(row.get("signal_verdict") or "unknown")
    score = row.get("signal_score")
    playbook = str(row.get("playbook_name") or "unknown")
    timing = str(row.get("technical_timing") or "unknown")
    return f"{ticker} | verdict={verdict} | score={score} | playbook={playbook} | timing={timing}"


def clean_review_questions() -> list[str]:
    return [
        "How close was price to the local low when the signal appeared?",
        "Was there real volume confirmation or only a visual setup?",
        "Was the sector waking up too, or was the symbol moving alone?",
        "Was support clear and nearby, or vague and easy to lose?",
        "Was there dilution or structural risk that should have cut confidence?",
    ]


def clean_focus_lines(agent_payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    coverage = as_list(agent_payload.get("backtest_signal_coverage"))
    summary_rows = as_list(agent_payload.get("backtest_summary"))
    breakdown = agent_payload.get("playbook_breakdown", {})

    if coverage:
        parts = []
        for row in coverage[:4]:
            parts.append(
                f"{row.get('playbook_name')} {row.get('signal_verdict')}={row.get('signal_count')}"
            )
        lines.append("Current signal coverage: " + ", ".join(parts) + ".")

    if summary_rows:
        best = summary_rows[0]
        lines.append(
            "Best matured scorecard right now: "
            f"{best.get('playbook_name')} / {best.get('signal_verdict')} at {best.get('primary_horizon_days')}d."
        )
    else:
        lines.append("No matured backtest summary yet, so this cycle stays review-first and rule-based.")

    if isinstance(breakdown, dict):
        for playbook, counts in breakdown.items():
            if not isinstance(counts, dict):
                continue
            candidate_count = int(counts.get("candidate", 0) or 0)
            watch_count = int(counts.get("watch", 0) or 0)
            if candidate_count == 0 and watch_count > 0:
                lines.append(f"{playbook} is producing mostly watch signals, so the trigger still needs tightening.")
    return lines or ["Review current signals and keep the swing entry discipline tight."]


def render_assignment(agent_name: str, agent_payload: dict[str, Any], manifest: dict[str, Any]) -> str:
    lines = [
        f"# {agent_name.title()} Strategy Workshop",
        "",
        f"- Generated at: {now_local_iso()}",
        f"- Source run: {agent_payload.get('run_timestamp')}",
        "",
        "## Focus",
        "",
    ]

    focus = clean_focus_lines(agent_payload)
    if focus:
        for item in focus:
            lines.append(f"- {item}")
    else:
        lines.append("- No focused item was generated for this cycle.")

    lines.extend(["", "## Approved And Training Playbooks", ""])
    docs = docs_for_agent(manifest, agent_name)
    if docs:
        for doc in docs:
            lines.append(f"- {doc}")
    else:
        lines.append("- No playbooks assigned in the manifest.")

    lines.extend(["", "## Sokratis Research Strategy Drill", ""])
    research_notes = sokratis_research_notes(manifest)
    if research_notes:
        for note in research_notes:
            lines.append(
                "- "
                f"{note['name']} | status={note['status']} | style={note['style']} | doc={note['doc']}"
            )
        lines.extend(
            [
                "",
                "Training task:",
                "",
                "- Explain one Sokratis strategy in your own words before the next cycle.",
                "- Identify the exact trigger, invalidation, and reward/risk requirement.",
                "- Pick one current name from your board and say whether the Sokratis filter would accept, reject, or wait.",
                "- Do not promote or trade it; request a backtest/event-test plan first.",
            ]
        )
    else:
        lines.append("- No Sokratis research notes available for this cycle.")

    lines.extend(["", "## Review Questions", ""])
    questions = clean_review_questions()
    if questions:
        for item in questions:
            lines.append(f"- {item}")
    else:
        lines.append("- No review questions were generated.")

    lines.extend(["", "## Current Names To Re-check", ""])
    rows = as_list(agent_payload.get("top_candidates")) or as_list(agent_payload.get("top_watch"))
    if rows:
        for row in rows[:8]:
            lines.append(short_row(row))
    else:
        lines.append("- No names available for this cycle.")

    lines.extend(["", "## Backtest Snapshot", ""])
    summary_rows = as_list(agent_payload.get("backtest_summary"))
    if summary_rows:
        for row in summary_rows[:4]:
            lines.append(
                "- "
                f"{row.get('playbook_name')} | verdict={row.get('signal_verdict')} | "
                f"horizon={row.get('primary_horizon_days')}d | samples={row.get('sample_count')} | "
                f"win_rate={row.get('win_rate_pct')} | avg_return={row.get('avg_return_pct')}"
            )
    else:
        lines.append("- No matured backtest summary yet.")

    return "\n".join(lines) + "\n"


def overlap_tickers(a_rows: list[dict[str, Any]], b_rows: list[dict[str, Any]]) -> list[str]:
    a = {str(row.get("ticker") or "").strip() for row in a_rows if str(row.get("ticker") or "").strip()}
    b = {str(row.get("ticker") or "").strip() for row in b_rows if str(row.get("ticker") or "").strip()}
    return sorted(a & b)


def merged_top_rows(agent_payload: dict[str, Any]) -> list[dict[str, Any]]:
    merged = as_list(agent_payload.get("top_candidates")) + as_list(agent_payload.get("top_watch"))
    unique_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in merged:
        ticker = str(row.get("ticker") or "").strip()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        unique_rows.append(row)
    return unique_rows


def row_by_ticker(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").strip()
        if ticker and ticker not in mapped:
            mapped[ticker] = row
    return mapped


def playbook_totals(agent_payload: dict[str, Any]) -> tuple[int, int]:
    candidate_total = 0
    watch_total = 0
    breakdown = agent_payload.get("playbook_breakdown", {})
    if isinstance(breakdown, dict):
        for counts in breakdown.values():
            if not isinstance(counts, dict):
                continue
            candidate_total += int(counts.get("candidate", 0) or 0)
            watch_total += int(counts.get("watch", 0) or 0)
    return candidate_total, watch_total


def best_scorecard(agent_payload: dict[str, Any]) -> dict[str, Any] | None:
    rows = as_list(agent_payload.get("backtest_summary"))
    if not rows:
        return None

    reliable = [row for row in rows if int(row.get("sample_count", 0) or 0) >= 3]
    target = reliable or rows
    target.sort(
        key=lambda row: (
            float(row.get("win_rate_pct", 0) or 0),
            float(row.get("avg_return_pct", 0) or 0),
            int(row.get("sample_count", 0) or 0),
        ),
        reverse=True,
    )
    return target[0]


def scorecard_line(agent_payload: dict[str, Any]) -> str:
    row = best_scorecard(agent_payload)
    if not row:
        return "No matured scorecard yet."
    return (
        f"{row.get('playbook_name')} / {row.get('signal_verdict')} | "
        f"samples={row.get('sample_count')} | "
        f"win_rate={row.get('win_rate_pct')} | "
        f"avg_return={row.get('avg_return_pct')} at {row.get('primary_horizon_days')}d"
    )


def metric_value(row: dict[str, Any], key: str) -> float:
    value = row.get(key)
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def metric_count(row: dict[str, Any], key: str) -> int:
    value = row.get(key)
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def matching_scorecard(agent_payload: dict[str, Any], playbook_name: str, verdict: str) -> dict[str, Any] | None:
    for row in as_list(agent_payload.get("backtest_summary")):
        if str(row.get("playbook_name") or "") != playbook_name:
            continue
        if str(row.get("signal_verdict") or "") != verdict:
            continue
        return row
    return None


def competition_points(
    agent_payload: dict[str, Any],
    own_rows: dict[str, dict[str, Any]],
    other_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    scorecards = as_list(agent_payload.get("backtest_summary"))
    early_and_right = 0
    cautious_and_right = 0
    bad_entry_avoided = 0

    for row in scorecards:
        avg_return = metric_value(row, "avg_return_pct")
        sample_count = metric_count(row, "sample_count")
        verdict = str(row.get("signal_verdict") or "")
        if avg_return <= 0 or sample_count <= 0:
            continue
        if verdict == "candidate":
            early_and_right += sample_count * 4
        elif verdict == "watch":
            cautious_and_right += sample_count * 3

    shared = sorted(set(own_rows) & set(other_rows))
    cross_review_overlap = len(shared)

    for ticker in shared:
        own_row = own_rows[ticker]
        other_row = other_rows[ticker]
        own_verdict = str(own_row.get("signal_verdict") or "")
        other_verdict = str(other_row.get("signal_verdict") or "")
        own_playbook = str(own_row.get("playbook_name") or "")
        if own_verdict != "watch" or other_verdict != "candidate" or not own_playbook:
            continue
        scorecard = matching_scorecard(agent_payload, own_playbook, "watch")
        if scorecard and metric_value(scorecard, "avg_return_pct") > 0:
            bad_entry_avoided += 3

    total = early_and_right + cautious_and_right + cross_review_overlap + bad_entry_avoided
    return {
        "early_and_right": early_and_right,
        "cautious_and_right": cautious_and_right,
        "cross_review_overlap": cross_review_overlap,
        "bad_entry_avoided": bad_entry_avoided,
        "total": total,
    }


def disagreement_reason(yankos_row: dict[str, Any], periklis_row: dict[str, Any]) -> str:
    yankos_verdict = str(yankos_row.get("signal_verdict") or "")
    periklis_verdict = str(periklis_row.get("signal_verdict") or "")
    yankos_playbook = str(yankos_row.get("playbook_name") or "")
    periklis_playbook = str(periklis_row.get("playbook_name") or "")

    if yankos_verdict == "candidate" and periklis_verdict == "watch":
        return "Yankos' own method sees tradable timing now, while Periklis' separate method still wants a cleaner early-entry confirmation."
    if yankos_verdict == "watch" and periklis_verdict == "candidate":
        return "Periklis' own method is ready to act earlier here, while Yankos' separate method still wants the swing setup to prove itself."
    if yankos_playbook != periklis_playbook:
        return "Their independent boards surfaced the same ticker, but each trader explains it through a different playbook."
    return "The disagreement is small, but it is still worth tracking because later outcomes can teach which independent read was better."


def render_joint_reflection(review_payload: dict[str, Any]) -> str:
    agents = review_payload.get("agents", {}) if isinstance(review_payload, dict) else {}
    yankos = agents.get("yankos", {}) if isinstance(agents, dict) else {}
    periklis = agents.get("periklis", {}) if isinstance(agents, dict) else {}
    yankos_rows = as_list(yankos.get("top_candidates")) or as_list(yankos.get("top_watch"))
    periklis_rows = as_list(periklis.get("top_candidates")) or as_list(periklis.get("top_watch"))
    overlap = overlap_tickers(yankos_rows, periklis_rows)

    lines = [
        "# Joint Strategy Reflection",
        "",
        f"- Generated at: {now_local_iso()}",
        "",
        "## Cross-Review Overlap",
        "",
    ]
    if overlap:
        for ticker in overlap[:10]:
            lines.append(f"- {ticker}")
    else:
        lines.append("- No overlapping top names in this cycle.")

    lines.extend(["", "## Workshop Goal", ""])
    lines.append("- Yankos should apply Sokratis lessons only to his own board, while keeping his anti-chase swing discipline.")
    lines.append("- Periklis should apply Sokratis lessons only to his own board, while keeping his early-entry discipline.")
    lines.append("- Cross-review is for learning and outcome comparison only; it must not merge trader universes or override each trader's execution logic.")
    return "\n".join(lines) + "\n"


def render_post_training_meeting(review_payload: dict[str, Any]) -> str:
    agents = review_payload.get("agents", {}) if isinstance(review_payload, dict) else {}
    yankos = agents.get("yankos", {}) if isinstance(agents, dict) else {}
    periklis = agents.get("periklis", {}) if isinstance(agents, dict) else {}

    yankos_rows = merged_top_rows(yankos)
    periklis_rows = merged_top_rows(periklis)
    yankos_by_ticker = row_by_ticker(yankos_rows)
    periklis_by_ticker = row_by_ticker(periklis_rows)
    shared = sorted(set(yankos_by_ticker) & set(periklis_by_ticker))

    disagreement_lines: list[str] = []
    for ticker in shared:
        yankos_row = yankos_by_ticker[ticker]
        periklis_row = periklis_by_ticker[ticker]
        same_verdict = str(yankos_row.get("signal_verdict") or "") == str(periklis_row.get("signal_verdict") or "")
        same_playbook = str(yankos_row.get("playbook_name") or "") == str(periklis_row.get("playbook_name") or "")
        if same_verdict and same_playbook:
            continue
        disagreement_lines.append(
            "- "
            f"{ticker} | Yankos: {compact_row(yankos_row)} | "
            f"Periklis: {compact_row(periklis_row)} | "
            f"{disagreement_reason(yankos_row, periklis_row)}"
        )

    yankos_candidate_total, yankos_watch_total = playbook_totals(yankos)
    periklis_candidate_total, periklis_watch_total = playbook_totals(periklis)
    yankos_exclusive = sorted(set(yankos_by_ticker) - set(periklis_by_ticker))
    periklis_exclusive = sorted(set(periklis_by_ticker) - set(yankos_by_ticker))
    yankos_points = competition_points(yankos, yankos_by_ticker, periklis_by_ticker)
    periklis_points = competition_points(periklis, periklis_by_ticker, yankos_by_ticker)

    lines = [
        "# Post-Training Strategy Meeting",
        "",
        f"- Generated at: {now_local_iso()}",
        "- Purpose: let Yankos and Periklis compare their logic after the workshop, while preserving separate trader-owned boards and strategy responsibilities.",
        "",
        "## How They Think Right Now",
        "",
        "- Yankos: owns his support-bounce and controlled-breakout board, then pushes harder when his swing setup already looks tradable.",
        "- Periklis: owns his early-accumulation and pre-run confirmation board, then stays more selective before calling a setup executable.",
        "- Team reading: overlap is useful for learning, but it does not merge their universes. Each trader must defend only his own symbols, rules, and strategy choices.",
        "",
        "## Cross-Review Overlap",
        "",
    ]

    if shared:
        for ticker in shared[:10]:
            lines.append(
                "- "
                f"{ticker} | Yankos: {compact_row(yankos_by_ticker[ticker])} | "
                f"Periklis: {compact_row(periklis_by_ticker[ticker])}"
            )
    else:
        lines.append("- No overlapping names reached the top boards this cycle.")

    lines.extend(["", "## Productive Disagreements", ""])
    if disagreement_lines:
        lines.extend(disagreement_lines[:10])
    else:
        lines.append("- No meaningful disagreements this cycle.")

    lines.extend(["", "## Friendly Rivalry Scoreboard", ""])
    lines.append(
        f"- Breadth leader: Yankos with candidate_signals={yankos_candidate_total} and watch_signals={yankos_watch_total}."
    )
    lines.append(
        f"- Selectivity leader: Periklis with candidate_signals={periklis_candidate_total} and watch_signals={periklis_watch_total}."
    )
    lines.append(f"- Yankos best matured scorecard: {scorecard_line(yankos)}.")
    lines.append(f"- Periklis best matured scorecard: {scorecard_line(periklis)}.")
    lines.append(
        "- Current competition read: Yankos is winning on breadth and immediate candidate production, while Periklis is trying to win on cleaner early-entry discipline."
    )

    lines.extend(["", "## Competition Rubric", ""])
    lines.append("- Early and right: +4 points for each matured positive sample from a candidate bucket.")
    lines.append("- Cautious and right: +3 points for each matured positive sample from a watch bucket.")
    lines.append("- Cross-review overlap: +1 point to each agent when their separate boards independently surface the same name.")
    lines.append("- Bad entry avoided: +3 points when one agent keeps a name at watch while the other pushes candidate, and the cautious playbook has a positive matured watch scorecard.")

    lines.extend(["", "## Provisional Competition Score", ""])
    lines.append(
        "- "
        f"Yankos | early_and_right={yankos_points['early_and_right']} | "
        f"cautious_and_right={yankos_points['cautious_and_right']} | "
        f"cross_review_overlap={yankos_points['cross_review_overlap']} | "
        f"bad_entry_avoided={yankos_points['bad_entry_avoided']} | "
        f"total={yankos_points['total']}"
    )
    lines.append(
        "- "
        f"Periklis | early_and_right={periklis_points['early_and_right']} | "
        f"cautious_and_right={periklis_points['cautious_and_right']} | "
        f"cross_review_overlap={periklis_points['cross_review_overlap']} | "
        f"bad_entry_avoided={periklis_points['bad_entry_avoided']} | "
        f"total={periklis_points['total']}"
    )
    if yankos_points["total"] > periklis_points["total"]:
        lines.append("- Score reading: Yankos is ahead for now, mostly through broader positive candidate production.")
    elif periklis_points["total"] > yankos_points["total"]:
        lines.append("- Score reading: Periklis is ahead for now, mostly through selectivity and cleaner caution points.")
    else:
        lines.append("- Score reading: the rivalry is currently tied, which is a good sign that both agents are contributing differently.")

    lines.extend(["", "## Exclusive Names To Defend Next Cycle", ""])
    if yankos_exclusive:
        lines.append("- Yankos-only names: " + ", ".join(yankos_exclusive[:8]))
    else:
        lines.append("- Yankos-only names: none.")
    if periklis_exclusive:
        lines.append("- Periklis-only names: " + ", ".join(periklis_exclusive[:8]))
    else:
        lines.append("- Periklis-only names: none.")

    lines.extend(["", "## Competition Rules", ""])
    lines.append("- Win by later outcomes, not by louder opinions or by producing the most signals.")
    lines.append("- An early and correct read earns credit, but a cautious downgrade that avoids a weak entry also earns credit.")
    lines.append("- Overlapping names should be reviewed to learn whether separate methods independently found useful evidence.")
    lines.append("- Disagreement names should be reviewed only as learning samples; each trader still owns his own universe and execution logic.")
    lines.append("- The goal is friendly pressure, better reasoning, and better coaching for Petros, not ego battles.")
    return "\n".join(lines) + "\n"


def copy_if_exists(source: Path, destination: Path) -> None:
    if source.exists():
        shutil.copy2(source, destination)


def next_cycle_index(output_root: Path) -> int:
    existing = [p for p in output_root.glob("cycle_*") if p.is_dir()]
    return len(existing) + 1


def build_cycle(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    run_python("strategy_backtest.py", "build", "--out-prefix", args.backtest_prefix)
    run_python(
        "strategy_agent_training.py",
        "build",
        "--compare",
        args.compare,
        "--yankos",
        args.yankos,
        "--periklis",
        args.periklis,
        "--backtest",
        args.backtest_json,
        "--output-dir",
        args.output_dir,
    )

    review_payload = load_json(Path(args.review_json))
    manifest_payload = load_json(Path(args.strategy_manifest))
    agents = review_payload.get("agents", {}) if isinstance(review_payload, dict) else {}
    yankos_payload = agents.get("yankos", {}) if isinstance(agents, dict) else {}
    periklis_payload = agents.get("periklis", {}) if isinstance(agents, dict) else {}

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    cycle_dir = output_root / f"cycle_{ts}"
    cycle_dir.mkdir(parents=True, exist_ok=False)

    summary_path = cycle_dir / "01_workshop_summary.md"
    yankos_path = cycle_dir / "02_yankos_assignment.md"
    periklis_path = cycle_dir / "03_periklis_assignment.md"
    joint_path = cycle_dir / "04_joint_reflection.md"
    meeting_path = cycle_dir / "05_post_training_meeting.md"
    latest_meeting_path = Path(args.output_dir) / "strategy_post_training_meeting_latest.md"

    summary_lines = [
        "# Strategy Workshop Cycle",
        "",
        f"- Generated at: {now_local_iso()}",
        f"- Cycle index: {next_cycle_index(output_root)}",
        f"- Compare file: {args.compare}",
        f"- Backtest file: {args.backtest_json}",
        f"- Review file: {args.review_json}",
        "",
        "## Outputs",
        "",
        "- 02_yankos_assignment.md",
        "- 03_periklis_assignment.md",
        "- 04_joint_reflection.md",
        "- 05_post_training_meeting.md",
        "",
        "## Notes",
        "",
        "- This workshop is local, rule-based, and safe. No execution or brokerage action is possible from this cycle.",
        "- The goal is to sharpen swing timing, not to force new trades.",
        "- The cycle closes with a post-training meeting so we can inspect reasoning and keep a healthy rivalry between Yankos and Periklis.",
    ]
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    yankos_path.write_text(render_assignment("yankos", yankos_payload, manifest_payload), encoding="utf-8")
    periklis_path.write_text(render_assignment("periklis", periklis_payload, manifest_payload), encoding="utf-8")
    joint_path.write_text(render_joint_reflection(review_payload), encoding="utf-8")
    meeting_text = render_post_training_meeting(review_payload)
    meeting_path.write_text(meeting_text, encoding="utf-8")
    latest_meeting_path.write_text(meeting_text, encoding="utf-8")

    copy_if_exists(Path(args.review_json), cycle_dir / Path(args.review_json).name)
    copy_if_exists(Path(args.yankos_card), cycle_dir / Path(args.yankos_card).name)
    copy_if_exists(Path(args.periklis_card), cycle_dir / Path(args.periklis_card).name)
    copy_if_exists(Path(args.backtest_md), cycle_dir / Path(args.backtest_md).name)

    latest_cycle_path = output_root / "latest_cycle.txt"
    latest_cycle_path.write_text(str(cycle_dir), encoding="utf-8")

    manifest = {
        "generated_at": now_local_iso(),
        "cycle_dir": str(cycle_dir),
        "summary_md": str(summary_path),
        "yankos_assignment_md": str(yankos_path),
        "periklis_assignment_md": str(periklis_path),
        "joint_reflection_md": str(joint_path),
        "post_training_meeting_md": str(meeting_path),
        "post_training_meeting_latest_md": str(latest_meeting_path),
        "review_json": args.review_json,
        "yankos_card": args.yankos_card,
        "periklis_card": args.periklis_card,
        "backtest_json": args.backtest_json,
        "backtest_md": args.backtest_md,
    }
    manifest_path = output_root / "latest_manifest.json"
    write_json(manifest_path, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build one local strategy workshop cycle for Yankos and Periklis.")
    parser.add_argument("--compare", default="output/scan_compare_strategy_upgrade_latest.json")
    parser.add_argument("--yankos", default="output/yankos_strategy_latest.json")
    parser.add_argument("--periklis", default="output/periklis_strategy_latest.json")
    parser.add_argument("--output-root", default="output/strategy_workshop")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--backtest-prefix", default="output/strategy_backtest")
    parser.add_argument("--backtest-json", default="output/strategy_backtest_latest.json")
    parser.add_argument("--backtest-md", default="output/strategy_backtest_latest.md")
    parser.add_argument("--review-json", default="output/strategy_training_review_latest.json")
    parser.add_argument("--yankos-card", default="output/yankos_strategy_training_latest.md")
    parser.add_argument("--periklis-card", default="output/periklis_strategy_training_latest.md")
    parser.add_argument("--strategy-manifest", default="strategy_library/strategy_manifest.json")
    args = parser.parse_args()

    manifest = build_cycle(args)
    print(f"[WORKSHOP] cycle_dir={manifest['cycle_dir']}")
    print(f"[WORKSHOP] manifest={Path(args.output_root) / 'latest_manifest.json'}")


if __name__ == "__main__":
    main()
