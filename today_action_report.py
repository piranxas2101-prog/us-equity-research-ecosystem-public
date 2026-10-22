from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import technical_setup
from common import load_json


def as_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def fmt(value: Any) -> str:
    if value is None or value == "":
        return "N/A"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def classify_instrument(row: dict[str, Any]) -> str:
    existing = str(row.get("instrument_type") or "").strip()
    if existing:
        return existing
    ticker = str(row.get("ticker") or row.get("symbol") or "").upper().strip()
    company = str(row.get("company") or row.get("company_name") or "").lower()
    if ticker.endswith("W") or ticker.endswith("WS") or "warrant" in company:
        return "warrant"
    if ticker.endswith("U") or " unit" in company or company.endswith("units"):
        return "unit"
    if ticker.endswith("R") or " right" in company:
        return "right"
    if "-P" in ticker or "preferred" in company or "preference" in company:
        return "preferred"
    if "fund" in company or "trust" in company or "etf" in company or "etn" in company:
        return "fund_or_trust"
    return "common"


def is_complex(row: dict[str, Any]) -> bool:
    if bool(row.get("complex_security")):
        return True
    return classify_instrument(row) != "common"


def line_for_giannis(row: dict[str, Any]) -> str:
    flags = row.get("dilution_flags") or []
    if isinstance(flags, list):
        flags_text = ", ".join(str(flag) for flag in flags) or "none"
    else:
        flags_text = str(flags)
    instrument = classify_instrument(row)
    technical = technical_setup.analyze_technical_setup(row)
    timing = technical.get("technical_timing")
    rsi = technical.get("rsi_14")
    return_20d = technical.get("return_20d_pct")
    position_60d = technical.get("price_position_60d_pct")
    fresh = bool(row.get("fresh_primary_data", True))
    freshness = "fresh" if fresh else "stale"
    return (
        f"- {fmt(row.get('ticker'))} | score={fmt(row.get('score_final'))}/10 | "
        f"tier={fmt(row.get('giannis_tier'))} | risk={fmt(row.get('risk_level'))} | "
        f"instrument={instrument} | timing={fmt(timing)} | data={freshness} | price={fmt(row.get('share_price'))} | "
        f"rsi={fmt(rsi)} | 20d={fmt(return_20d)}% | 60d_pos={fmt(position_60d)} | flags={flags_text}"
    )


def line_for_babis(row: dict[str, Any]) -> str:
    instrument = classify_instrument(row)
    technical = technical_setup.analyze_technical_setup(row)
    return (
        f"- #{fmt(row.get('rank'))} {fmt(row.get('ticker'))} | "
        f"score={fmt(row.get('composite_score'))} | risk={fmt(row.get('risk_level'))} | "
        f"instrument={instrument} | timing={fmt(technical.get('technical_timing'))} | "
        f"catalyst={fmt(row.get('catalyst_bucket'))}"
    )


def line_for_yankos(row: dict[str, Any]) -> str:
    return (
        f"- {fmt(row.get('ticker'))} | yankos={fmt(row.get('signal_score'))}/10 | "
        f"verdict={fmt(row.get('signal_verdict'))} | playbook={fmt(row.get('playbook_name'))} | "
        f"timing={fmt(row.get('technical_timing'))} | risk={fmt(row.get('risk_level'))} | "
        f"rsi={fmt(row.get('rsi_14'))} | vol_ratio={fmt(row.get('volume_ratio_20'))}"
    )


def line_for_periklis(row: dict[str, Any]) -> str:
    return (
        f"- {fmt(row.get('ticker'))} | periklis={fmt(row.get('signal_score'))}/10 | "
        f"verdict={fmt(row.get('signal_verdict'))} | playbook={fmt(row.get('playbook_name'))} | "
        f"timing={fmt(row.get('technical_timing'))} | risk={fmt(row.get('risk_level'))} | "
        f"rsi={fmt(row.get('rsi_14'))} | vol_ratio={fmt(row.get('volume_ratio_20'))}"
    )


