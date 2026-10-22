"""After-close report of the afternoon paper session: what the pre-open check, Koulis, the traders, Miltos and the
position monitors did today. Reads only the ecosystem's own output files (no broker call); a file that was not
written today is reported as missing, never as zero activity. SPY's session move comes from yfinance and stays
unknown if it cannot be read.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
AGENTS = ("yankos", "periklis")


def _load(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _today(payload: dict[str, Any] | None, today: date, *keys: str) -> dict[str, Any] | None:
    """The payload only if one of its timestamps falls on ``today`` (local date)."""
    if payload is None:
        return None
    for key in keys:
        text = str(payload.get(key) or "")
        if not text:
            continue
        try:
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if (stamp.astimezone() if stamp.tzinfo else stamp).date() == today:
            return payload
    return None


def spy_session_move(today: date) -> float | None:
    """SPY open-to-close of today in %, or None when it cannot be read."""
    try:
        import yfinance as yf

        bars = yf.Ticker("SPY").history(start=today.isoformat(), interval="1d", auto_adjust=False)
        bar = bars[bars.index.date == today]
        if bar.empty:
            return None
        return round((float(bar["Close"].iloc[0]) / float(bar["Open"].iloc[0]) - 1) * 100, 2)
    except Exception:  # noqa: BLE001 - unknown, not zero
        return None


def build(out: Path, today: date, spy_move: float | None) -> dict[str, Any]:
    preopen = _today(_load(out / "preopen" / "preopen_check_latest.json"), today, "checked_at")
    koulis = _today(_load(out / "koulis_order_intents_latest.json"), today, "generated_at")
    traders = {a: _today(_load(out / f"{a}_paper_trades_latest.json"), today, "run_timestamp") for a in AGENTS}
    miltos = _today(_load(out / "miltos_entry_stalker_latest.json"), today, "run_timestamp")
    monitors = {a: _today(_load(out / f"{a}_position_monitor_latest.json"), today, "run_timestamp") for a in AGENTS}
    # every cycle's actions from the monitors' day journal; the *_latest files keep only the last cycle
    actions: list[tuple[str, dict[str, Any]]] = []
    journal = out / "paper_session" / f"actions_{today:%Y%m%d}.jsonl"
    if journal.is_file():
        for line in journal.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            actions.append((str(item.get("agent") or ""), item))
    for agent, monitor in monitors.items():
        actions += [(agent, a) for a in (monitor or {}).get("actions") or []]
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for agent, action in actions:  # one row per position: the last action recorded for it
        if action.get("action_type") == "close_position":
            latest[(agent, str(action.get("ticker")))] = {**action, "_agent": agent}
    closes = []
    for action in latest.values():
        plpc = action.get("position_plpc")
        pct = None if plpc is None else round(float(plpc) * 100, 2)
        direction = str(action.get("trade_direction") or "")
        excess = None if pct is None or spy_move is None else round(
            pct - spy_move if direction == "long" else pct + spy_move, 2)
        closes.append({"agent": action["_agent"], "ticker": action.get("ticker"), "direction": direction,
                       "result_pct": pct, "approx_excess_vs_spy_pct": excess,
                       "status": action.get("action_status"), "reason": action.get("decision_reason")})
    known = [c["result_pct"] for c in closes if c["result_pct"] is not None]
    return {
        "schema": "ecosystem.paper_session_report.v1", "session_date": today.isoformat(),
        "spy_open_to_close_pct": spy_move,
        "preopen": None if preopen is None else {"counts": preopen.get("counts"), "demoted": [
            r for r in preopen.get("rows") or [] if r.get("decision") == "demoted_to_watch"]},
        "koulis": None if koulis is None else {
            "approved_count": koulis.get("approved_count"), "approved_notional_usd": koulis.get("approved_notional_usd"),
            "blocked_count": koulis.get("blocked_count"), "prefiltered_count": koulis.get("prefiltered_count"),
            "gate_reason": koulis.get("gate_reason"),
            "approved": [{k: i.get(k) for k in ("agent", "ticker", "trade_direction", "playbook_name", "signal_score",
                                                  "notional_usd")} for i in koulis.get("approved_intents") or []]},
        "traders": {a: None if t is None else {k: t.get(k) for k in (
            "submitted_count", "failed_count", "confirmation_blocked_count", "skipped_count", "new_openings_count")}
            for a, t in traders.items()},
        "miltos": None if miltos is None else {
            "status": miltos.get("status") or ("entry window finished" if miltos.get("entry_window_finished")
                                               else "entry window open" if miltos.get("entry_window_open") else None),
            **{k: miltos.get(k) for k in ("completion_reason", "checked_count", "submitted_count",
                                          "still_waiting_count", "skipped_count", "failed_count")}},
        "closed_positions": closes,
        "day_result": {"positions": len(closes), "with_result": len(known),
                       "average_result_pct": round(sum(known) / len(known), 2) if known else None,
                       "winners": sum(1 for x in known if x > 0), "losers": sum(1 for x in known if x < 0)},
    }


def render(report: dict[str, Any]) -> str:
    def v(value: Any) -> str:
        return "άγνωστο" if value is None else str(value)

    lines = [f"# Συνεδρία paper trading · {report['session_date']}", "",
             f"S&P 500 (SPY) από το άνοιγμα στο κλείσιμο: {v(report['spy_open_to_close_pct'])}%", ""]
    pre = report["preopen"]
    lines.append("## Έλεγχος πριν το άνοιγμα")
    if pre is None:
        lines.append("- Δεν υπάρχει σημερινός έλεγχος.")
    else:
        c = pre["counts"] or {}
        lines.append(f"- Candidates {v(c.get('candidates'))}: έμειναν {v(c.get('kept'))}, σε watch {v(c.get('demoted_to_watch'))}.")
        for row in pre["demoted"][:15]:
            lines.append(f"  - {row.get('agent')} {row.get('ticker')} ({row.get('playbook')}, {row.get('direction')}): "
                         f"{' '.join(row.get('demote_reasons') or [])}")
    lines += ["", "## Koulis (όρια κινδύνου) και Thymios"]
    k = report["koulis"]
    if k is None:
        lines.append("- Δεν υπάρχει σημερινή απόφαση του Koulis.")
    else:
        lines.append(f"- Εγκρίθηκαν {v(k['approved_count'])} (σύνολο ${v(k['approved_notional_usd'])}), μπλοκαρίστηκαν "
                     f"{v(k['blocked_count'])}, φιλτραρίστηκαν {v(k['prefiltered_count'])}. Λόγος: {v(k['gate_reason'])}.")
        for item in k["approved"]:
            lines.append(f"  - {item.get('agent')} · {item.get('ticker')} · {item.get('trade_direction')} · "
                         f"{item.get('playbook_name')} · σκορ {v(item.get('signal_score'))}")
    lines += ["", "## Traders και Miltos"]
    for agent, t in report["traders"].items():
        lines.append(f"- {agent}: " + ("δεν υπάρχει σημερινή εγγραφή." if t is None else
                     f"στάλθηκαν {v(t['submitted_count'])}, απέτυχαν {v(t['failed_count'])}, περιμένουν επιβεβαίωση "
                     f"{v(t['confirmation_blocked_count'])}, παραλείφθηκαν {v(t['skipped_count'])}."))
    m = report["miltos"]
    lines.append("- Miltos: " + ("δεν υπάρχει σημερινή εγγραφή." if m is None else
                 f"{v(m['status'])} · έλεγξε {v(m['checked_count'])}, μπήκαν {v(m['submitted_count'])}, "
                 f"περίμεναν {v(m['still_waiting_count'])}, παραλείφθηκαν {v(m['skipped_count'])}."))
    lines += ["", "## Θέσεις που έκλεισαν"]
    if not report["closed_positions"]:
        lines.append("- Καμία θέση δεν έκλεισε σήμερα (ή δεν άνοιξε καμία).")
    else:
        lines += ["| trader | μετοχή | κατεύθυνση | αποτέλεσμα % | ≈ έναντι SPY % | κατάσταση |", "|---|---|---|---|---|---|"]
        for c in report["closed_positions"]:
            lines.append(f"| {c['agent']} | {c['ticker']} | {c['direction']} | {v(c['result_pct'])} | "
                         f"{v(c['approx_excess_vs_spy_pct'])} | {v(c['status'])} |")
        d = report["day_result"]
        lines.append(f"\nΣύνολο: {d['positions']} θέσεις, μέσο αποτέλεσμα {v(d['average_result_pct'])}%, "
                     f"κερδισμένες {d['winners']}, χαμένες {d['losers']}. (Η σύγκριση με το SPY είναι προσεγγιστική: "
                     "οι είσοδοι έγιναν μετά το άνοιγμα.)")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    today = date.today()
    out = args.root / "output"
    report = build(out, today, spy_session_move(today))
    folder = out / "paper_session"
    folder.mkdir(parents=True, exist_ok=True)
    text = render(report)
    for name in ("session_report_latest", f"session_report_{today:%Y%m%d}"):
        (folder / f"{name}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / f"{name}.md").write_text(text, encoding="utf-8")
    d = report["day_result"]
    print(json.dumps({"approved": (report["koulis"] or {}).get("approved_count"), "closed": d["positions"],
                      "average_result_pct": d["average_result_pct"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
