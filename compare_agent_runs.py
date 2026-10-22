from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


TIER_ORDER = {
    "Avoid For Now": 0,
    "Speculative Monitor": 1,
    "Selective Watchlist": 2,
    "Core Watchlist": 3,
}


def load_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def as_rows(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def map_by_ticker(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").upper().strip()
        if ticker:
            result[ticker] = row
    return result


def tier_value(name: str) -> int:
    return TIER_ORDER.get(name, -1)


def top_tickers(rows: list[dict[str, Any]], key: str, limit: int = 10) -> list[str]:
    cleaned = [row for row in rows if row.get("ticker")]
    if key == "rank":
        cleaned.sort(key=lambda row: float(row.get("rank") or 999999))
    else:
        cleaned.sort(key=lambda row: float(row.get(key) or 0.0), reverse=True)
    return [str(row.get("ticker")).upper() for row in cleaned[:limit]]


def summarize_babis(old_payload: dict[str, Any], new_payload: dict[str, Any]) -> dict[str, Any]:
    old_rows = as_rows(old_payload, "shortlist")
    new_rows = as_rows(new_payload, "shortlist")
    old_map = map_by_ticker(old_rows)
    new_map = map_by_ticker(new_rows)

    old_top10 = top_tickers(old_rows, "rank", 10)
    new_top10 = top_tickers(new_rows, "rank", 10)

    added = []
    removed = []
    rank_improved = []

    for ticker, row in new_map.items():
        if ticker not in old_map:
            added.append(
                {
                    "ticker": ticker,
                    "rank": row.get("rank"),
                    "catalyst_bucket": row.get("catalyst_bucket"),
                    "risk_level": row.get("risk_level"),
                }
            )
        else:
            old_rank = float(old_map[ticker].get("rank") or 999999)
            new_rank = float(row.get("rank") or 999999)
            if new_rank < old_rank:
                rank_improved.append(
                    {
                        "ticker": ticker,
                        "old_rank": int(old_rank),
                        "new_rank": int(new_rank),
                        "rank_delta": int(old_rank - new_rank),
                    }
                )

    for ticker, row in old_map.items():
        if ticker not in new_map:
            removed.append(
                {
                    "ticker": ticker,
                    "rank": row.get("rank"),
                    "catalyst_bucket": row.get("catalyst_bucket"),
                    "risk_level": row.get("risk_level"),
                }
            )

    added.sort(key=lambda row: float(row.get("rank") or 999999))
    removed.sort(key=lambda row: float(row.get("rank") or 999999))
    rank_improved.sort(key=lambda row: float(row.get("rank_delta") or 0.0), reverse=True)

    return {
        "old_run_timestamp": old_payload.get("run_timestamp"),
        "new_run_timestamp": new_payload.get("run_timestamp"),
        "old_shortlist_total": old_payload.get("shortlist_total", len(old_rows)),
        "new_shortlist_total": new_payload.get("shortlist_total", len(new_rows)),
        "top10_overlap_count": len(set(old_top10) & set(new_top10)),
        "old_top10": old_top10,
        "new_top10": new_top10,
        "added_to_shortlist": added[:10],
        "removed_from_shortlist": removed[:10],
        "best_rank_improvements": rank_improved[:10],
    }


def summarize_giannis(old_payload: dict[str, Any], new_payload: dict[str, Any]) -> dict[str, Any]:
    old_rows = as_rows(old_payload, "results")
    new_rows = as_rows(new_payload, "results")
    old_map = map_by_ticker(old_rows)
    new_map = map_by_ticker(new_rows)

    old_counts = Counter(str(row.get("giannis_tier") or "UNKNOWN") for row in old_rows)
    new_counts = Counter(str(row.get("giannis_tier") or "UNKNOWN") for row in new_rows)

    new_core = []
    dropped_core = []
    tier_upgrades = []
    score_improved = []

    for ticker, row in new_map.items():
        new_tier = str(row.get("giannis_tier") or "")
        old_row = old_map.get(ticker)
        old_tier = str((old_row or {}).get("giannis_tier") or "")
        if new_tier == "Core Watchlist" and old_tier != "Core Watchlist":
            new_core.append(
                {
                    "ticker": ticker,
                    "new_score": row.get("score_final"),
                    "old_tier": old_tier or "not_present",
                }
            )
        if old_row:
            if tier_value(new_tier) > tier_value(old_tier):
                tier_upgrades.append(
                    {
                        "ticker": ticker,
                        "old_tier": old_tier,
                        "new_tier": new_tier,
                        "new_score": row.get("score_final"),
                    }
                )
            old_score = float(old_row.get("score_final") or 0.0)
            new_score = float(row.get("score_final") or 0.0)
            if new_score > old_score:
                score_improved.append(
                    {
                        "ticker": ticker,
                        "old_score": round(old_score, 2),
                        "new_score": round(new_score, 2),
                        "score_delta": round(new_score - old_score, 2),
                        "new_tier": new_tier,
                    }
                )

    for ticker, row in old_map.items():
        old_tier = str(row.get("giannis_tier") or "")
        new_tier = str((new_map.get(ticker) or {}).get("giannis_tier") or "")
        if old_tier == "Core Watchlist" and new_tier != "Core Watchlist":
            dropped_core.append(
                {
                    "ticker": ticker,
                    "old_score": row.get("score_final"),
                    "new_tier": new_tier or "not_present",
                }
            )

    new_core.sort(key=lambda row: float(row.get("new_score") or 0.0), reverse=True)
    dropped_core.sort(key=lambda row: float(row.get("old_score") or 0.0), reverse=True)
    tier_upgrades.sort(
        key=lambda row: (tier_value(str(row.get("new_tier") or "")), float(row.get("new_score") or 0.0)),
        reverse=True,
    )
    score_improved.sort(key=lambda row: float(row.get("score_delta") or 0.0), reverse=True)

    return {
        "old_run_timestamp": old_payload.get("run_timestamp"),
        "new_run_timestamp": new_payload.get("run_timestamp"),
        "old_tier_counts": dict(old_counts),
        "new_tier_counts": dict(new_counts),
        "new_core_names": new_core[:10],
        "dropped_core_names": dropped_core[:10],
        "tier_upgrades": tier_upgrades[:10],
        "best_score_improvements": score_improved[:10],
    }


def summarize_signal_list(
    old_rows: list[dict[str, Any]], new_rows: list[dict[str, Any]], verdict_key: str, score_key: str
) -> dict[str, Any]:
    old_map = map_by_ticker(old_rows)
    new_map = map_by_ticker(new_rows)

    old_candidates = {ticker for ticker, row in old_map.items() if str(row.get(verdict_key) or "") == "candidate"}
    new_candidates = {ticker for ticker, row in new_map.items() if str(row.get(verdict_key) or "") == "candidate"}
    old_watch = {ticker for ticker, row in old_map.items() if str(row.get(verdict_key) or "") == "watch"}
    new_watch = {ticker for ticker, row in new_map.items() if str(row.get(verdict_key) or "") == "watch"}

    new_candidate_rows = [
        {"ticker": ticker, "score": new_map[ticker].get(score_key)}
        for ticker in sorted(new_candidates - old_candidates, key=lambda t: float(new_map[t].get(score_key) or 0.0), reverse=True)
    ]
    dropped_candidate_rows = [
        {"ticker": ticker, "score": old_map[ticker].get(score_key)}
        for ticker in sorted(old_candidates - new_candidates, key=lambda t: float(old_map[t].get(score_key) or 0.0), reverse=True)
    ]
    new_watch_rows = [
        {"ticker": ticker, "score": new_map[ticker].get(score_key)}
        for ticker in sorted(new_watch - old_watch, key=lambda t: float(new_map[t].get(score_key) or 0.0), reverse=True)
    ]
    dropped_watch_rows = [
        {"ticker": ticker, "score": old_map[ticker].get(score_key)}
        for ticker in sorted(old_watch - new_watch, key=lambda t: float(old_map[t].get(score_key) or 0.0), reverse=True)
    ]

    return {
        "old_count": len(old_rows),
        "new_count": len(new_rows),
        "new_candidates": new_candidate_rows[:10],
        "dropped_candidates": dropped_candidate_rows[:10],
        "new_watch": new_watch_rows[:10],
        "dropped_watch": dropped_watch_rows[:10],
    }


def summarize_yankos(old_payload: dict[str, Any], new_payload: dict[str, Any]) -> dict[str, Any]:
    old_rows = as_rows(old_payload, "results")
    new_rows = as_rows(new_payload, "results")
    summary = summarize_signal_list(old_rows, new_rows, "signal_verdict", "signal_score")
    summary["old_run_timestamp"] = old_payload.get("run_timestamp")
    summary["new_run_timestamp"] = new_payload.get("run_timestamp")
    return summary


def summarize_periklis(old_payload: dict[str, Any], new_payload: dict[str, Any]) -> dict[str, Any]:
    old_rows = as_rows(old_payload, "results")
    new_rows = as_rows(new_payload, "results")
    summary = summarize_signal_list(old_rows, new_rows, "signal_verdict", "signal_score")
    summary["old_run_timestamp"] = old_payload.get("run_timestamp")
    summary["new_run_timestamp"] = new_payload.get("run_timestamp")
    return summary


def summarize_petros(old_payload: dict[str, Any], new_payload: dict[str, Any]) -> dict[str, Any]:
    old_rows = as_rows(old_payload, "results")
    new_rows = as_rows(new_payload, "results")
    summary = summarize_signal_list(old_rows, new_rows, "petros_verdict", "petros_score")
    summary["old_run_timestamp"] = old_payload.get("run_timestamp")
    summary["new_run_timestamp"] = new_payload.get("run_timestamp")
    return summary


def render_rows(rows: list[dict[str, Any]], score_key: str | None = None, extra_key: str | None = None) -> list[str]:
    lines: list[str] = []
    for row in rows:
        ticker = str(row.get("ticker") or "N/A")
        parts = [ticker]
        if score_key:
            score = row.get(score_key) if score_key in row else row.get("score")
            if score is not None:
                parts.append(f"score={score}")
        if extra_key and row.get(extra_key):
            parts.append(f"{extra_key}={row.get(extra_key)}")
        lines.append("- " + " | ".join(parts))
    return lines or ["- None."]


def build_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Scan Comparison Report",
        "",
        f"- Generated at: {payload.get('generated_at')}",
        f"- Baseline label: {payload.get('baseline_label')}",
        f"- Current label: {payload.get('current_label')}",
        "",
        "## Babis",
        "",
    ]
    babis = payload["babis"]
    lines.extend(
        [
            f"- Old run: {babis.get('old_run_timestamp')}",
            f"- New run: {babis.get('new_run_timestamp')}",
            f"- Shortlist total: {babis.get('old_shortlist_total')} -> {babis.get('new_shortlist_total')}",
            f"- Top 10 overlap: {babis.get('top10_overlap_count')}/10",
            "",
            "Added to shortlist:",
            *render_rows(babis.get("added_to_shortlist", []), extra_key="catalyst_bucket"),
            "",
            "Removed from shortlist:",
            *render_rows(babis.get("removed_from_shortlist", []), extra_key="catalyst_bucket"),
            "",
            "Best rank improvements:",
            *render_rows(babis.get("best_rank_improvements", []), extra_key="rank_delta"),
            "",
            "## Giannis",
            "",
            f"- Old run: {payload['giannis'].get('old_run_timestamp')}",
            f"- New run: {payload['giannis'].get('new_run_timestamp')}",
            "",
            "New Core names:",
            *render_rows(payload["giannis"].get("new_core_names", []), score_key="new_score", extra_key="old_tier"),
            "",
            "Dropped Core names:",
            *render_rows(payload["giannis"].get("dropped_core_names", []), score_key="old_score", extra_key="new_tier"),
            "",
            "Tier upgrades:",
            *render_rows(payload["giannis"].get("tier_upgrades", []), score_key="new_score", extra_key="new_tier"),
            "",
            "## Yankos",
            "",
            f"- Old run: {payload['yankos'].get('old_run_timestamp')}",
            f"- New run: {payload['yankos'].get('new_run_timestamp')}",
            "",
            "New swing candidates:",
            *render_rows(payload["yankos"].get("new_candidates", []), score_key="score"),
            "",
            "Dropped swing candidates:",
            *render_rows(payload["yankos"].get("dropped_candidates", []), score_key="score"),
            "",
        ]
    )
    periklis = payload.get("periklis")
    if isinstance(periklis, dict) and periklis:
        lines.extend(
            [
                "",
                "## Periklis",
                "",
                f"- Old run: {periklis.get('old_run_timestamp')}",
                f"- New run: {periklis.get('new_run_timestamp')}",
                "",
                "New swing candidates:",
                *render_rows(periklis.get("new_candidates", []), score_key="score"),
                "",
                "Dropped swing candidates:",
                *render_rows(periklis.get("dropped_candidates", []), score_key="score"),
            ]
        )
    lines.extend(
        [
            "",
            "## Petros",
            "",
            f"- Old run: {payload['petros'].get('old_run_timestamp')}",
            f"- New run: {payload['petros'].get('new_run_timestamp')}",
            "",
            "New final candidates:",
            *render_rows(payload["petros"].get("new_candidates", []), score_key="score"),
            "",
            "Dropped final candidates:",
            *render_rows(payload["petros"].get("dropped_candidates", []), score_key="score"),
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare previous and current agent scan artifacts.")
    parser.add_argument("--old-babis", required=True)
    parser.add_argument("--new-babis", required=True)
    parser.add_argument("--old-giannis", required=True)
    parser.add_argument("--new-giannis", required=True)
    parser.add_argument("--old-yankos", required=True)
    parser.add_argument("--new-yankos", required=True)
    parser.add_argument("--old-periklis")
    parser.add_argument("--new-periklis")
    parser.add_argument("--old-petros", required=True)
    parser.add_argument("--new-petros", required=True)
    parser.add_argument("--baseline-label", default="previous_scan")
    parser.add_argument("--current-label", default="current_scan")
    parser.add_argument("--out-json", default="output/scan_compare_latest.json")
    parser.add_argument("--out-md", default="output/scan_compare_latest.md")
    args = parser.parse_args()

    payload = {
        "generated_at": __import__("datetime").datetime.now().astimezone().isoformat(),
        "baseline_label": args.baseline_label,
        "current_label": args.current_label,
        "babis": summarize_babis(load_json(Path(args.old_babis)), load_json(Path(args.new_babis))),
        "giannis": summarize_giannis(load_json(Path(args.old_giannis)), load_json(Path(args.new_giannis))),
        "yankos": summarize_yankos(load_json(Path(args.old_yankos)), load_json(Path(args.new_yankos))),
        "petros": summarize_petros(load_json(Path(args.old_petros)), load_json(Path(args.new_petros))),
    }
    if args.old_periklis and args.new_periklis:
        payload["periklis"] = summarize_periklis(
            load_json(Path(args.old_periklis)),
            load_json(Path(args.new_periklis)),
        )

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(build_markdown(payload), encoding="utf-8")
    print(f"[OUTPUT] Compare JSON: {out_json}")
    print(f"[OUTPUT] Compare MD: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
