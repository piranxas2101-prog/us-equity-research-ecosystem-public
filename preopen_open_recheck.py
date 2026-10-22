"""Recheck at the open (09:32 New York): the ideas the pre-open check took down, judged again with the opening price.

Owner's decision 2026-10-22 (the VRTX case: taken down at 09:00 for a pre-market gap of -2.85% on the thin IEX
feed, opened -0.57%, rose 2.5%). After the open and before the 09:35 orders, every row the pre-open check demoted
is judged again by the rule of ``preopen_recheck.recheck`` with the opening price, the pre-open check's own last
close and ATR(14): a gap reason comes back when the opening gap against the trade is within 1 x ATR; a material
8-K when the opening reaction against the trade is within 0.5 x ATR; a supporting 8-K within 1 x ATR. An opening
price that cannot be read keeps the idea on watch.

The switch ``preopen.recheck_at_open`` in scanner_config.json decides what happens:

* ``off``: nothing runs;
* ``log`` (the starting value): the decisions are written to ``output/preopen/open_recheck_latest.{json,md}`` and
  the pre-open files are left as they are - evidence with the real 09:32 prices before anything changes;
* ``on``: restored rows go back to their nightly verdict in the pre-open files the traders read at 09:35, marked
  with the recheck. Koulis, Thymios, Miltos and all limits apply to them as to any candidate.

Read-only market data (Alpaca snapshots, IEX). A failure leaves the pre-open files untouched.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
import os
from pathlib import Path
from typing import Any, Callable

import preopen_check as pc
import preopen_recheck as pr

ROOT = Path(__file__).resolve().parent
MODES = ("off", "log", "on")


def mode(config_path: Path) -> str:
    try:
        value = (json.loads(config_path.read_text(encoding="utf-8-sig")).get("preopen") or {}).get("recheck_at_open")
    except (OSError, ValueError):
        value = None
    return value if value in MODES else "off"


def opening_price(snapshot: dict[str, Any] | None, today: date) -> float | None:
    """Today's open from a snapshot's daily bar (IEX), or None when the bar is not today's."""
    bar = (snapshot or {}).get("dailyBar") or {}
    price, stamp = pc.to_float(bar.get("o")), str(bar.get("t") or "")
    if price is None or price <= 0 or not stamp:
        return None
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return price if moment.astimezone(pc.NEW_YORK).date() == today else None


def recheck_payload(payload: dict[str, Any], opens: dict[str, float], *, restore: bool, checked_at: str
                    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows, decisions = [], []
    for row in payload.get("results") or []:
        view = (row.get("preopen_check") or {}) if isinstance(row, dict) else {}
        if view.get("decision") != "demoted_to_watch":
            rows.append(row)
            continue
        ticker = str(row.get("ticker") or "").upper()
        direction = str(row.get("trade_direction") or "").lower()
        decision = pr.recheck(direction, view.get("demote_reasons") or [],
                              {"open": opens.get(ticker), "previous_close": pc.to_float(view.get("last_close")),
                               "atr_pct": pc.to_float(view.get("atr_14_pct"))})
        decision = {"ticker": ticker, "playbook": row.get("playbook_name"), "direction": direction,
                    "premarket_gap_pct": view.get("gap_pct"), "opening_price": opens.get(ticker), **decision,
                    "checked_at": checked_at}
        decisions.append(decision)
        if restore and decision["decision"] == "restored":
            row = {**row, "signal_verdict": row.get("nightly_signal_verdict") or "candidate", "open_recheck": decision}
        else:
            row = {**row, "open_recheck": decision}
        rows.append(row)
    return {**payload, "results": rows}, decisions


def run(root: Path, out_dir: Path, fetch: Callable[[list[str]], dict[str, Any]] | None, today: date,
        current_mode: str) -> dict[str, Any]:
    payloads = {agent: json.loads((out_dir / f"{agent}_strategy_preopen_latest.json").read_text(encoding="utf-8-sig"))
                for agent in pc.AGENTS}
    demoted = sorted({str(r.get("ticker") or "").upper() for p in payloads.values() for r in p.get("results") or []
                      if isinstance(r, dict) and (r.get("preopen_check") or {}).get("decision") == "demoted_to_watch"})
    opens: dict[str, float] = {}
    error = None
    if demoted and fetch is not None:
        try:
            snapshots = fetch(demoted)
            for ticker in demoted:
                price = opening_price(snapshots.get(ticker), today)
                if price is not None:
                    opens[ticker] = price
        except Exception as exc:  # noqa: BLE001 - no opening prices: every idea stays on watch
            error = type(exc).__name__
    checked_at = datetime.now(pc.NEW_YORK).isoformat()
    decisions, updated = [], {}
    for agent, payload in payloads.items():
        new, items = recheck_payload(payload, opens, restore=current_mode == "on", checked_at=checked_at)
        updated[agent] = new
        decisions += [{"agent": agent, **d} for d in items]
    if current_mode == "on":
        for agent, payload in updated.items():
            target = out_dir / f"{agent}_strategy_preopen_latest.json"
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            os.replace(temporary, target)
    return {"schema": "ecosystem.preopen_open_recheck.v1", "checked_at": checked_at, "mode": current_mode,
            "demoted": len(decisions), "restored": sum(1 for d in decisions if d["decision"] == "restored"),
            "opening_prices_read": len(opens), "error": error, "files_changed": current_mode == "on",
            "decisions": decisions}


def render(report: dict[str, Any]) -> str:
    lines = [f"# Ξανακρίσιμο στο άνοιγμα · {report['checked_at'][:16]}", "",
             f"Διακόπτης: **{report['mode']}** (log = μόνο καταγραφή, on = οι ιδέες επιστρέφουν στους traders). "
             f"Κατεβασμένες {report['demoted']} · θα επέστρεφαν {report['restored']} · τιμές ανοίγματος "
             f"{report['opening_prices_read']} · αρχεία άλλαξαν: {report['files_changed']}.", "",
             "| trader | μετοχή | στρατηγική | κατ. | gap 09:00 | τιμή ανοίγματος | gap στο άνοιγμα | απόφαση | γιατί |",
             "|---|---|---|---|---|---|---|---|---|"]
    for d in report["decisions"]:
        lines.append(f"| {d['agent']} | {d['ticker']} | {d['playbook']} | {d['direction']} | {d['premarket_gap_pct']} | "
                     f"{d['opening_price']} | {d.get('opening_gap_pct')} | {d['decision']} | {d['why']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--mode", choices=MODES, help="override the scanner_config switch (testing)")
    args = parser.parse_args(argv)
    current = args.mode or mode(args.root / "scanner_config.json")
    if current == "off":
        print(json.dumps({"mode": "off"}))
        return 0
    from research_workflow_orchestrator import load_env

    load_env(args.root / ".env")  # Alpaca market-data keys, never printed
    key_id, secret = os.getenv("ALPACA_API_KEY_ID", "").strip(), os.getenv("ALPACA_API_SECRET_KEY", "").strip()
    fetch = pc.alpaca_snapshot_fetcher(key_id, secret) if key_id and secret else None
    out_dir = args.root / "output" / "preopen"
    report = run(args.root, out_dir, fetch, datetime.now(pc.NEW_YORK).date(), current)
    (out_dir / "open_recheck_latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                                                      encoding="utf-8")
    (out_dir / "open_recheck_latest.md").write_text(render(report), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("mode", "demoted", "restored", "opening_prices_read", "error")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
