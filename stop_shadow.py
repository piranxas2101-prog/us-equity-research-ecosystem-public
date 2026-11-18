"""The stop of a held paper position, in shadow: the live widening stop against the class-wide stop. No order.

Owner's decision 2026-11-15. The position monitor (``alpaca_multi_day_monitor.py``) closes a held position on
``thesis_exit.scaled_stop(atr, held)``: 1.1 x ATR(14) x sqrt(sessions held so far), so a 60-session position starts
with a one-session stop (often 2-3%) that widens slowly. The backtest of ``thesis_exit.py`` uses the stop of the
whole class from the first session (sqrt(60) for a quarter). A five-year replay of 4000 support_bounce_swing longs
held 60 sessions (2026-11-15) gave the class-wide stop more edge against a random entry (+3.5 against +2.7 points,
62% against 48% wins, stopped 17% against 38%). This measures the two rules on the real paper positions before
anything changes:

* the positions: every held position the monitor registered (``data/paper_positions.json`` still open,
  ``data/paper_positions_closed.jsonl`` closed) with its own entry price, ATR(14) at entry and planned sessions;
* replayed on the daily bars of the price store from the entry session to the last planned session: ``live``
  (the widening stop, session k of the hold uses sqrt(k)) and ``class`` (sqrt(planned sessions) from the start).
  A session opening beyond the stop exits at its open; a touch exits at the stop; otherwise out at the last close.
  The entry session's whole range counts (the bar cannot tell what came before the fill) - the same for both rules.
  Only the stop is replayed: the thesis, results and market-shock exits are left out of both;
* judged only on positions whose whole window is stored (the maturity rule of ``paper_learning.py``): before that
  the class rule's stopped positions would be its worst ones. The difference class minus live per position, with a
  95% interval resampling whole entry sessions, at least ``MIN_TRADES`` positions from ``MIN_DAYS`` entry days.
  Before that the positions still running are shown: stopped so far under each rule, and where the rules parted.

**The other exits** (owner 2026-11-15): a position the monitor closed for any other reason - an abrupt change of
the market's mood (``market_shock``, 64 quarter positions by 2026-11-13), a broken thesis, results ahead, the
owner - is set against the same position held on under the live stop alone (the ``live`` replay above): the real
result at its closing fill (the decision's price while the fill is not read) against the held-on one, per exit
reason, judged the same way on whole windows only.

A missing ATR or entry price leaves the position unknown, never a default. A lesson candidate is only proposed; the
owner alone decides, and the wider stop carries a larger loss per stopped position (shown beside the verdict).
"""
from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
import statistics
from typing import Any, Callable

import pandas as pd

import thesis_exit as te

RULES = ("live", "class")
NOT_COMPARED = {"scaled_stop", "stop_loss", "horizon_end"}  # the stop itself, and the planned end (nothing to compare)
EXIT_VERDICT_GR = {"insufficient_sample": "ανεπαρκές δείγμα", "holding on did better": "το κράτημα θα πήγαινε καλύτερα",
                   "the exit did better": "η έξοδος πήγε καλύτερα", "no clear difference": "καμία σαφής διαφορά"}
MIN_TRADES = 30
MIN_DAYS = 10
# Pre-registered (owner 2026-11-18, RULES_REVIEW section 14): F4 is judged once - when 60 positions have completed
# their window, or on the deadline, whichever comes first - not again on every pass from the 30th on (each look is
# another chance to pass by luck). Before that the numbers are shown and the verdict is "collecting".
JUDGE_AT = 60
DEADLINE = date(2026, 12, 29)
VERDICT_GR = {"collecting": f"μαζεύει δείγμα (κρίση στις {JUDGE_AT} ολοκληρωμένες θέσεις ή στις {DEADLINE.isoformat()})",
              "deadline_insufficient": "έφτασε η προθεσμία χωρίς αρκετό δείγμα (απόφαση του ιδιοκτήτη)",
              "insufficient_sample": "ανεπαρκές δείγμα (περιμένει να ολοκληρωθούν τα παράθυρα)",
              "class stop did better": "το φαρδύ stop πήγε καλύτερα",
              "live stop did better": "το σημερινό stop πήγε καλύτερα",
              "no clear difference": "καμία σαφής διαφορά"}