def line_for_team_focus(row: dict[str, Any]) -> str:
    parts = [
        f"- {fmt(row.get('ticker'))}",
        f"giannis={fmt(row.get('giannis_score'))}/10",
        f"yankos={fmt(row.get('yankos_score'))}/10",
    ]
    if row.get("periklis_score") is not None:
        parts.append(f"periklis={fmt(row.get('periklis_score'))}/10")
    parts.extend(
        [
            f"petros={fmt(row.get('petros_score'))}/10",
            f"consensus={fmt(row.get('consensus'))}",
        ]
    )
    return " | ".join(parts)


def line_for_team_review(row: dict[str, Any]) -> str:
    parts = [
        f"- {fmt(row.get('ticker'))}",
        f"giannis={fmt(row.get('giannis_tier'))}",
        f"yankos={fmt(row.get('yankos_verdict'))}",
    ]
    if row.get("periklis_verdict"):
        parts.append(f"periklis={fmt(row.get('periklis_verdict'))}")
    parts.extend(
        [
            f"petros={fmt(row.get('petros_verdict'))}",
            f"reason={fmt(row.get('reason'))}",
        ]
    )
    return " | ".join(parts)


def line_for_selected_company(row: dict[str, Any]) -> str:
    parts = [
        f"- {fmt(row.get('ticker'))}",
        f"bucket={fmt(row.get('review_bucket'))}",
        f"yankos={fmt(row.get('yankos_verdict'))}",
    ]
    if row.get("periklis_verdict"):
        parts.append(f"periklis={fmt(row.get('periklis_verdict'))}")
    parts.extend(
        [
            f"petros={fmt(row.get('petros_verdict'))}",
            f"reason={fmt(row.get('reason'))}",
        ]
    )
    return " | ".join(parts)


def line_for_backtest_summary(row: dict[str, Any]) -> str:
    return (
        f"- {fmt(row.get('agent_name'))} / {fmt(row.get('playbook_name'))} / {fmt(row.get('signal_verdict'))} | "
        f"horizon={fmt(row.get('primary_horizon_days'))}d | samples={fmt(row.get('sample_count'))} | "
        f"avg={fmt(row.get('avg_return_pct'))}% | median={fmt(row.get('median_return_pct'))}% | "
        f"win={fmt(row.get('win_rate_pct'))}%"
    )


def line_for_delta_move(row: dict[str, Any]) -> str:
    instrument = classify_instrument(row)
    previous_rank = row.get("previous_rank")
    current_rank = row.get("current_rank")
    rank_change = row.get("rank_change")
    risk = row.get("current_risk_level") or row.get("risk_level")
    catalyst = row.get("current_catalyst_bucket") or row.get("catalyst_bucket")
    return (
        f"- {fmt(row.get('ticker'))} | rank {fmt(previous_rank)} -> {fmt(current_rank)} "
        f"(+{fmt(rank_change)}) | risk={fmt(risk)} | instrument={instrument} | catalyst={fmt(catalyst)}"
    )


