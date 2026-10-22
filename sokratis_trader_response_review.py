from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STRATEGIES = [
    {
        "name": "sokratis_volatility_adjusted_vwap_reclaim",
        "plain_english": (
            "Wait for price to stop falling or squeezing around VWAP, then require a completed "
            "15Min reclaim/rejection candle before considering an entry."
        ),
        "math": [
            "VWAP as intraday fair value",
            "ATR or realized range for volatility normalization",
            "standardized distance from VWAP to avoid chasing",
            "candle body quality and close-location value",
            "event testing versus setup-only baseline",
            "overfit guard before promotion",
        ],
    },
    {
        "name": "sokratis_volatility_compression_breakout",
        "plain_english": (
            "Find a symbol whose range has tightened, then wait for a clean 15Min range break "
            "with volume before treating the move as useful."
        ),
        "math": [
            "rolling high/low range contraction",
            "ATR or realized-volatility percentile",
            "Bollinger Band width or normalized range width",
            "Donchian-style compression range high/low",
            "relative volume expansion",
            "reward/risk validation and out-of-sample review",
        ],
    },
]


def now_local_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def as_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def top_names(agent_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = as_list(agent_payload.get("top_candidates")) or as_list(agent_payload.get("top_watch"))
    return rows[:6]


def unique_top_names(agent_payload: dict[str, Any]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in top_names(agent_payload):
        ticker = str(row.get("ticker") or "").strip()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        unique.append(row)
    return unique


def row_line(row: dict[str, Any]) -> str:
    return (
        f"{row.get('ticker')} | verdict={row.get('signal_verdict')} | "
        f"score={row.get('signal_score')} | playbook={row.get('playbook_name')}"
    )


def build_yankos_response(agent_payload: dict[str, Any]) -> dict[str, Any]:
    names = top_names(agent_payload)
    unique_names = unique_top_names(agent_payload)
    primary_symbol = str(names[0].get("ticker") if names else "AMBP")
    secondary_symbol = str(unique_names[1].get("ticker") if len(unique_names) > 1 else "AMTD")
    return {
        "agent": "yankos",
        "selected_strategy": "sokratis_volatility_adjusted_vwap_reclaim",
        "why_this_strategy": (
            "It directly fixes my main risk: I can like a support-bounce setup too early. "
            "VWAP reclaim forces me to wait until price proves it has stopped falling."
        ),
        "trigger": (
            "For a long, I need a completed 15Min candle to close back above VWAP, with the "
            "close in the upper part of the candle and without being too extended above VWAP."
        ),
        "invalidation": (
            "The idea fails if price loses VWAP again, if the trigger candle is weak, or if the "
            "entry is already too far from VWAP for a rational stop."
        ),
        "reward_risk": (
            "The target must be larger than the stop. If the stop has to be too wide because "
            "volatility is high, I should wait instead of forcing the trade."
        ),
        "symbol_drill": {
            "symbol": primary_symbol,
            "decision": "wait",
            "reason": (
                "I may like the support-bounce thesis, but I should not treat it as an entry "
                "until the 15Min VWAP reclaim confirms timing."
            ),
        },
        "secondary_drill": {
            "symbol": secondary_symbol,
            "strategy": "sokratis_volatility_compression_breakout",
            "decision": "wait",
            "reason": (
                "Inside my own board, I need compression plus a clean range break with volume. "
                "Without that, I risk buying late."
            ),
        },
        "owned_board_policy": (
            "Yankos reviews only symbols produced by his own playbooks. Sokratis can teach a "
            "filter, but cannot merge another trader's board into Yankos' decision process."
        ),
        "current_board": [row_line(row) for row in names],
    }


def build_periklis_response(agent_payload: dict[str, Any]) -> dict[str, Any]:
    names = top_names(agent_payload)
    unique_names = unique_top_names(agent_payload)
    primary_symbol = str(unique_names[0].get("ticker") if unique_names else "AMTD")
    secondary_symbol = str(unique_names[1].get("ticker") if len(unique_names) > 1 else "AVAL")
    return {
        "agent": "periklis",
        "selected_strategy": "sokratis_volatility_compression_breakout",
        "why_this_strategy": (
            "It fits my role because I am early and selective. Compression helps me separate "
            "a real prepared move from a symbol that only looks interesting too soon."
        ),
        "trigger": (
            "For a long, I need compressed range conditions and then a completed 15Min close "
            "above the compression high with volume. For a short, I need the opposite breakdown "
            "plus broker shortability."
        ),
        "invalidation": (
            "The setup fails if price breaks before real compression, closes near the middle "
            "of the candle, loses the range edge immediately, or volume does not expand."
        ),
        "reward_risk": (
            "The stop belongs behind the failed breakout/breakdown area. If that stop is larger "
            "than the realistic target, the correct answer is no trade."
        ),
        "symbol_drill": {
            "symbol": primary_symbol,
            "decision": "wait",
            "reason": (
                "Even if my early-accumulation read is valid, I should wait for compression "
                "and a 15Min break before calling it executable."
            ),
        },
        "secondary_drill": {
            "symbol": secondary_symbol,
            "strategy": "sokratis_volatility_adjusted_vwap_reclaim",
            "decision": "wait",
            "reason": (
                "Even when the name is interesting early, I still need reclaim confirmation. Early interest "
                "is not enough without a completed 15Min timing signal."
            ),
        },
        "owned_board_policy": (
            "Periklis reviews only symbols produced by his own playbooks. Sokratis can teach a "
            "filter, but cannot make Periklis inherit Yankos' symbols or conclusions."
        ),
        "current_board": [row_line(row) for row in names],
    }


def render_strategy_summary() -> list[str]:
    lines = ["## Sokratis Strategies", ""]
    for strategy in STRATEGIES:
        lines.append(f"### {strategy['name']}")
        lines.append("")
        lines.append(f"- Plain logic: {strategy['plain_english']}")
        lines.append("- Math used:")
        for item in strategy["math"]:
            lines.append(f"- {item}")
        lines.append("")
    return lines


def render_response(response: dict[str, Any]) -> list[str]:
    agent = str(response["agent"]).title()
    lines = [
        f"## {agent} Response",
        "",
        f"- Selected strategy: {response['selected_strategy']}",
        f"- Why: {response['why_this_strategy']}",
        f"- Trigger: {response['trigger']}",
        f"- Invalidation: {response['invalidation']}",
        f"- Reward/risk: {response['reward_risk']}",
        "",
        "### Symbol Drill",
        "",
    ]
    lines.append(f"- Board policy: {response['owned_board_policy']}")
    lines.append("")
    drill = response["symbol_drill"]
    lines.append(f"- {drill['symbol']}: {drill['decision']} - {drill['reason']}")
    secondary = response["secondary_drill"]
    lines.append(
        f"- {secondary['symbol']}: {secondary['decision']} via {secondary['strategy']} - {secondary['reason']}"
    )
    lines.extend(["", "### Agent-Owned Board Used", ""])
    for item in response["current_board"]:
        lines.append(f"- {item}")
    return lines


def build_report(review_path: Path) -> dict[str, Any]:
    review = load_json(review_path)
    agents = review.get("agents", {}) if isinstance(review, dict) else {}
    yankos = agents.get("yankos", {}) if isinstance(agents, dict) else {}
    periklis = agents.get("periklis", {}) if isinstance(agents, dict) else {}
    responses = [build_yankos_response(yankos), build_periklis_response(periklis)]
    return {
        "generated_at": now_local_iso(),
        "source_review": str(review_path),
        "purpose": "Capture trader explanations of Sokratis research strategies before backtest promotion.",
        "authority": "training_only_no_broker_action",
        "team_policy": (
            "The trader team is separated by agent-owned boards. Each trader analyzes only his "
            "own symbols with his own rules and preferred strategies. Cross-trader comparison is "
            "for learning and outcome review, not for merging symbol lists."
        ),
        "strategies": STRATEGIES,
        "responses": responses,
        "verdict": {
            "ready_for_paper_candidate": False,
            "recommended_next_step": "write a backtest/event-test plan, starting with VWAP reclaim because it directly addresses the observed timing problem",
        },
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Sokratis Trader Strategy Responses",
        "",
        f"- Generated at: {payload['generated_at']}",
        f"- Source review: {payload['source_review']}",
        f"- Authority: {payload['authority']}",
        f"- Team policy: {payload['team_policy']}",
        "",
    ]
    lines.extend(render_strategy_summary())
    for response in payload["responses"]:
        lines.extend(render_response(response))
        lines.append("")
    verdict = payload["verdict"]
    lines.extend(
        [
            "## Review Verdict",
            "",
            f"- Ready for paper candidate: {verdict['ready_for_paper_candidate']}",
            f"- Recommended next step: {verdict['recommended_next_step']}.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build trader responses to Sokratis research strategies.")
    parser.add_argument("--review", default="output/strategy_training_review_latest.json")
    parser.add_argument("--output-dir", default="output/agent_meetings")
    args = parser.parse_args()

    payload = build_report(Path(args.review))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "sokratis_trader_strategy_responses_latest.json"
    md_path = output_dir / "sokratis_trader_strategy_responses_latest.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    print(f"[OUTPUT] JSON: {json_path}")
    print(f"[OUTPUT] MD: {md_path}")


if __name__ == "__main__":
    main()