Frames = Callable[[str], "pd.DataFrame | None"]


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def positions(open_path: Path, closed_path: Path) -> list[dict[str, Any]]:
    """The held positions the monitor registered, open and closed, once each."""
    rows: list[dict[str, Any]] = []
    if open_path.is_file():
        payload = json.loads(open_path.read_text(encoding="utf-8-sig") or "{}")  # symbol -> position
        items = payload.values() if isinstance(payload, dict) else payload
        rows += [dict(p, state="open") for p in items or [] if isinstance(p, dict)]
    if closed_path.is_file():
        for line in closed_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(dict(json.loads(line), state="closed"))
    seen, out = set(), []
    for p in rows:
        key = (p.get("agent"), p.get("symbol"), p.get("direction"), p.get("opened_session"), p.get("filled_at"))
        sessions = p.get("horizon_sessions")
        if key in seen or not isinstance(sessions, int) or sessions <= 1 or not p.get("opened_session"):
            continue
        seen.add(key)
        out.append(p)
    return out


def replay(frame: pd.DataFrame, session: date, entry: float, atr_pct: float, sessions: int, direction: str,
           rule: str) -> dict[str, Any] | None:
    """One position under ``rule``; ``final`` False while it is still running on the stored bars. None without the
    entry session's bar."""
    dates = frame.index.date
    start = int((dates < session).sum())
    if start >= len(frame) or dates[start] != session:
        return None
    o, h, low, c = (frame[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    long, end = direction == "long", start + sessions - 1
    last = min(end, len(frame) - 1)
    for j in range(start, last + 1):
        stop = te.scaled_stop(atr_pct, j - start + 1 if rule == "live" else sessions)
        level = entry * (1 - stop) if long else entry * (1 + stop)
        if j > start and ((o[j] <= level) if long else (o[j] >= level)):
            price, how = o[j], "stop_gap"
        elif (low[j] <= level) if long else (h[j] >= level):
            price, how = level, "stop"
        else:
            continue
        return {"exit": how, "exit_date": str(dates[j]), "sessions_held": j - start + 1, "final": True,
                "result_pct": round((1.0 if long else -1.0) * (price / entry - 1) * 100, 3)}
    result = round((1.0 if long else -1.0) * (c[last] / entry - 1) * 100, 3)
    return {"exit": "time" if last == end else "running", "exit_date": str(dates[last]),
            "sessions_held": last - start + 1, "final": last == end, "result_pct": result}


def real_result(position: dict[str, Any], fills: dict[str, float | None]) -> tuple[float | None, str | None]:
    """A closed position's own result: at its closing fill when read, else at the decision (``exit_plpc``)."""
    entry, fill = _float(position.get("entry_price")), _float(fills.get(str(position.get("exit_order_id") or "")))
    sign = 1.0 if position.get("direction") == "long" else -1.0
    if entry and fill:
        return round(sign * (fill / entry - 1) * 100, 3), "fill"
    plpc = _float(position.get("exit_plpc"))
    return (None, None) if plpc is None else (round(plpc * 100, 3), "decision")


def build(cases: list[dict[str, Any]], frames: Frames,
          fills: dict[str, float | None] | None = None) -> list[dict[str, Any]]:
    out = []
    for p in cases:
        entry, atr = _float(p.get("entry_price")), _float(p.get("atr_14_pct"))
        closed = p.get("state") == "closed"
        real, basis = real_result(p, fills or {}) if closed else (None, None)
        row = {"agent": p.get("agent"), "symbol": p.get("symbol"), "playbook": p.get("playbook"),
               "direction": p.get("direction"), "holding_class": p.get("holding_class"),
               "session_date": p.get("opened_session"), "sessions": p["horizon_sessions"], "atr_14_pct": atr,
               "state": p.get("state"), "real_exit": p.get("close_reason") if closed else None,
               "real_result_pct": real, "real_basis": basis,
               "live": None, "class": None, "known": False, "mature": False}
        frame = frames(str(p.get("symbol") or ""))
        if entry and atr and atr > 0 and frame is not None and len(frame):
            session = date.fromisoformat(str(p["opened_session"]))
            for rule in RULES:
                row[rule] = replay(frame, session, entry, atr, p["horizon_sessions"], p.get("direction") or "long", rule)
            row["known"] = row["live"] is not None and row["class"] is not None
            # the whole planned window is stored: both rules are final, whichever stopped first
            row["mature"] = row["known"] and int((frame.index.date >= session).sum()) >= p["horizon_sessions"]
        out.append(row)
    return out


def _cluster_ci(diffs: list[float], days: list[str]) -> list[float] | None:
    return te._cluster_ci(pd.Series(diffs), pd.Series(days))


def _side(rows: list[dict[str, Any]], rule: str) -> dict[str, Any]:
    results = [r[rule]["result_pct"] for r in rows]
    stopped = [r[rule]["result_pct"] for r in rows if r[rule]["exit"] in ("stop", "stop_gap")]
    return {"positions": len(rows), "mean_pct": round(statistics.fmean(results), 3) if results else None,
            "win_rate_pct": round(100 * sum(x > 0 for x in results) / len(results), 1) if results else None,
            "stopped": len(stopped), "stopped_mean_pct": round(statistics.fmean(stopped), 3) if stopped else None,
            "worst_pct": min(results) if results else None,
            "mean_sessions_held": round(statistics.fmean(r[rule]["sessions_held"] for r in rows), 1) if rows else None}


def evaluate(rows: list[dict[str, Any]], margin_pct: float, today: date | None = None) -> dict[str, Any]:
    known = [r for r in rows if r["known"]]
    mature = [r for r in known if r["mature"]]
    running = [r for r in known if not r["mature"]]
    diffs = [r["class"]["result_pct"] - r["live"]["result_pct"] for r in mature]
    days = [r["session_date"] for r in mature]
    mean, ci, verdict = _paired_verdict(diffs, days, margin_pct, "class stop did better", "live stop did better")
    deadline = today is not None and today >= DEADLINE
    if len(mature) < JUDGE_AT and not deadline:
        verdict = "collecting"
    elif verdict == "insufficient_sample" and deadline:
        verdict = "deadline_insufficient"
    parted = [r for r in running if r["live"]["exit"] in ("stop", "stop_gap")
              and r["class"]["exit"] not in ("stop", "stop_gap")]
    return {"schema": "ecosystem.stop_shadow.v1", "positions": len(rows), "unknown": len(rows) - len(known),
            "exits": evaluate_exits(rows, margin_pct),
            "mature": len(mature), "mature_days": len(set(days)), "running": len(running),
            "judged": {"live": _side(mature, "live"), "class": _side(mature, "class"),
                       "class_minus_live_pct": mean, "ci_95": ci},
            "running_view": {"live": _side(running, "live"), "class": _side(running, "class"),
                             "parted": len(parted),
                             "parted_live_mean_pct": round(statistics.fmean(r["live"]["result_pct"] for r in parted), 3)
                             if parted else None,
                             "parted_class_now_mean_pct": round(statistics.fmean(r["class"]["result_pct"] for r in parted), 3)
                             if parted else None},
            "judge_at": JUDGE_AT, "deadline": DEADLINE.isoformat(),
            "verdict": verdict, "rows": rows, "active_decisions_changed": False}


def _paired_verdict(diffs: list[float], days: list[str], margin_pct: float,
                    better: str, worse: str) -> tuple[float | None, list[float] | None, str]:
    mean = round(statistics.fmean(diffs), 3) if diffs else None
    ci = _cluster_ci(diffs, days) if diffs else None
    if len(diffs) < MIN_TRADES or len(set(days)) < MIN_DAYS or ci is None:
        return mean, ci, "insufficient_sample"
    return mean, ci, (better if ci[0] > 0 and mean >= margin_pct else
                      worse if ci[1] < 0 and mean <= -margin_pct else "no clear difference")


def evaluate_exits(rows: list[dict[str, Any]], margin_pct: float) -> dict[str, Any]:
    """Per exit reason: the real result against the same position held on under the live stop alone."""
    out: dict[str, Any] = {}
    compared = [r for r in rows if r["known"] and r.get("real_exit") and r["real_exit"] not in NOT_COMPARED
                and r.get("real_result_pct") is not None]

    def avg(items: list[dict[str, Any]], held_on: bool) -> float | None:
        values = [r["live"]["result_pct"] if held_on else r["real_result_pct"] for r in items]
        return round(statistics.fmean(values), 3) if values else None

    for reason in sorted({r["real_exit"] for r in compared}):
        members = [r for r in compared if r["real_exit"] == reason]
        mature = [r for r in members if r["mature"]]
        running = [r for r in members if not r["mature"]]
        diffs = [r["live"]["result_pct"] - r["real_result_pct"] for r in mature]
        mean, ci, verdict = _paired_verdict(diffs, [r["session_date"] for r in mature], margin_pct,
                                            "holding on did better", "the exit did better")
        out[reason] = {"positions": len(members), "mature": len(mature),
                       "mature_days": len({r["session_date"] for r in mature}), "running": len(running),
                       "at_fill": sum(r["real_basis"] == "fill" for r in members),
                       "real_mean_pct": avg(mature, False), "held_on_mean_pct": avg(mature, True),
                       "held_on_minus_real_pct": mean, "ci_95": ci, "verdict": verdict,
                       "running_real_mean_pct": avg(running, False), "running_held_on_now_pct": avg(running, True)}
    return out


def proposal(result: dict[str, Any]) -> list[dict[str, Any]]:
    found = []
    for reason, cell in (result.get("exits") or {}).items():
        if cell["verdict"] == "holding on did better":
            found.append({"target_agent": "position_monitor", "criterion_id": f"held_position_exit:{reason}",
                          "proposed_change": f"Stop closing held positions on '{reason}': held on under the stop alone "
                                             f"they did {cell['held_on_minus_real_pct']}% better per position (95% "
                                             f"interval {cell['ci_95']}, {cell['mature']} whole windows).",
                          "support": cell["mature"], "evidence": cell})
    if result.get("verdict") != "class stop did better":
        return found
    judged = result["judged"]
    return found + [{"target_agent": "position_monitor", "criterion_id": "held_position_class_stop",
             "proposed_change": "Give a held position the stop of its whole planned hold from the first session "
                                "(1.1 x ATR x sqrt(planned sessions)) instead of the stop that widens with the sessions "
                                f"held. Class minus live {judged['class_minus_live_pct']}% per position, 95% interval "
                                f"{judged['ci_95']}; a stopped position loses {judged['class']['stopped_mean_pct']}% "
                                f"on average under the wider stop.",
             "support": result["mature"], "evidence": {k: result[k] for k in ("judged", "mature", "mature_days")}}]


def render(result: dict[str, Any]) -> str:
    def v(x: Any) -> str:
        return "άγνωστο" if x is None else str(x)

    def table(view: dict[str, Any]) -> list[str]:
        lines = ["| κανόνας | θέσεις | μέσο % | κερδισμένα % | κόπηκαν | μέσο όσων κόπηκαν % | χειρότερο % | μέσες συνεδρίες |",
                 "|---|---|---|---|---|---|---|---|"]
        for rule, title in (("live", "σημερινό (ανοίγει με τις συνεδρίες)"), ("class", "φαρδύ από την αρχή")):
            s = view[rule]
            lines.append(f"| {title} | {s['positions']} | {v(s['mean_pct'])} | {v(s['win_rate_pct'])} | {s['stopped']} | "
                         f"{v(s['stopped_mean_pct'])} | {v(s['worst_pct'])} | {v(s['mean_sessions_held'])} |")
        return lines

    j, run = result["judged"], result["running_view"]
    lines = ["# Το stop των θέσεων πολλών ημερών (shadow, χωρίς εντολές)", "",
             "Κάθε πραγματική θέση πολλών ημερών ξαναπαίζεται στις ημερήσιες μπάρες με δύο stop: το σημερινό του monitor "
             "(1.1×ATR×√συνεδρίες που πέρασαν) και το φαρδύ της διάρκειας από την πρώτη μέρα (1.1×ATR×√προγραμματισμένες "
             "συνεδρίες). Μόνο το stop· οι έξοδοι thesis, αποτελεσμάτων και σοκ αγοράς δεν υπολογίζονται. "
             "Τίποτα δεν αλλάζει στις εντολές.", "",
             f"Θέσεις {result['positions']} · χωρίς γνωστό ATR/τιμή {result['unknown']} · ολοκληρωμένα παράθυρα "
             f"{result['mature']} από {result['mature_days']} μέρες εισόδου · σε εξέλιξη {result['running']}.", "",
             f"Συμπέρασμα: **{VERDICT_GR.get(result['verdict'], result['verdict'])}** · φαρδύ μείον σημερινό "
             f"{v(j['class_minus_live_pct'])}% ανά θέση, 95% διάστημα {v(j['ci_95'])}. Κρίση μόνο με ≥{MIN_TRADES} "
             f"ολοκληρωμένες θέσεις από ≥{MIN_DAYS} μέρες.", "", "## Ολοκληρωμένα παράθυρα (κρίνονται)", ""]
    lines += table(j)
    lines += ["", "## Σε εξέλιξη (ενημέρωση, όχι κρίση)", "",
              "Τα αποτελέσματα των θέσεων που τρέχουν είναι στο τελευταίο κλείσιμο της αποθήκης τιμών.", ""]
    lines += table(run)
    lines += ["", f"- Εκεί που χώρισαν οι κανόνες (κόπηκε με το σημερινό, κρατά με το φαρδύ): {run['parted']} θέσεις · "
                  f"σημερινό {v(run['parted_live_mean_pct'])}% · φαρδύ ως τώρα {v(run['parted_class_now_mean_pct'])}%.",
              "", "Το φαρδύ stop κόβει λιγότερες θέσεις αλλά με μεγαλύτερη ζημιά η καθεμία· η στήλη «μέσο όσων κόπηκαν» "
                  "δείχνει πόση. Η απόφαση είναι του ιδιοκτήτη.",
              "", "## Οι άλλες έξοδοι: τι θα γινόταν αν η θέση έμενε ανοιχτή", "",
              "Το πραγματικό αποτέλεσμα (στην εκτέλεση της εντολής κλεισίματος, ή στην τιμή της απόφασης όσο η εκτέλεση "
              "δεν έχει διαβαστεί) απέναντι στην ίδια θέση κρατημένη ως το τέλος του παραθύρου μόνο με το σημερινό stop. "
              "Κρίνονται μόνο ολοκληρωμένα παράθυρα.", "",
              "| έξοδος | θέσεις | στην εκτέλεση | ολοκληρωμένα | πραγματικό % | αν κρατούσε % | διαφορά % | 95% διάστημα | "
              "σε εξέλιξη: πραγματικό % / αν κρατούσε ως τώρα % | συμπέρασμα |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for reason, c in (result.get("exits") or {}).items():
        lines.append(f"| {reason} | {c['positions']} | {c['at_fill']} | {c['mature']} ({c['mature_days']} μέρες) | "
                     f"{v(c['real_mean_pct'])} | {v(c['held_on_mean_pct'])} | {v(c['held_on_minus_real_pct'])} | "
                     f"{v(c['ci_95'])} | {v(c['running_real_mean_pct'])} / {v(c['running_held_on_now_pct'])} | "
                     f"{EXIT_VERDICT_GR.get(c['verdict'], c['verdict'])} |")
    return "\n".join(lines) + "\n"


def run(root: Path, frames: Frames, margin_pct: float, *, fills: dict[str, float | None] | None = None,
        write: bool = True) -> dict[str, Any]:
    cases = positions(root / "data" / "paper_positions.json", root / "data" / "paper_positions_closed.jsonl")
    from zoneinfo import ZoneInfo

    result = evaluate(build(cases, frames, fills), margin_pct,
                      today=datetime.now(ZoneInfo("America/New_York")).date())
    if write:
        out = root / "output" / "paper_learning"
        out.mkdir(parents=True, exist_ok=True)
        (out / "stop_shadow_latest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "stop_shadow_latest.md").write_text(render(result), encoding="utf-8")
    return result