def build_report(
    babis: dict[str, Any],
    giannis: dict[str, Any],
    delta: dict[str, Any] | None,
    yankos: dict[str, Any] | None,
    periklis: dict[str, Any] | None,
    strategy_backtest: dict[str, Any] | None,
    team_decision: dict[str, Any] | None,
    output_path: Path,
    limit: int,
) -> None:
    giannis_rows = as_list(giannis.get("results"))
    babis_rows = as_list(babis.get("shortlist"))
    babis_by_ticker = {str(row.get("ticker") or "").upper(): row for row in babis_rows}
    enriched_giannis_rows = [
        {**babis_by_ticker.get(str(row.get("ticker") or "").upper(), {}), **row}
        for row in giannis_rows
    ]
    core = [row for row in enriched_giannis_rows if row.get("giannis_tier") == "Core Watchlist"]
    selective = [row for row in enriched_giannis_rows if row.get("giannis_tier") == "Selective Watchlist"]
    complex_rows = [row for row in enriched_giannis_rows if is_complex(row)]
    avoid = sorted(enriched_giannis_rows, key=lambda x: float(x.get("score_final") or 0.0))
    early_rows = [
        row
        for row in enriched_giannis_rows
        if technical_setup.analyze_technical_setup(row).get("technical_timing")
        in {"near_lows_reversal", "early_accumulation", "pre_run_watch", "pullback_watch"}
        and row.get("giannis_tier") != "Avoid For Now"
        and not is_complex(row)
    ]
    extended_rows = [
        row
        for row in enriched_giannis_rows
        if technical_setup.analyze_technical_setup(row).get("technical_timing") == "already_extended"
        and row.get("giannis_tier") != "Avoid For Now"
    ]

    delta_obj = delta.get("delta", {}) if isinstance(delta, dict) else {}
    added = as_list(delta_obj.get("added"))[:limit]
    removed = as_list(delta_obj.get("removed"))[:limit]
    moved_up = as_list(delta_obj.get("moved_up"))[:limit]
    summary = delta_obj.get("summary", {}) if isinstance(delta_obj, dict) else {}
    yankos_rows = as_list(yankos.get("results")) if isinstance(yankos, dict) else []
    yankos_candidates = [row for row in yankos_rows if row.get("signal_verdict") == "candidate"]
    yankos_watch = [row for row in yankos_rows if row.get("signal_verdict") == "watch"]
    periklis_rows = as_list(periklis.get("results")) if isinstance(periklis, dict) else []
    periklis_candidates = [row for row in periklis_rows if row.get("signal_verdict") == "candidate"]
    periklis_watch = [row for row in periklis_rows if row.get("signal_verdict") == "watch"]
    backtest_summary = as_list(strategy_backtest.get("playbook_summary")) if isinstance(strategy_backtest, dict) else []
    backtest_candidates = [row for row in backtest_summary if row.get("signal_verdict") == "candidate"]
    backtest_watch = [row for row in backtest_summary if row.get("signal_verdict") == "watch"]
    selected_companies = as_list(team_decision.get("selected_companies")) if isinstance(team_decision, dict) else []
    team_focus = as_list(team_decision.get("focus_now")) if isinstance(team_decision, dict) else []
    team_review = as_list(team_decision.get("review_next")) if isinstance(team_decision, dict) else []

    lines = [
        "# Today Action Report",
        "",
        f"- Babis run: {fmt(babis.get('run_timestamp'))}",
        f"- Giannis run: {fmt(giannis.get('run_timestamp'))}",
        f"- Babis shortlist: {fmt(babis.get('shortlist_total'))}",
        f"- Giannis coverage: {fmt(giannis.get('count'))}",
        f"- Core: {len(core)}",
        f"- Selective: {len(selective)}",
        f"- Complex securities flagged: {len(complex_rows)}",
        "",
        "## Team Final Decision",
        "",
    ]

    if team_focus:
        lines.append("Focus now:")
        for row in team_focus[:limit]:
            lines.append(line_for_team_focus(row))
    else:
        lines.append("- No full-team agreement names available yet.")

    lines.extend(["", "Selected companies for your review:", ""])
    if selected_companies:
        for row in selected_companies[:limit]:
            lines.append(line_for_selected_company(row))
    else:
        lines.append("- No post-meeting review list available yet.")

    lines.extend(["", "Review next:", ""])
    if team_review:
        for row in team_review[:limit]:
            lines.append(line_for_team_review(row))
    else:
        lines.append("- No open team disagreements for review.")

    lines.extend(["", "## Priority Review", ""])
    priority = core[:limit] + selective[: max(0, limit - len(core[:limit]))]
    if priority:
        for row in priority[:limit]:
            lines.append(line_for_giannis(row))
    else:
        lines.append("- No Core/Selective names available.")

    lines.extend(["", "## Babis Top Discovery", ""])
    for row in babis_rows[:limit]:
        lines.append(line_for_babis(row))

    lines.extend(["", "## Near-Lows / Early Technical Opportunities", ""])
    if early_rows:
        for row in sorted(early_rows, key=lambda x: float(x.get("score_final") or 0.0), reverse=True)[:limit]:
            lines.append(line_for_giannis(row))
    else:
        lines.append("- No early technical setups found in Core/Selective/Speculative names.")

    lines.extend(["", "## Yankos Swing Playbook", ""])
    if yankos_candidates or yankos_watch:
        for row in (yankos_candidates + yankos_watch)[:limit]:
            lines.append(line_for_yankos(row))
    else:
        lines.append("- No Yankos swing signals available yet.")

    lines.extend(["", "## Periklis Swing Playbook", ""])
    if periklis_candidates or periklis_watch:
        for row in (periklis_candidates + periklis_watch)[:limit]:
            lines.append(line_for_periklis(row))
    else:
        lines.append("- No Periklis swing signals available yet.")

    lines.extend(["", "## Strategy Backtest Snapshot", ""])
    if backtest_candidates:
        lines.append("Candidate history:")
        for row in backtest_candidates[: min(8, limit)]:
            lines.append(line_for_backtest_summary(row))
    else:
        lines.append("- No candidate backtest history available yet.")
    lines.extend(["", "Watch history:"])
    if backtest_watch:
        for row in backtest_watch[: min(8, limit)]:
            lines.append(line_for_backtest_summary(row))
    else:
        lines.append("- No watch backtest history available yet.")

    lines.extend(["", "## Already Extended / Do Not Chase", ""])
    if extended_rows:
        for row in sorted(extended_rows, key=lambda x: float(x.get("score_final") or 0.0), reverse=True)[:limit]:
            lines.append(line_for_giannis(row))
    else:
        lines.append("- No extended names flagged.")

    lines.extend(["", "## New Entries", ""])
    if added:
        for row in added:
            lines.append(line_for_babis(row))
    else:
        lines.append("- No added entries in latest delta.")

    lines.extend(["", "## Moved Up", ""])
    if moved_up:
        for row in moved_up:
            lines.append(line_for_delta_move(row))
    else:
        lines.append("- No moved-up entries in latest delta.")

    lines.extend(["", "## Complex / Warrant Risk", ""])
    if complex_rows:
        for row in complex_rows[:limit]:
            lines.append(line_for_giannis(row))
    else:
        lines.append("- No complex securities flagged in Giannis output.")

    lines.extend(["", "## Top Traps", ""])
    for row in avoid[:limit]:
        lines.append(line_for_giannis(row))

    lines.extend(["", "## Delta Summary", ""])
    if summary:
        for key, value in summary.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- Delta summary unavailable.")

    lines.extend(["", "## Removed", ""])
    if removed:
        for row in removed:
            lines.append(line_for_babis(row))
    else:
        lines.append("- No removed entries in latest delta.")

    lines.extend(
        [
            "",
            "## Human Review Notes",
            "",
            "- Team Final Decision is the post-meeting output and should be read first.",
            "- Core/Selective are candidates for manual review, not trade instructions.",
            "- Early Technical Opportunities are timing candidates, not automatic buys.",
            "- Periklis is a second swing read so we can compare technical opinions before changing core scoring.",
            "- Strategy Backtest Snapshot is an outcome scorecard, not a promise of future results.",
            "- Already Extended names can still be good companies, but the process should avoid chasing.",
            "- Complex securities are kept visible as risk, not deleted from the process.",
            "- Outcome tracking will judge these setups after 1/3/5/10/20 days, with 20d now serving as the main training horizon.",
        ]
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OUTPUT] Action report: {output_path}")
    return


