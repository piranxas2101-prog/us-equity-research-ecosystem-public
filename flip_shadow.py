"""After a direction flip: what the opposite position would have done. Shadow only, no order (owner 2026-11-07).

``thesis_exit.thesis_break`` closes a held position when tonight's board has a candidate in the opposite direction on
the same stock (9 of 9 shorts won, +7.08% on average, in the week of 2026-11-02). The owner asks whether the short
should then become a long. A first look at 12 flips: bought at that day's open the long lost 1.94% the same day (3 of
12 positive) and held up better than SPY over the next sessions. So the long is not opened automatically: it would be
an ordinary entry through Koulis and Miltos, and it is measured here first. A flip is one stock's reading, not the
market's direction.

For every position closed by a flip (data/paper_positions_closed.jsonl), on the session it closed in:

* the opposite candidate in the ledger: none after the pre-open check = no entry;
* Koulis: what the gate said about it that day. ``existing_account_exposure`` alone is the position being closed
  itself, so it counts as passing (it may hide a later check of the gate, so a live version sees more blocks). Koulis's
  file holds only its latest session: read on the session's evening and kept; older flips stay unknown, never a pass;
* Miltos: the ledger's replay on 15-minute bars (``paper_simulation.py``): his bar confirmation only, not his volume
  and market checks, at the first confirmed bar's price; not confirmed = no entry.

Held 1, 3, 5, 10 and 20 sessions (the entry session included) so the holding time can be chosen later from evidence,
from two entries: ``open`` (flipped at once at the session's open, the comparison) and ``miltos``; and two exits:
``fixed`` (the last close) and ``stop`` (the agents' widening stop, ``thesis_exit.scaled_stop``; unknown ATR: no result).
The market-shock exit is not replayed. SPY over the same days from the entry session's open gives the excess. A window
not stored yet stays pending. Nothing reads this to decide; a lesson candidate is only proposed, the owner decides.

To go live (only if the owner accepts it): Koulis would have to let the opposite candidate through its own
``existing_account_exposure`` block, and the monitor would have to wait for the close to fill before the entry.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

import holding_shadow as hs
import thesis_exit as te

NEW_YORK = ZoneInfo("America/New_York")
FLIP_RULE = "thesis_direction_flip"
HORIZONS = (1, 3, 5, 10, 20)
ENTRY_RULES = ("open", "miltos")
EXIT_RULES = ("fixed", "stop")
OWN_POSITION = "existing_account_exposure"
# five horizons are looked at, so "beat SPY" must hold at the Wilson lower bound, not only on average
PASSES_WILSON_PCT = 50.0
ROOT = Path(__file__).resolve().parent

Frames = Callable[[str], "pd.DataFrame | None"]


def flips(closed_log: Path) -> list[dict[str, Any]]:
    """Every position a direction flip closed; a line without a known session or direction is left out."""
    if not closed_log.is_file():
        return []
    out = []
    for line in closed_log.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            closed_at = datetime.fromisoformat(str(row["closed_at"]).replace("Z", "+00:00"))
        except (ValueError, KeyError):
            continue
        direction = str(row.get("direction") or "")
        if row.get("close_reason") != FLIP_RULE or direction not in ("long", "short"):
            continue
        ticker = str(row.get("symbol") or "").upper()
        session = closed_at.astimezone(NEW_YORK).date().isoformat()
        plpc = row.get("exit_plpc")
        out.append({"flip_id": f"{session}|{ticker}|{row.get('agent')}|{row.get('playbook')}", "session_date": session,
                    "ticker": ticker, "agent": row.get("agent"), "closed_playbook": row.get("playbook"),
                    "closed_direction": direction, "direction": "long" if direction == "short" else "short",
                    "closed_result_pct": None if plpc is None else round(float(plpc) * 100, 3),
                    "atr_14_pct": row.get("atr_14_pct")})
    return out


def koulis_view(koulis: dict[str, Any] | None, flip: dict[str, Any]) -> dict[str, Any] | None:
    """Koulis on the opposite candidate that day; None when his file is another session's (unknown, read later)."""
    if not koulis or koulis.get("session_date") != flip["session_date"]:
        return None
    def mine(rows: Any) -> list[dict[str, Any]]:
        return [r for r in rows or [] if str(r.get("ticker") or "").upper() == flip["ticker"]
                and r.get("trade_direction") == flip["direction"]]
    if mine(koulis.get("approved_intents")):
        return {"status": "approved", "reasons": []}
    reasons = sorted({str(b.get("block_reason")) for b in mine(koulis.get("blocked_intents"))})
    if not reasons:
        return {"status": "not_seen", "reasons": []}
    return {"status": "only_own_position" if OWN_POSITION in reasons else "blocked", "reasons": reasons}


