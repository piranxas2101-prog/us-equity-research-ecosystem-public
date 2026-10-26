"""After-close report of the afternoon paper session: what the pre-open check, Koulis, the traders, Miltos and the
position monitors did today. Reads only the ecosystem's own output files (no broker call); a file that was not
written today is reported as missing, never as zero activity. SPY's session move comes from yfinance and stays
unknown if it cannot be read.

T7 (owner 2026-10-26): the report shows the whole day, not the last cycle - every entry with its strategy, tier,
time and who placed it (trader at the start or Miltos later), what kept the waiting entries out (trigger,
volume, momentum, price now), results per strategy and tier, the positions held on their agent's horizon (result,
sessions held, stop, expiry), and Koulis as a summary instead of one line per approval.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
AGENTS = ("yankos", "periklis")
NEW_YORK = ZoneInfo("America/New_York")
CHECKS = ("trigger", "participation", "momentum", "holding_now")
CHECK_GR = {"trigger": "χωρίς σπάσιμο", "participation": "χαμηλός όγκος", "momentum": "λάθος ορμή",
            "holding_now": "η τιμή γύρισε"}
TIER_GR = {"horizon": "με διάρκεια agent", "same_day_measured": "ίδιας μέρας, μετριέται", "shadow_only": "μόνο καταγραφή"}


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


def _ny_time(stamp: Any) -> str | None:
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(NEW_YORK).strftime("%H:%M")
    except (TypeError, ValueError):
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


def _tiers():
    """(curve, shadow-only set) for the tier of each strategy; empty when unavailable (tier stays unknown)."""
    try:
        import holding_horizon
        import strategy_tiers

        return holding_horizon.load_curve(), strategy_tiers.shadow_only()
    except Exception:  # noqa: BLE001 - the report never fails on the tiers
        return None, set()


def _tier(playbook: Any, direction: Any, curve: Any, shadow: set[str]) -> str | None:
    if curve is None or not playbook or not direction:
        return None
    import strategy_tiers

    return strategy_tiers.tier(str(playbook), str(direction), curve, shadow)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def build(out: Path, today: date, spy_move: float | None) -> dict[str, Any]:
    preopen = _today(_load(out / "preopen" / "preopen_check_latest.json"), today, "checked_at")
    koulis = _today(_load(out / "koulis_order_intents_latest.json"), today, "generated_at")
    traders = {a: _today(_load(out / f"{a}_paper_trades_latest.json"), today, "run_timestamp") for a in AGENTS}
    miltos = _today(_load(out / "miltos_entry_stalker_latest.json"), today, "run_timestamp")
    monitors = {a: _today(_load(out / f"{a}_position_monitor_latest.json"), today, "run_timestamp") for a in AGENTS}
    multi = _today(_load(out / "multi_day_monitor_latest.json"), today, "run_timestamp")
    curve, shadow = _tiers()

    # every entry of the day, with who placed it and when (the trader at the start, Miltos later)
    entries, waiting, by_key = [], Counter(), {}
    for agent, payload in traders.items():
        for o in (payload or {}).get("openings") or []:
            if not isinstance(o, dict):
                continue
            order = o.get("refreshed_order_response") or o.get("order_response") or {}
            plan = o.get("paper_trade_plan") if isinstance(o.get("paper_trade_plan"), dict) else {}
            if str(o.get("order_status") or "").lower() == "filled" and order.get("filled_at"):
                row = {"agent": agent, "ticker": o.get("ticker"), "playbook": o.get("playbook_name"),
                       "direction": o.get("trade_direction"), "entry_time_ny": _ny_time(order.get("filled_at")),
                       "entered_by": "miltos" if o.get("miltos_activated_at") else "trader",
                       "holding_sessions": (plan.get("holding_horizon") or {}).get("sessions")
                       if plan.get("holding_period_rule") == "agent_horizon" else None,
                       "tier": _tier(o.get("playbook_name"), o.get("trade_direction"), curve, shadow)}
                entries.append(row)
                by_key[(agent, str(o.get("ticker")))] = row
            elif str(o.get("order_status") or "").lower() == "waiting_confirmation":
                checks = o.get("confirmation_checks") or {}
                failed = [k for k in CHECKS if checks.get(k) is False] or [k for k in CHECKS if checks.get(k) is None]
                waiting.update(failed or ["unknown"])

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
            owner = str(action.get("agent") or agent) if agent == "multi_day_monitor" else agent
            latest[(owner, str(action.get("ticker")))] = {**action, "_agent": owner}
    closes = []
    for action in latest.values():
        plpc = action.get("position_plpc")
        pct = None if plpc is None else round(float(plpc) * 100, 2)
        direction = str(action.get("trade_direction") or "")
        excess = None if pct is None or spy_move is None else round(
            pct - spy_move if direction == "long" else pct + spy_move, 2)
        entry = by_key.get((action["_agent"], str(action.get("ticker")))) or {}
        playbook = entry.get("playbook") or action.get("playbook_name")
        closes.append({"agent": action["_agent"], "ticker": action.get("ticker"), "direction": direction,
                       "playbook": playbook, "tier": entry.get("tier") or _tier(playbook, direction, curve, shadow),
                       "entry_time_ny": entry.get("entry_time_ny"), "exit_rule": action.get("exit_rule"),
                       "result_pct": pct, "approx_excess_vs_spy_pct": excess,
                       "status": action.get("action_status"), "reason": action.get("decision_reason")})
    known = [c["result_pct"] for c in closes if c["result_pct"] is not None]

    groups: dict[tuple, list[float]] = defaultdict(list)
    for c in closes:
        if c["result_pct"] is not None:
            groups[(c["agent"], c["playbook"], c["direction"], c["tier"])].append(c["result_pct"])
    per_strategy = [{"agent": a, "playbook": p, "direction": d, "tier": t, "closed": len(v), "average_pct": _mean(v),
                     "winners": sum(1 for x in v if x > 0)} for (a, p, d, t), v in sorted(groups.items(),
                                                                                         key=lambda kv: -len(kv[1]))]
    held = []
    registry = _load(out.parent / "data" / "paper_positions.json") or {}
    for h in (multi or {}).get("holds") or []:
        h = {**{"agent": None, "playbook_name": None, "trade_direction": None, "opened_session": None,
                "expires_at": None}, **{k: v for k, v in h.items() if v is not None}}
        reg = registry.get(str(h.get("ticker"))) or {}  # an older monitor file lacks these fields
        h["agent"] = h["agent"] or reg.get("agent")
        h["playbook_name"] = h["playbook_name"] or reg.get("playbook")
        h["trade_direction"] = h["trade_direction"] or reg.get("direction")
        h["opened_session"] = h["opened_session"] or reg.get("opened_session")
        h["expires_at"] = h["expires_at"] or reg.get("expires_at")
        plpc = h.get("position_plpc")
        held.append({"agent": h.get("agent"), "ticker": h.get("ticker"), "playbook": h.get("playbook_name"),
                     "direction": h.get("trade_direction"), "horizon_sessions": h.get("horizon_sessions"),
                     "sessions_held": h.get("sessions_held"), "opened_session": h.get("opened_session"),
                     "expires": str(h.get("expires_at") or "")[:10] or None,
                     "result_pct": None if plpc is None else round(float(plpc) * 100, 2),
                     "stop_pct": None if h.get("stop_pct") is None else round(float(h["stop_pct"]) * 100, 2)})
    blocked = Counter(str(b.get("block_reason") or "").split(":")[0] for b in (koulis or {}).get("blocked_intents") or [])
    approved_by = Counter((i.get("agent"), i.get("playbook_name"), i.get("trade_direction"))
                          for i in (koulis or {}).get("approved_intents") or [])
    hours = Counter((e["entry_time_ny"] or "??")[:2] for e in entries)
    return {
        "schema": "ecosystem.paper_session_report.v2", "session_date": today.isoformat(),
        "spy_open_to_close_pct": spy_move,
        "preopen": None if preopen is None else {"counts": preopen.get("counts"), "demoted": [
            r for r in preopen.get("rows") or [] if r.get("decision") == "demoted_to_watch"]},
        "koulis": None if koulis is None else {
            "approved_count": koulis.get("approved_count"), "approved_notional_usd": koulis.get("approved_notional_usd"),
            "blocked_count": koulis.get("blocked_count"), "prefiltered_count": koulis.get("prefiltered_count"),
            "gate_reason": koulis.get("gate_reason"),
            "approved_by_strategy": [{"agent": a, "playbook": p, "direction": d, "count": n}
                                     for (a, p, d), n in approved_by.most_common()],
            "blocked_reasons": dict(blocked.most_common()),
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
        "entries": {"total": len(entries), "by_trader_at_start": sum(1 for e in entries if e["entered_by"] == "trader"),
                    "by_miltos": sum(1 for e in entries if e["entered_by"] == "miltos"),
                    "held_on_horizon": sum(1 for e in entries if e["holding_sessions"]),
                    "by_hour_ny": dict(sorted(hours.items())), "list": entries},
        "waiting_reasons": dict(waiting.most_common()),
        "closed_positions": closes,
        "per_strategy": per_strategy,
        "held_positions": held,
        "day_result": {"positions": len(closes), "with_result": len(known),
                       "average_result_pct": _mean(known),
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
        top = k.get("approved_by_strategy") or []
        if top:
            lines.append("- Εγκρίσεις ανά στρατηγική: " + "; ".join(
                f"{i['agent']} {i['playbook']} {i['direction']} {i['count']}" for i in top))
        if k.get("blocked_reasons"):
            lines.append("- Λόγοι μπλοκαρίσματος: " + "; ".join(f"{r} {n}" for r, n in k["blocked_reasons"].items()))
    lines += ["", "## Είσοδοι της ημέρας"]
    e = report.get("entries") or {}
    lines.append(f"- Μπήκαν {e.get('total', 0)}: {e.get('by_trader_at_start', 0)} από τους traders στην αρχή, "
                 f"{e.get('by_miltos', 0)} από τον Miltos αργότερα · με διάρκεια agent {e.get('held_on_horizon', 0)}.")
    if e.get("by_hour_ny"):
        lines.append("- Ώρα εισόδου (Νέα Υόρκη): " + ", ".join(f"{h}:00 {n}" for h, n in e["by_hour_ny"].items()))
    for agent, t in report["traders"].items():
        lines.append(f"- {agent}: " + ("δεν υπάρχει σημερινή εγγραφή." if t is None else
                     f"εντολές στο τέλος της μέρας {v(t['submitted_count'])} (traders και Miltos μαζί), απέτυχαν "
                     f"{v(t['failed_count'])}, έμειναν χωρίς επιβεβαίωση {v(t['confirmation_blocked_count'])}."))
    m = report["miltos"]
    lines.append("- Miltos (όλη η μέρα): " + ("δεν υπάρχει σημερινή εγγραφή." if m is None else
                 f"μπήκαν {e.get('by_miltos', 0)} · {v(m['status'])}."))
    if report.get("waiting_reasons"):
        lines.append("- Τι κράτησε έξω όσες περίμεναν (στον τελευταίο έλεγχο της καθεμιάς): " + "; ".join(
            f"{CHECK_GR.get(r, r)} {n}" for r, n in report["waiting_reasons"].items()))
    lines += ["", "## Αποτελέσματα ανά στρατηγική (θέσεις που έκλεισαν)"]
    if report.get("per_strategy"):
        lines += ["| trader | στρατηγική | κατεύθυνση | επίπεδο | θέσεις | μέσο % | κερδισμένες |", "|---|---|---|---|---|---|---|"]
        for s in report["per_strategy"]:
            lines.append(f"| {s['agent']} | {v(s['playbook'])} | {s['direction']} | {TIER_GR.get(s['tier'], v(s['tier']))} | "
                         f"{s['closed']} | {v(s['average_pct'])} | {s['winners']} |")
    else:
        lines.append("- Καμία θέση δεν έκλεισε σήμερα.")
    lines += ["", "## Θέσεις που κρατάμε (διάρκεια agent)"]
    held = report.get("held_positions") or []
    if held:
        lines += ["| μετοχή | trader · στρατηγική | κατεύθ. | αποτέλεσμα % | συνεδρία / διάρκεια | stop % | λήξη |",
                  "|---|---|---|---|---|---|---|"]
        for h in sorted(held, key=lambda x: (x["result_pct"] is None, x["result_pct"] or 0)):
            lines.append(f"| {h['ticker']} | {h['agent']} · {v(h['playbook'])} | {h['direction']} | {v(h['result_pct'])} | "
                         f"{v(h['sessions_held'])} / {v(h['horizon_sessions'])} | {v(h['stop_pct'])} | {v(h['expires'])} |")
        values = [h["result_pct"] for h in held if h["result_pct"] is not None]
        mean = _mean(values)
        lines.append(f"\n{len(held)} θέσεις, μέσο αποτέλεσμα ως τώρα {'άγνωστο' if mean is None else f'{mean}%'}.")
    else:
        lines.append("- Καμία.")
    lines += ["", "## Θέσεις που έκλεισαν"]
    if not report["closed_positions"]:
        lines.append("- Καμία θέση δεν έκλεισε σήμερα (ή δεν άνοιξε καμία).")
    else:
        lines += ["| trader | μετοχή | στρατηγική | κατεύθυνση | είσοδος NY | αποτέλεσμα % | ≈ έναντι SPY % | κατάσταση |",
                  "|---|---|---|---|---|---|---|---|"]
        for c in report["closed_positions"]:
            lines.append(f"| {c['agent']} | {c['ticker']} | {v(c.get('playbook'))} | {c['direction']} | "
                         f"{v(c.get('entry_time_ny'))} | {v(c['result_pct'])} | {v(c['approx_excess_vs_spy_pct'])} | "
                         f"{v(c['status'])} |")
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
