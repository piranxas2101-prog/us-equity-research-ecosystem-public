"""Pre-open recheck at the open: would the ideas the pre-open check took down have come back, and how would they
have done? Research only (owner's decision 2026-10-22); kept in its own report so it is not mixed with the
learning evaluation of what really happened.

The pre-open check (09:00 New York) can only take a candidate down to watch, on two grounds: a pre-market gap
against the trade larger than 1 x ATR(14), or a new content-bearing 8-K the fundamental review has not read. On
2026-10-21 VRTX was taken down for a pre-market gap of -2.85% but opened only -0.57% and rose 2.5%: the thin
pre-market price misled. The rule tested here looks again with the official opening price of the price store:

* gap reason: measured again at the open; the idea comes back unless the opening gap is still against the trade
  by more than ``GAP_ATR`` x ATR(14);
* 8-K reason: a material item (results, agreements, acquisitions, officer changes, delisting, non-reliance ...)
  brings the idea back only when the market's opening reaction against the trade is at most ``MATERIAL_ATR`` x
  ATR (VRTX 2026-10-21: an officer change, open -0.57% = 0.24 x ATR, then +2.5%); a supporting item
  (Reg FD, other events, exhibits, votes) brings it back unless the opening gap is against it by more than
  ``GAP_ATR`` x ATR;
* both reasons: both tests must pass. An opening price, previous close or ATR that is not known keeps the idea
  on watch (unknown stays unknown, never a reason to trade).

How the restored ideas would have done is the shadow-trade replay already in the ledger (``paper_simulation.py``,
15-minute bars, Miltos's confirmation and each idea's own limits). Nothing here changes an order; a lesson
candidate is proposed only with judged evidence and the owner decides.
"""
from __future__ import annotations

from datetime import date
import re
from typing import Any, Callable

import numpy as np
import pandas as pd

GAP_ATR = 1.0
MATERIAL_ATR = 0.5
MATERIAL = frozenset({"1.01", "1.02", "1.03", "2.01", "2.02", "2.03", "2.05", "2.06", "3.01", "4.01", "4.02",
                      "5.01", "5.02"})
ITEM = re.compile(r"8-K(?:/A)? item (\d\.\d\d)")


def filing_items(reasons: list[str]) -> list[str]:
    return sorted({m for text in reasons for m in ITEM.findall(text)})


def opening_view(frame: pd.DataFrame | None, session: date) -> dict[str, float | None]:
    """Official open of ``session``, the previous close and ATR(14) % up to the previous session."""
    empty = {"open": None, "previous_close": None, "atr_pct": None}
    if frame is None:
        return empty
    dates = frame.index.date
    today, before = frame[dates == session], frame[dates < session].tail(15)
    if today.empty or len(before) < 15:
        return empty
    high, low, close = (before[k].to_numpy(float) for k in ("High", "Low", "Close"))
    tr = np.maximum(high[1:] - low[1:], np.maximum(abs(high[1:] - close[:-1]), abs(low[1:] - close[:-1])))
    previous = float(close[-1])
    return {"open": float(today["Open"].iloc[0]), "previous_close": previous,
            "atr_pct": float(tr.mean() / previous * 100) if previous > 0 else None}


def recheck(direction: str, reasons: list[str], view: dict[str, float | None]) -> dict[str, Any]:
    """The decision at the open for one idea taken down before it."""
    opened, previous, atr = view.get("open"), view.get("previous_close"), view.get("atr_pct")
    if not (opened and previous and atr):
        return {"decision": "stays_watch", "why": "opening price, previous close or ATR unknown"}
    gap = (opened / previous - 1) * 100
    against = gap < 0 if direction == "long" else gap > 0
    against_size = abs(gap) / atr if against else 0.0
    items = filing_items(reasons)
    gap_reason = any("Pre-market gap" in text for text in reasons)
    tests = []
    if gap_reason:
        tests.append(("gap", against_size <= GAP_ATR))
    if items:
        material = [i for i in items if i in MATERIAL]
        tests.append(("filing", against_size <= (MATERIAL_ATR if material else GAP_ATR)))
    if not tests:
        return {"decision": "stays_watch", "why": "reason not recognised", "opening_gap_pct": round(gap, 3)}
    passed = all(ok for _, ok in tests)
    why = ", ".join(f"{name} {'ok' if ok else 'still against'}" for name, ok in tests)
    return {"decision": "restored" if passed else "stays_watch", "why": why, "opening_gap_pct": round(gap, 3),
            "atr_pct": round(atr, 3), "items": items}