def candidate(flip: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The best-standing opposite candidate of that session in the ledger, or None."""
    rows = [r for r in records if r.get("session_date") == flip["session_date"] and r.get("ticker") == flip["ticker"]
            and r.get("direction") == flip["direction"] and r.get("kind") != "preopen_demoted"]
    rows.sort(key=lambda r: (r.get("strategy_rank_pct") is None, -(r.get("strategy_rank_pct") or 0.0), r["record_id"]))
    return rows[0] if rows else None


def forward(frame: pd.DataFrame, session: date, entry: float, sessions: int, direction: str, exit_rule: str,
            atr_pct: Any, *, checks_entry_day: bool) -> dict[str, Any] | None:
    """One hold entered at ``entry`` on ``session``; None while its last session is not stored or the stop is unknown.
    ``checks_entry_day``: False when the entry came during the day, when that day's range may be from before it."""
    dates = frame.index.date
    start = int((dates < session).sum())
    if start >= len(frame) or dates[start] != session or not entry > 0:
        return None
    end = start + sessions - 1
    if end >= len(frame):
        return None
    o, h, low, c = (frame[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    long = direction == "long"
    exit_index, exit_price = end, float(c[end])
    if exit_rule == "stop":
        try:
            stop = te.scaled_stop(float(atr_pct), sessions)
        except (TypeError, ValueError):
            stop = None
        if stop is None:
            return None
        level = entry * (1 - stop) if long else entry * (1 + stop)
        for j in range(start, end + 1):
            if j > start and ((o[j] <= level) if long else (o[j] >= level)):
                exit_index, exit_price = j, float(o[j])
                break
            if (j > start or checks_entry_day) and ((low[j] <= level) if long else (h[j] >= level)):
                exit_index, exit_price = j, level
                break
    result = (1.0 if long else -1.0) * (exit_price / entry - 1) * 100
    return {"exit_date": str(dates[exit_index]), "exit_price": round(exit_price, 4),
            "sessions_held": exit_index - start + 1, "result_pct": round(result, 3)}


def ensure_tables(conn: Any) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS paper_flip_koulis (flip_id TEXT PRIMARY KEY, status TEXT NOT NULL,"
                 " reasons_json TEXT NOT NULL, read_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS paper_flip_shadows (flip_id TEXT NOT NULL, entry_rule TEXT NOT NULL,"
                 " exit_rule TEXT NOT NULL, horizon INTEGER NOT NULL, session_date TEXT NOT NULL, entry_price REAL NOT NULL,"
                 " exit_date TEXT NOT NULL, sessions_held INTEGER NOT NULL, result_pct REAL NOT NULL, spy_pct REAL NOT NULL,"
                 " excess_pct REAL NOT NULL, computed_at TEXT NOT NULL, PRIMARY KEY (flip_id, entry_rule, exit_rule, horizon))")


def _simulation(conn: Any, record_id: str | None) -> dict[str, Any] | None:
    if not record_id:
        return None
    try:
        row = conn.execute("SELECT simulation_json FROM paper_simulations WHERE record_id=?", (record_id,)).fetchone()
    except Exception:  # noqa: BLE001 - the table comes with the first replay: unknown until then
        return None
    return json.loads(row[0]) if row else None


def build_cases(conn: Any, closed_log: Path, records: list[dict[str, Any]],
                koulis: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The flips with the path their opposite candidate took that day; Koulis's reading is kept once read."""
    ensure_tables(conn)
    kept = {r[0]: {"status": r[1], "reasons": json.loads(r[2])} for r in
            conn.execute("SELECT flip_id, status, reasons_json FROM paper_flip_koulis").fetchall()}
    stamp, cases = datetime.now(timezone.utc).isoformat(), []
    for flip in flips(closed_log):
        view = kept.get(flip["flip_id"])
        if view is None:
            view = koulis_view(koulis, flip)
            if view is not None:
                conn.execute("INSERT INTO paper_flip_koulis VALUES (?,?,?,?)",
                             (flip["flip_id"], view["status"], json.dumps(view["reasons"]), stamp))
        row = candidate(flip, records)
        simulation = _simulation(conn, row["record_id"] if row else None)
        passes = None if view is None else view["status"] in ("approved", "only_own_position")
        cases.append({**flip, "candidate": bool(row), "candidate_playbook": row.get("playbook") if row else None,
                      "koulis": view["status"] if view else None, "koulis_reasons": view["reasons"] if view else [],
                      "passes_koulis": passes,
                      "miltos_entered": None if simulation is None else bool(simulation.get("entered")),
                      "miltos_entry_price": simulation.get("entry_price") if simulation else None})
    conn.commit()
    return cases


def fill(conn: Any, cases: list[dict[str, Any]], frames: Frames) -> int:
    """Every complete (flip, entry, exit, horizon) once; what is not stored yet is tried again on a later run."""
    ensure_tables(conn)
    known = {tuple(r) for r in conn.execute("SELECT flip_id, entry_rule, exit_rule, horizon FROM paper_flip_shadows")}
    spy, stamp, added = frames("SPY"), datetime.now(timezone.utc).isoformat(), 0
    for case in cases:
        frame = frames(case["ticker"])
        if frame is None:
            continue
        session = date.fromisoformat(case["session_date"])
        today = frame[frame.index.date == session]
        entries = {"open": float(today["Open"].iloc[0]) if not today.empty else None,
                   "miltos": case.get("miltos_entry_price") if case.get("miltos_entered") else None}
        for entry_rule, exit_rule, horizon in ((e, x, h) for e in ENTRY_RULES for x in EXIT_RULES for h in HORIZONS):
            entry = entries[entry_rule]
            if entry is None or (case["flip_id"], entry_rule, exit_rule, horizon) in known:
                continue
            out = forward(frame, session, float(entry), horizon, case["direction"], exit_rule, case.get("atr_14_pct"),
                          checks_entry_day=entry_rule == "open")
            if out is None:
                continue
            bench = hs._spy(spy, case["session_date"], out["exit_date"])
            if bench is None:
                continue
            excess = out["result_pct"] - bench if case["direction"] == "long" else out["result_pct"] + bench
            conn.execute("INSERT INTO paper_flip_shadows VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (case["flip_id"], entry_rule, exit_rule, horizon, case["session_date"], float(entry),
                          out["exit_date"], out["sessions_held"], out["result_pct"], bench, round(excess, 3), stamp))
            added += 1
    conn.commit()
    return added


def evaluate(conn: Any, cases: list[dict[str, Any]],
             metrics: Callable[[list[dict[str, Any]]], dict[str, Any]], margin_pct: float) -> dict[str, Any]:
    """``open``: all flips at the open. ``miltos``: confirmed, whatever Koulis said. ``gated``: confirmed and known to
    pass Koulis, the entry that would really happen; the verdict reads this one, with the stop."""
    ensure_tables(conn)
    by_case = {c["flip_id"]: c for c in cases}
    groups: dict[str, list[dict[str, Any]]] = {}
    for fid, entry_rule, exit_rule, horizon, session, result, excess in conn.execute(
            "SELECT flip_id, entry_rule, exit_rule, horizon, session_date, result_pct, excess_pct FROM paper_flip_shadows"):
        case = by_case.get(fid)
        if case is None:
            continue
        row = {"session_date": session, "outcome_pct": result, "excess_pct": excess}
        groups.setdefault(f"{entry_rule} · {exit_rule} · {horizon}", []).append(row)
        if entry_rule == "miltos" and case.get("passes_koulis") is True:
            groups.setdefault(f"gated · {exit_rule} · {horizon}", []).append(row)
    summary = {name: metrics(rows) for name, rows in sorted(groups.items())}
    gated = {h: summary.get(f"gated · stop · {h}") for h in HORIZONS}
    judged = {h: m for h, m in gated.items() if m and m["status"] == "judged"}
    passed = sorted(h for h, m in judged.items()
                    if m["mean_excess_pct"] is not None and m["mean_excess_pct"] >= margin_pct
                    and (m["beat_spy_wilson_lower_pct"] or 0) > PASSES_WILSON_PCT)
    verdict = "insufficient_sample" if not judged else "the opposite entry would have helped" if passed else "no clear gain"
    return {"schema": "ecosystem.flip_shadow.v1", "flips": len(cases), "candidates": sum(c["candidate"] for c in cases),
            "passes_koulis": sum(c["passes_koulis"] is True for c in cases),
            "koulis_unknown": sum(c["passes_koulis"] is None for c in cases),
            "miltos_entered": sum(c["miltos_entered"] is True for c in cases),
            "gated": sum(c["passes_koulis"] is True and c["miltos_entered"] is True for c in cases),
            "summary": summary, "passed_horizons": passed, "verdict": verdict, "cases": cases,
            "active_decisions_changed": False}


def proposal(result: dict[str, Any]) -> list[dict[str, Any]]:
    if result.get("verdict") != "the opposite entry would have helped":
        return []
    horizon = result["passed_horizons"][0]
    return [{"target_agent": "koulis", "criterion_id": "flip_opposite_entry",
             "proposed_change": "After a direction flip let the opposite candidate through as an ordinary entry "
                                f"(Koulis and Miltos). Horizons that passed: {result['passed_horizons']}.",
             "support": result["summary"][f"gated · stop · {horizon}"]["known"],
             "evidence": {k: result[k] for k in ("passed_horizons", "summary")}}]


GROUPS = (("gated", "πέρασαν Koulis και Miltos"), ("miltos", "μόνο επιβεβαίωση Miltos"), ("open", "αμέσως στο άνοιγμα"))


def render(result: dict[str, Any]) -> str:
    def v(x: Any) -> str:
        return "άγνωστο" if x is None else str(x)

    lines = ["# Αντίθετη θέση μετά από αλλαγή κατεύθυνσης (shadow, χωρίς εντολές)", "",
             "Τι θα έκανε η αντίθετη θέση στη μετοχή που μόλις έκλεισε ο κανόνας. Τίποτα δεν αλλάζει στις εντολές.",
             "Ο Miltos εδώ είναι η επιβεβαίωση σε μπάρες 15 λεπτών, χωρίς τους ελέγχους όγκου και αγοράς. "
             "Το stop είναι των agents· η έξοδος από σοκ δεν υπολογίζεται.", "",
             f"Αλλαγές {result['flips']} · με αντίθετο υποψήφιο {result['candidates']} · "
             f"περνούν Koulis {result['passes_koulis']} (άγνωστο {result['koulis_unknown']}) · "
             f"επιβεβαίωση Miltos {result['miltos_entered']} · και τα δύο {result['gated']}.", "",
             f"Συμπέρασμα: **{result['verdict']}**.", "",
             "| είσοδος | διάρκεια | έξοδος | με αποτέλεσμα | μέρες | μέσο % | υπέρβαση SPY % | νίκησαν SPY % | κατάσταση |",
             "|---|---|---|---|---|---|---|---|---|"]
    for key, title in GROUPS:
        for horizon in HORIZONS:
            for exit_rule in EXIT_RULES:
                m = result["summary"].get(f"{key} · {exit_rule} · {horizon}")
                if m:
                    lines.append(f"| {title} | {horizon} | {exit_rule} | {m['known']} | {m['days']} | {v(m['mean_pct'])} | "
                                 f"{v(m['mean_excess_pct'])} | {v(m['beat_spy_pct'])} | {m['status']} |")
    lines += ["", "## Ανά αλλαγή", "", "| μέρα | μετοχή | έκλεισε | αποτέλεσμα % | αντίθετος υποψήφιος | Koulis | Miltos |",
              "|---|---|---|---|---|---|---|"]
    for c in sorted(result["cases"], key=lambda c: (c["session_date"], c["ticker"])):
        koulis = v(c["koulis"]) + (f" ({', '.join(c['koulis_reasons'])})" if c["koulis_reasons"] else "")
        lines.append(f"| {c['session_date']} | {c['ticker']} | {c['closed_direction']} | {v(c['closed_result_pct'])} | "
                     f"{v(c['candidate_playbook'])} | {koulis} | {v(c['miltos_entered'])} |")
    return "\n".join(lines) + "\n"


def run(root: Path, conn: Any, records: list[dict[str, Any]], frames: Frames,
        metrics: Callable[[list[dict[str, Any]]], dict[str, Any]], margin_pct: float, *, write: bool = True) -> dict[str, Any]:
    path = root / "output" / "koulis_order_intents_latest.json"
    koulis = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    cases = build_cases(conn, root / "data" / "paper_positions_closed.jsonl", records, koulis)
    fill(conn, cases, frames)
    result = evaluate(conn, cases, metrics, margin_pct)
    if write:
        out = root / "output" / "paper_learning"
        out.mkdir(parents=True, exist_ok=True)
        (out / "flip_shadow_latest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "flip_shadow_latest.md").write_text(render(result), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    import paper_learning as pl
    import paper_trade_ledger as ledger

    with closing(ledger.connect(args.root / "data" / "paper_learning.db")) as conn:
        result = run(args.root, conn, ledger.load(conn), hs.store_frames(args.root / "data" / "price_history"),
                     pl.metrics, pl.MARGIN_PCT)
    print(json.dumps({k: result[k] for k in ("flips", "candidates", "passes_koulis", "koulis_unknown", "miltos_entered",
                                             "gated", "verdict")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
