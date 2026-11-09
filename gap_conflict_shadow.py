"""Two pre-open questions measured in shadow, no order (owner 2026-11-09, after the NVDA short of that day).

On 2026-11-09 Yankos had two candidates on NVDA from the same night: a support-bounce long and a gap-fade short. The
pre-open check took the long down (the -5.85% pre-market gap was against it by more than one ATR) and kept the short,
because it only looks at gaps against a trade. The short entered near the day's low and the stock rallied with the
sector. Two things are measured on every session so far:

* ``favourable_gap``: a candidate whose pre-market gap is in its own direction by at least one 14-day ATR (the move
  may already be spent), against candidates with a smaller gap. The gap is the pre-open check's own reading
  (``preopen.gap_pct``); the ATR is rebuilt from the stored daily bars before the session (simple 14-day mean of the
  true range over the last close), so it approximates the board's ATR. Unknown gap or ATR: left out, counted.
* ``both_directions``: a stock on which the same trader proposed a long and a short the same night (any record kind:
  a side taken down before the open still counts as proposed), against stocks with one direction. Both sides are
  also shown on their own.

Outcomes are the learning evaluation's (a trade's real result, otherwise the replayed or open-to-close one), on trades
and shadow candidates. When one group does clearly worse (mean at least the control margin lower, both groups with
the minimum trades and days) a lesson candidate is proposed; nothing changes the checks, Koulis or the orders, and
accepting it stays the owner's decision.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd

ATR_SESSIONS = 14
GAP_ATR_MULTIPLE = 1.0     # the pre-open check's own threshold for a gap against the trade
KINDS = ("trade", "shadow_candidate")

Frames = Callable[[str], "pd.DataFrame | None"]
Metrics = Callable[[list[dict[str, Any]]], dict[str, Any]]


def atr_pct(frame: pd.DataFrame | None, session: date) -> float | None:
    """The 14-day ATR in % of the last close before ``session``; None without 15 stored sessions before it."""
    if frame is None:
        return None
    before = frame[frame.index.date < session]
    if len(before) < ATR_SESSIONS + 1:
        return None
    high, low, close = (before[k].to_numpy(float) for k in ("High", "Low", "Close"))
    ranges = [max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
              for i in range(len(before) - ATR_SESSIONS, len(before))]
    last = close[-1]
    return round(sum(ranges) / ATR_SESSIONS / last * 100, 4) if last > 0 else None


def gap_reading(record: dict[str, Any], frames: Frames) -> dict[str, Any]:
    """The pre-market gap in the trade's own direction, in ATRs; unknown when the gap or the ATR is."""
    gap = (record.get("preopen") or {}).get("gap_pct")
    try:
        gap = float(gap)
        session = date.fromisoformat(str(record["session_date"]))
    except (TypeError, ValueError, KeyError):
        return {"known": False}
    atr = atr_pct(frames(str(record.get("ticker") or "")), session)
    if not atr:
        return {"known": False}
    signed = gap if record.get("direction") == "long" else -gap
    return {"known": True, "signed_gap_pct": round(signed, 3), "atr_pct": atr, "in_atr": round(signed / atr, 3),
            "favourable_big": signed >= GAP_ATR_MULTIPLE * atr}