def evaluate(records: list[dict[str, Any]], frames: Callable[[str], pd.DataFrame | None],
             metrics: Callable[[list[dict[str, Any]]], dict[str, Any]], margin_pct: float) -> dict[str, Any]:
    """Every idea the pre-open check took down, rechecked at the open, with its simulated outcome
    (``outcome_pct`` / ``excess_pct`` as attached by the learning evaluation)."""
    rows = []
    for record in records:
        if record.get("kind") != "preopen_demoted" or record.get("direction") not in ("long", "short"):
            continue
        view = opening_view(frames(record["ticker"]), date.fromisoformat(record["session_date"]))
        decision = recheck(record["direction"], (record.get("preopen") or {}).get("reasons") or [], view)
        rows.append({"session_date": record["session_date"], "agent": record.get("agent"),
                     "ticker": record["ticker"], "playbook": record.get("playbook"), "direction": record["direction"],
                     "premarket_gap_pct": (record.get("preopen") or {}).get("gap_pct"), **decision,
                     "outcome_pct": record.get("outcome_pct"), "excess_pct": record.get("excess_pct"),
                     "outcome_source": record.get("outcome_source")})
    restored = [r for r in rows if r["decision"] == "restored"]
    stayed = [r for r in rows if r["decision"] != "restored"]
    traded = [r for r in records if r.get("kind") == "trade"]
    # "all": none taken down at all, Miltos's 15-minute confirmation alone decides - the other variant to watch
    summary = {"restored": metrics(restored), "stayed_watch": metrics(stayed), "all": metrics(rows),
               "traded": metrics(traded)}
    a, b = summary["restored"], summary["stayed_watch"]
    judged = a["status"] == "judged" and b["status"] == "judged"
    better = (judged and a["mean_excess_pct"] is not None and b["mean_excess_pct"] is not None
              and a["mean_excess_pct"] - b["mean_excess_pct"] >= margin_pct
              and a["beat_spy_pct"] is not None and a["beat_spy_pct"] > 50)
    verdict = ("insufficient_sample" if not judged else
               "restoring at the open would have helped" if better else "no clear gain")
    return {"schema": "ecosystem.preopen_recheck.v1", "rule": {"gap_atr": GAP_ATR, "material_items": sorted(MATERIAL)},
            "ideas": len(rows), "restored": len(restored), "summary": summary, "verdict": verdict, "rows": rows,
            "active_decisions_changed": False}


def proposal(result: dict[str, Any]) -> list[dict[str, Any]]:
    if result.get("verdict") != "restoring at the open would have helped":
        return []
    return [{"target_agent": "preopen", "criterion_id": "preopen_recheck_at_open",
             "proposed_change": "Recheck the ideas taken down before the open with the opening price and restore those "
                                "the recheck rule clears.",
             "support": result["summary"]["restored"]["known"] + result["summary"]["stayed_watch"]["known"],
             "evidence": result["summary"]}]


def render(result: dict[str, Any]) -> str:
    def v(x: Any, suffix: str = "") -> str:
        return "άγνωστο" if x is None else f"{x}{suffix}"

    s = result["summary"]
    lines = ["# Ξανακρίσιμο στο άνοιγμα · ιδέες που κατέβασε ο έλεγχος πριν το άνοιγμα", "",
             "Μόνο έρευνα: τίποτα δεν άλλαξε στις εντολές. Κανόνας: το gap μετριέται ξανά με την τιμή ανοίγματος· "
             f"επιστρέφει αν δεν είναι πια κόντρα πάνω από {GAP_ATR}×ATR. Σημαντικό 8-K: επιστρέφει μόνο αν η αγορά "
             f"άνοιξε κόντρα στη θέση το πολύ {MATERIAL_ATR}×ATR. Άγνωστη τιμή ή ATR: μένει σε watch. Αποτέλεσμα = η προσομοίωση στα 15λεπτα "
             "(επιβεβαίωση Miltos, τα δικά της όρια).", "",
             f"Ιδέες {result['ideas']} · θα επέστρεφαν {result['restored']} · συμπέρασμα: **{result['verdict']}**.", "",
             "| ομάδα | με αποτέλεσμα | μέρες | μέσο % | υπέρβαση SPY % | νίκησαν SPY % | κατάσταση |", "|---|---|---|---|---|---|---|"]
    for name, title in (("restored", "θα επέστρεφαν"), ("stayed_watch", "θα έμεναν σε watch"),
                        ("all", "όλες (χωρίς αποκλεισμό, μόνο ο Miltos)"), ("traded", "trades που μπήκαν")):
        m = s[name]
        lines.append(f"| {title} | {m['known']} | {m['days']} | {v(m['mean_pct'])} | {v(m['mean_excess_pct'])} | "
                     f"{v(m['beat_spy_pct'])} | {m['status']} |")
    lines += ["", "## Ανά ιδέα", "", "| μέρα | trader | μετοχή | στρατηγική | κατ. | gap πριν | gap στο άνοιγμα | απόφαση | γιατί | αποτέλεσμα % |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(result["rows"], key=lambda r: (r["session_date"], r["ticker"])):
        lines.append(f"| {r['session_date']} | {r['agent']} | {r['ticker']} | {r['playbook']} | {r['direction']} | "
                     f"{v(r['premarket_gap_pct'])} | {v(r.get('opening_gap_pct'))} | {r['decision']} | {r['why']} | "
                     f"{v(r['outcome_pct'])} |")
    return "\n".join(lines) + "\n"