def main() -> int:
    parser = argparse.ArgumentParser(description="Build concise daily action report.")
    parser.add_argument("--babis", default="output/babis_shortlist_latest.json")
    parser.add_argument("--giannis", default="output/giannis_rule_based_latest.json")
    parser.add_argument("--delta", default="output/babis_shortlist_delta_latest.json")
    parser.add_argument("--yankos", default="output/yankos_strategy_latest.json")
    parser.add_argument("--periklis", default="output/periklis_strategy_latest.json")
    parser.add_argument("--strategy-backtest", default="output/strategy_backtest_latest.json")
    parser.add_argument("--team-decision", default="output/team_decision_latest.json")
    parser.add_argument("--output", default="output/today_action_report.md")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    delta_path = Path(args.delta)
    build_report(
        babis=load_json(Path(args.babis)),
        giannis=load_json(Path(args.giannis)),
        delta=load_json(delta_path) if delta_path.exists() else None,
        yankos=load_json(Path(args.yankos)) if Path(args.yankos).exists() else None,
        periklis=load_json(Path(args.periklis)) if Path(args.periklis).exists() else None,
        strategy_backtest=load_json(Path(args.strategy_backtest)) if Path(args.strategy_backtest).exists() else None,
        team_decision=load_json(Path(args.team_decision)) if Path(args.team_decision).exists() else None,
        output_path=Path(args.output),
        limit=max(1, args.limit),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