def both_directions(records: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    """(session, agent, ticker) where the same trader proposed both a long and a short that night."""
    sides: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for r in records:
        sides[(str(r.get("session_date")), str(r.get("agent")), str(r.get("ticker")))].add(str(r.get("direction")))
    return {key for key, found in sides.items() if {"long", "short"} <= found}


def _verdict(flagged: dict[str, Any], rest: dict[str, Any], margin_pct: float) -> tuple[float | None, str]:
    gap = (None if flagged["mean_pct"] is None or rest["mean_pct"] is None
           else round(flagged["mean_pct"] - rest["mean_pct"], 3))
    if flagged["status"] != "judged" or rest["status"] != "judged":
        return gap, "insufficient_sample"
    if gap is not None and gap <= -margin_pct:
        return gap, "flagged did worse"
    if gap is not None and gap >= margin_pct:
        return gap, "flagged did better"
    return gap, "no clear difference"


def evaluate(records: list[dict[str, Any]], frames: Frames, metrics: Metrics, margin_pct: float) -> dict[str, Any]:
    scored = [r for r in records if r.get("kind") in KINDS]
    # favourable gap
    big, small, unknown = [], [], 0
    for r in scored:
        reading = gap_reading(r, frames)
        if not reading["known"]:
            unknown += 1
            continue
        (big if reading["favourable_big"] else small).append(r)
    gap_m, rest_m = metrics(big), metrics(small)
    gap_diff, gap_verdict = _verdict(gap_m, rest_m, margin_pct)
    # both directions (proposed in any record kind, judged on trades and shadow candidates)
    conflicted = both_directions(records)
    mixed = [r for r in scored if (str(r["session_date"]), str(r["agent"]), str(r["ticker"])) in conflicted]
    single = [r for r in scored if (str(r["session_date"]), str(r["agent"]), str(r["ticker"])) not in conflicted]
    mix_m, single_m = metrics(mixed), metrics(single)
    mix_diff, mix_verdict = _verdict(mix_m, single_m, margin_pct)
    return {"schema": "ecosystem.gap_conflict_shadow.v1",
            "favourable_gap": {"flagged": gap_m, "rest": rest_m, "unknown": unknown, "flagged_minus_rest_pct": gap_diff,
                               "by_direction": {d: metrics([r for r in big if r.get("direction") == d])
                                                for d in ("long", "short")},
                               "verdict": gap_verdict},
            "both_directions": {"stocks": len(conflicted), "sessions": len({k[0] for k in conflicted}),
                                "flagged": mix_m, "rest": single_m, "flagged_minus_rest_pct": mix_diff,
                                "by_direction": {d: metrics([r for r in mixed if r.get("direction") == d])
                                                 for d in ("long", "short")},
                                "verdict": mix_verdict},
            "active_decisions_changed": False}


def proposal(result: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    gap = result["favourable_gap"]
    if gap["verdict"] == "flagged did worse":
        out.append({"target_agent": "preopen_check", "criterion_id": "favourable_gap_spent",
                    "proposed_change": "A candidate whose pre-market gap is in its own direction by at least one ATR "
                                       "goes from candidate to watch at the pre-open check (the move may be spent).",
                    "support": gap["flagged"]["known"], "evidence": gap})
    mix = result["both_directions"]
    if mix["verdict"] == "flagged did worse":
        out.append({"target_agent": "koulis", "criterion_id": "same_trader_both_directions",
                    "proposed_change": "When one trader proposes both a long and a short on the same stock the same "
                                       "night, neither side is sent (the reading is not clear).",
                    "support": mix["flagged"]["known"], "evidence": mix})
    return out


def render(result: dict[str, Any]) -> str:
    def v(x: Any) -> str:
        return "άγνωστο" if x is None else str(x)

    def row(name: str, m: dict[str, Any]) -> str:
        return (f"| {name} | {m['known']} | {m['days']} | {v(m['mean_pct'])} | {v(m['mean_excess_pct'])} | "
                f"{v(m['beat_spy_pct'])} | {m['status']} |")
    head = ["| ομάδα | με αποτέλεσμα | μέρες | μέσο % | υπέρβαση SPY % | νίκησαν SPY % | κατάσταση |",
            "|---|---|---|---|---|---|---|"]
    gap, mix = result["favourable_gap"], result["both_directions"]
    lines = ["# Κενό υπέρ της θέσης και long + short στην ίδια μετοχή (shadow, χωρίς εντολές)", "",
             "Μετρήσεις μόνο· δεν αλλάζουν τον έλεγχο πριν το άνοιγμα, τον Koulis ή τις εντολές.", "",
             "## Κενό πριν το άνοιγμα υπέρ της θέσης, τουλάχιστον 1 ATR", "",
             f"Συμπέρασμα: **{gap['verdict']}** · διαφορά μέσου {v(gap['flagged_minus_rest_pct'])}% · "
             f"χωρίς γνωστό κενό ή ATR {gap['unknown']}.", "", *head,
             row("κενό υπέρ ≥ 1 ATR", gap["flagged"]), row("μικρότερο κενό", gap["rest"]),
             row("  από αυτά long", gap["by_direction"]["long"]), row("  από αυτά short", gap["by_direction"]["short"]),
             "", "## Ο ίδιος trader με long και short στην ίδια μετοχή την ίδια νύχτα", "",
             f"Μετοχές {mix['stocks']} σε {mix['sessions']} συνεδρίες · συμπέρασμα: **{mix['verdict']}** · "
             f"διαφορά μέσου {v(mix['flagged_minus_rest_pct'])}%.", "", *head,
             row("και οι δύο κατευθύνσεις", mix["flagged"]), row("μία κατεύθυνση", mix["rest"]),
             row("  η πλευρά long", mix["by_direction"]["long"]), row("  η πλευρά short", mix["by_direction"]["short"])]
    return "\n".join(lines) + "\n"


def run(root: Path, records: list[dict[str, Any]], frames: Frames, metrics: Metrics, margin_pct: float, *,
        write: bool = True) -> dict[str, Any]:
    result = evaluate(records, frames, metrics, margin_pct)
    if write:
        out = root / "output" / "paper_learning"
        out.mkdir(parents=True, exist_ok=True)
        (out / "gap_conflict_shadow_latest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                                             encoding="utf-8")
        (out / "gap_conflict_shadow_latest.md").write_text(render(result), encoding="utf-8")
    return result
