from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from us_equity_monitor import (
    build_llm_prompt,
    call_ollama,
    fetch_snapshot,
    load_dotenv,
    load_json,
    normalize_symbol,
    now_athens,
    read_prompt,
    resolve_agent_prompt_path,
    to_float,
)


def compute_signature(
    tickers: list[str],
    model: str,
    prompt_path: str,
    prompt_sha256: str,
    lookback_days: int,
    min_history_days: int,
) -> str:
    payload = {
        "tickers": tickers,
        "model": model,
        "prompt_path": prompt_path,
        "prompt_sha256": prompt_sha256,
        "lookback_days": lookback_days,
        "min_history_days": min_history_days,
    }
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def load_checkpoint(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def save_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def render_markdown(results: list[dict[str, Any]], total_count: int, run_timestamp: str) -> str:
    lines = [
        "# Γιαννης - Shortlist Fundamental Report",
        "",
        f"- Run timestamp: {run_timestamp}",
        f"- Completed analyses: {len(results)}/{total_count}",
        "",
    ]
    for idx, item in enumerate(results, start=1):
        lines.append(f"## {idx}. {item.get('ticker')} - {item.get('company')}")
        lines.append("")
        lines.append(item.get("analysis") or "No response.")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Giannis analysis for tickers from Babis shortlist with resume.")
    parser.add_argument("--config", default="scanner_config.json", help="Config JSON path.")
    parser.add_argument("--env-file", default=".env", help="Env file path.")
    parser.add_argument(
        "--shortlist",
        default="output/babis_shortlist_latest.json",
        help="Babis shortlist JSON path.",
    )
    parser.add_argument("--limit", type=int, default=300, help="How many shortlist names to analyze.")
    parser.add_argument("--checkpoint", default="output/giannis_shortlist_checkpoint.json", help="Checkpoint path.")
    parser.add_argument("--report", default="output/giannis_shortlist_report_latest.md", help="Report markdown path.")
    parser.add_argument("--num-predict", type=int, default=220, help="Ollama num_predict override.")
    parser.add_argument("--timeout-seconds", type=int, default=180, help="Ollama timeout override.")
    parser.add_argument(
        "--question",
        default="Pipeline handoff from μπαμπης: strict fundamental validation and investability triage.",
        help="Extra focus question appended to the prompt.",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Missing config: {config_path}")
        return 1

    load_dotenv(Path(args.env_file))
    config = load_json(config_path)

    shortlist_path = Path(args.shortlist)
    if not shortlist_path.exists():
        print(f"Missing shortlist file: {shortlist_path}")
        return 1
    shortlist_payload = load_json(shortlist_path)
    shortlist_rows = shortlist_payload.get("shortlist", [])
    if not isinstance(shortlist_rows, list) or not shortlist_rows:
        print("Shortlist file has no rows.")
        return 1

    tickers: list[str] = []
    company_by_ticker: dict[str, str] = {}
    for row in shortlist_rows:
        ticker = normalize_symbol(str(row.get("ticker") or ""))
        if not ticker:
            continue
        if ticker in company_by_ticker:
            continue
        company_by_ticker[ticker] = str(row.get("company") or "")
        tickers.append(ticker)
        if len(tickers) >= max(1, args.limit):
            break
    if not tickers:
        print("No valid tickers found in shortlist.")
        return 1

    scan_cfg = config.get("scan", {})
    data_sources_cfg = config.get("data_sources", {})
    lookback_days = int(scan_cfg.get("lookback_days", 420))
    min_history_days = int(scan_cfg.get("min_history_days", 220))

    ollama_cfg = config.get("ollama", {})
    if not ollama_cfg.get("enabled", False):
        print("Ollama is disabled in config.")
        return 1
    host = str(ollama_cfg.get("host", "http://127.0.0.1:11434"))
    model = str(ollama_cfg.get("model", "gemma:7b"))

    prompt_path = resolve_agent_prompt_path(
        config,
        "agent_1",
        str(ollama_cfg.get("agent_prompt_file", "agent_prompt.txt")),
    )
    system_prompt = read_prompt(prompt_path, agent_name="giannis")
    if not system_prompt:
        print(f"Prompt file is empty or missing: {prompt_path}")
        return 1

    checkpoint_path = Path(args.checkpoint)
    report_path = Path(args.report)
    signature = compute_signature(
        tickers=tickers,
        model=model,
        prompt_path=str(prompt_path),
        prompt_sha256=hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
        lookback_days=lookback_days,
        min_history_days=min_history_days,
    )

    previous = load_checkpoint(checkpoint_path)
    results: list[dict[str, Any]] = []
    processed_tickers: set[str] = set()
    if previous and previous.get("status") == "running" and previous.get("signature") == signature:
        prev_results = previous.get("results", [])
        if isinstance(prev_results, list):
            for row in prev_results:
                ticker = normalize_symbol(str((row or {}).get("ticker") or ""))
                if not ticker:
                    continue
                results.append(row)
                processed_tickers.add(ticker)
        print(f"[GIANNIS] Resuming from checkpoint: {len(processed_tickers)}/{len(tickers)}")

    for idx, ticker in enumerate(tickers, start=1):
        if ticker in processed_tickers:
            continue
        try:
            snapshot = fetch_snapshot(
                ticker,
                lookback_days,
                min_history_days,
                data_sources_cfg=data_sources_cfg,
            )
            user_prompt = build_llm_prompt(snapshot, args.question)
            analysis = call_ollama(
                host=host,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=to_float(ollama_cfg.get("temperature")) or 0.2,
                num_predict=max(64, int(args.num_predict)),
                timeout_seconds=max(60, int(args.timeout_seconds)),
            )
            results.append(
                {
                    "ticker": ticker,
                    "company": company_by_ticker.get(ticker) or str(snapshot.get("company_name") or ""),
                    "analysis": analysis,
                }
            )
            processed_tickers.add(ticker)
            print(f"[GIANNIS] {len(processed_tickers)}/{len(tickers)} completed | {ticker}")
        except Exception as exc:
            results.append(
                {
                    "ticker": ticker,
                    "company": company_by_ticker.get(ticker, ""),
                    "analysis": f"[GIANNIS_ERROR] {exc}",
                }
            )
            processed_tickers.add(ticker)
            print(f"[GIANNIS][ERROR] {ticker} | {exc}")

        run_timestamp = now_athens().isoformat()
        save_checkpoint(
            checkpoint_path,
            {
                "status": "running",
                "signature": signature,
                "run_timestamp": run_timestamp,
                "shortlist_path": str(shortlist_path),
                "total": len(tickers),
                "completed": len(processed_tickers),
                "results": results,
            },
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_markdown(results, len(tickers), run_timestamp), encoding="utf-8")

    final_timestamp = now_athens().isoformat()
    save_checkpoint(
        checkpoint_path,
        {
            "status": "complete",
            "signature": signature,
            "run_timestamp": final_timestamp,
            "shortlist_path": str(shortlist_path),
            "total": len(tickers),
            "completed": len(processed_tickers),
            "results": results,
            "report_path": str(report_path),
        },
    )
    report_path.write_text(render_markdown(results, len(tickers), final_timestamp), encoding="utf-8")
    print(f"[GIANNIS] Complete. Report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
