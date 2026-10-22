"""Learning from real paper trades: evaluation, teaching, proposals and periodic reports (phases 2-5).

Owner's decision 2026-10-15. Reads the paper trade ledger (``paper_trade_ledger.py``) and:

* fills what each non-trade *would* have done (open to close of its session, in its direction) and SPY's move,
  from the price store; a session not stored yet stays unknown and is filled on a later run;
* evaluates each strategy (agent, playbook, direction), each holding class and market state: trades, days, wins,
  mean and median result, mean excess over SPY, share beating SPY with its Wilson lower bound. A group is judged
  only with at least ``MIN_TRADES`` trades from ``MIN_DAYS`` days; below that it says so instead of guessing;
* compares live strategies with the five-year lab, and judges the control team from the counterfactuals: the
  pre-open check (demoted vs traded), Koulis (left out vs approved), Miltos (approved but not entered vs entered)
  and the monitors (exits by stop or take-profit vs holding to the close);
* keeps a live track record per strategy (shadow: nothing reads it to decide yet) and Sokratis's trade review;
* writes lesson *candidates* to the learning control plane when the evidence is sufficient. A candidate never
  changes anything: it is shown to the owner, followed in shadow, and only the owner promotes it;
* writes weekly (Saturday), monthly, quarterly and yearly reports.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import statistics
from typing import Any, Callable

import holding_shadow as hs
import preopen_recheck as pr
import paper_trade_ledger as ledger

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output" / "paper_learning"
MIN_TRADES = 30
MIN_DAYS = 10
MARGIN_PCT = 0.5          # a control-team proposal needs the other side better by at least this, in % per trade
RUBRIC = "paper-learning.v1"
PERIODS = ("week", "month", "quarter", "year")
VERDICT_GR = {"insufficient_sample": "ανεπαρκές δείγμα", "the other side did better": "η άλλη πλευρά πήγε καλύτερα",
              "the traded side did better": "τα trades πήγαν καλύτερα", "no clear difference": "καμία σαφής διαφορά"}

Prices = Callable[[str, date], tuple[float | None, float | None]]


def store_prices(directory: Path) -> Prices:
    """Open and close of one session from the price store, or (None, None)."""
    import price_history_store as phs

    cache: dict[str, Any] = {}

    def prices(ticker: str, session: date) -> tuple[float | None, float | None]:
        if ticker not in cache:
            cache[ticker] = phs.load(ticker, directory)
        frame = cache[ticker]
        if frame is None:
            return None, None
        rows = frame[frame.index.date == session]
        if rows.empty:
            return None, None
        return ledger._float(rows["Open"].iloc[0]), ledger._float(rows["Close"].iloc[0])
    return prices


def open_close(prices: Prices, ticker: str, session: date, direction: str = "long") -> float | None:
    opened, closed = prices(ticker, session)
    if opened is None or closed is None:
        return None
    return round((1.0 if direction == "long" else -1.0) * (closed / opened - 1) * 100, 3)


def fill_counterfactuals(conn: Any, records: list[dict[str, Any]], prices: Prices) -> int:
    """Open-to-close (in each record's direction) and SPY's open-to-close, once both are stored."""
    known = {r[0] for r in conn.execute("SELECT record_id FROM paper_counterfactuals").fetchall()}
    stamp, added = datetime.now(timezone.utc).isoformat(), 0
    for record in records:
        if record["record_id"] in known:
            continue
        session = date.fromisoformat(record["session_date"])
        own, spy = open_close(prices, record["ticker"], session, record["direction"]), open_close(prices, "SPY", session)
        if own is None or spy is None:
            continue  # not stored yet: tried again on a later run
        conn.execute("INSERT INTO paper_counterfactuals VALUES (?,?,?,?)", (record["record_id"], own, spy, stamp))
        added += 1
    conn.commit()
    return added


def fill_simulations(conn: Any, records: list[dict[str, Any]], bars: Any, *, stop: float = 0.03,
                     take: float = 0.06) -> int:
    """'Had we opened this position': our entry and exit rules replayed on the session's 15-minute bars
    (paper_simulation.py), once per record. A session without bars is tried again on a later run."""
    import paper_simulation as sim

    conn.execute("CREATE TABLE IF NOT EXISTS paper_simulations (record_id TEXT PRIMARY KEY, simulation_json TEXT NOT NULL,"
                 " spy_session_pct REAL, computed_at TEXT NOT NULL)")
    known = {r[0] for r in conn.execute("SELECT record_id FROM paper_simulations").fetchall()}
    pending: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if record["record_id"] not in known:
            pending.setdefault(record["session_date"], []).append(record)
    stamp, added = datetime.now(timezone.utc).isoformat(), 0
    for day, items in pending.items():
        session = date.fromisoformat(day)
        try:
            table = bars(sorted({r["ticker"] for r in items} | {"SPY"}), session)
        except Exception as exc:  # noqa: BLE001 - no bars today: unknown, tried again later
            print(f"[LEARN] bars for {day} could not be read: {type(exc).__name__}", flush=True)
            continue
        spy = sim.spy_session(table.get("SPY") or [], session)
        for record in items:
            # each position's own limits (its trade plan, as the monitors apply them); the defaults only without one
            own_stop = record.get("stop_loss_pct") or stop
            own_take = record.get("take_profit_pct") or take
            result = sim.simulate(table.get(record["ticker"]) or [], session, record["direction"],
                                  stop=own_stop, take=own_take)
            if result is None or spy is None:
                continue
            conn.execute("INSERT INTO paper_simulations VALUES (?,?,?,?)",
                         (record["record_id"], json.dumps({**result, "stop": own_stop, "take": own_take}), spy, stamp))
            added += 1
    conn.commit()
    return added


def attach_counterfactuals(conn: Any, records: list[dict[str, Any]]) -> None:
    """Each record's outcome: a trade's real result; otherwise the simulated trade (entered positions only; an
    approval Miltos did not enter uses the forced entry). Without bars, open to close stands in, flagged."""
    table = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT * FROM paper_counterfactuals").fetchall()}
    try:
        sims = {r[0]: (json.loads(r[1]), r[2]) for r in
                conn.execute("SELECT record_id, simulation_json, spy_session_pct FROM paper_simulations").fetchall()}
    except Exception:  # noqa: BLE001 - table not created yet
        sims = {}
    for record in records:
        own, spy = table.get(record["record_id"], (None, None))
        simulation, sim_spy = sims.get(record["record_id"], (None, None))
        record["open_close_pct"], record["simulation"] = own, simulation
        spy = sim_spy if sim_spy is not None else spy
        record["spy_open_close_pct"] = spy
        if record["kind"] == "trade":
            result, source = record.get("result_pct"), "real"
        elif simulation is not None and record["kind"] == "approved_not_entered":
            result, source = simulation.get("forced_result_pct"), "simulated_forced_entry"
        elif simulation is not None:
            result, source = (simulation.get("result_pct"), "simulated") if simulation.get("entered") else (None, "not_entered")
        else:
            result, source = own, ("open_to_close" if own is not None else "unknown")
        record["outcome_pct"], record["outcome_source"] = result, source
        record["excess_pct"] = (None if result is None or spy is None else
                                round(result - spy if record["direction"] == "long" else result + spy, 3))


def wilson_lower(wins: int, total: int, z: float = 1.96) -> float | None:
    if total <= 0:
        return None
    p = wins / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return round((centre - margin) / (1 + z * z / total) * 100, 1)


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    known = [r for r in rows if r.get("outcome_pct") is not None]
    outcomes = [r["outcome_pct"] for r in known]
    excess = [r["excess_pct"] for r in known if r.get("excess_pct") is not None]
    days = len({r["session_date"] for r in known})
    beat = sum(1 for x in excess if x > 0)
    return {"records": len(rows), "known": len(known), "days": days,
            "win_rate_pct": round(100 * sum(1 for x in outcomes if x > 0) / len(outcomes), 1) if outcomes else None,
            "mean_pct": round(statistics.fmean(outcomes), 3) if outcomes else None,
            "median_pct": round(statistics.median(outcomes), 3) if outcomes else None,
            "mean_excess_pct": round(statistics.fmean(excess), 3) if excess else None,
            "beat_spy_pct": round(100 * beat / len(excess), 1) if excess else None,
            "beat_spy_wilson_lower_pct": wilson_lower(beat, len(excess)),
            "status": "judged" if len(known) >= MIN_TRADES and days >= MIN_DAYS else "insufficient_sample"}


def _group(rows: list[dict[str, Any]], *keys: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(" · ".join(str(row.get(k)) for k in keys), []).append(row)
    return {name: metrics(items) for name, items in sorted(groups.items())}


FDR_Q = 0.10              # false-discovery rate across all category tests (Benjamini-Hochberg)
MIN_WEEKS_SAME_SIGN = 2   # a finding must repeat in at least two different weeks


def _beats(rows: list[dict[str, Any]]) -> tuple[int, int]:
    known = [r["excess_pct"] for r in rows if r.get("excess_pct") is not None]
    return sum(1 for x in known if x > 0), len(known)


def _two_proportion_p(w1: int, n1: int, w2: int, n2: int) -> float:
    pooled = (w1 + w2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = abs(w1 / n1 - w2 / n2) / se
    return math.erfc(z / math.sqrt(2))


def category_findings(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Where a strategy's signals do better or worse: for each strategy and each tag value, the share beating SPY
    against the rest of the same strategy. Guards against luck: both sides need MIN_TRADES records from MIN_DAYS
    days; the p-values of all tests together pass a Benjamini-Hochberg false-discovery control (q <= FDR_Q); and the
    difference must point the same way in at least MIN_WEEKS_SAME_SIGN different weeks. 'unknown' is never a
    category to act on."""
    strategies: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("excess_pct") is not None and row.get("categories"):
            strategies.setdefault((row["agent"], row["playbook"], row["direction"]), []).append(row)
    tests = []
    for key, members in strategies.items():
        for tag in sorted({t for r in members for t in r["categories"]}):
            for value in sorted({r["categories"].get(tag) for r in members} - {None, "unknown"}):
                inside = [r for r in members if r["categories"].get(tag) == value]
                rest = [r for r in members if r["categories"].get(tag) != value]
                w1, n1 = _beats(inside)
                w2, n2 = _beats(rest)
                days1 = len({r["session_date"] for r in inside})
                if n1 < MIN_TRADES or n2 < MIN_TRADES or days1 < MIN_DAYS:
                    continue
                effect = round((w1 / n1 - w2 / n2) * 100, 1)
                weeks: dict[str, list[list[dict[str, Any]]]] = {}
                for r in members:
                    week = date.fromisoformat(r["session_date"]).isocalendar()[:2]
                    weeks.setdefault(str(week), [[], []])[0 if r["categories"].get(tag) == value else 1].append(r)
                same = 0
                for a, b in weeks.values():
                    (wa, na), (wb, nb) = _beats(a), _beats(b)
                    if na >= 5 and nb >= 5 and (wa / na - wb / nb) * effect > 0:
                        same += 1
                tests.append({"strategy": " · ".join(key), "tag": tag, "value": value, "records": n1, "days": days1,
                              "beat_spy_pct": round(100 * w1 / n1, 1), "rest_beat_spy_pct": round(100 * w2 / n2, 1),
                              "effect_pct_points": effect, "p_value": _two_proportion_p(w1, n1, w2, n2),
                              "weeks_same_direction": same})
    tests.sort(key=lambda t: t["p_value"])
    m = len(tests)
    q_running = 1.0
    for i in range(m - 1, -1, -1):  # Benjamini-Hochberg q-values
        q_running = min(q_running, tests[i]["p_value"] * m / (i + 1))
        tests[i]["q_value"] = round(q_running, 4)
        tests[i]["p_value"] = round(tests[i]["p_value"], 5)
    findings = [t for t in tests if t["q_value"] <= FDR_Q and t["weeks_same_direction"] >= MIN_WEEKS_SAME_SIGN]
    return {"tests": m, "fdr_q": FDR_Q, "min_weeks_same_direction": MIN_WEEKS_SAME_SIGN, "findings": findings,
            "closest": [t for t in tests[:10] if t not in findings]}


def lab_day_edges(lab: dict[str, Any], legacy: dict[str, Any]) -> dict[str, float]:
    """Lab share beating SPY one session after the signal, per 'playbook · direction' (indicative for day trades)."""
    edges = {}
    for entry in (lab.get("results") or []) + (legacy.get("results") or []):
        cell = (entry.get("by_horizon") or {}).get("1") or (entry.get("by_horizon") or {}).get(1) or {}
        if cell.get("beat_spy_pct") is not None:
            edges[f"{entry['strategy']} · {entry['side']}"] = cell["beat_spy_pct"]
    return edges


def _compare(name: str, other: list[dict[str, Any]], traded: list[dict[str, Any]], question: str) -> dict[str, Any]:
    a, b = metrics(other), metrics(traded)
    judged = a["status"] == "judged" and b["status"] == "judged"
    gap = None if a["mean_pct"] is None or b["mean_pct"] is None else round(a["mean_pct"] - b["mean_pct"], 3)
    return {"check": name, "question": question, "other": a, "traded": b, "other_minus_traded_pct": gap,
            "verdict": "insufficient_sample" if not judged else
            "the other side did better" if gap is not None and gap >= MARGIN_PCT else
            "the traded side did better" if gap is not None and gap <= -MARGIN_PCT else "no clear difference"}


def simulation_fidelity(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """How close the replay comes to what really happened, on the trades both exist for (trust in the simulation)."""
    pairs = [(t["result_pct"], t["simulation"]["result_pct"]) for t in trades
             if t.get("result_pct") is not None and (t.get("simulation") or {}).get("entered")]
    if not pairs:
        return {"pairs": 0}
    gaps = [abs(real - simulated) for real, simulated in pairs]
    return {"pairs": len(pairs), "mean_abs_gap_pct": round(statistics.fmean(gaps), 3),
            "same_sign_pct": round(100 * sum(1 for a, b in pairs if (a > 0) == (b > 0)) / len(pairs), 1)}


def evaluate(records: list[dict[str, Any]], *, lab: dict[str, Any] | None = None,
             legacy: dict[str, Any] | None = None) -> dict[str, Any]:
    trades = [r for r in records if r["kind"] == "trade"]
    signals = [r for r in records if r["kind"] == "shadow_candidate"]
    by_kind = {k: [r for r in records if r["kind"] == k] for k in
               ("approved_not_entered", "preopen_demoted", "koulis_left_out")}
    strategies = _group(trades, "agent", "playbook", "direction")
    lab_edges = lab_day_edges(lab or {}, legacy or {})
    live_vs_lab = {}
    for name, cell in strategies.items():
        agent, playbook, direction = name.split(" · ")
        reference = lab_edges.get(f"{playbook} · {direction}")
        live_vs_lab[name] = {"live_beat_spy_pct": cell["beat_spy_pct"], "lab_beat_spy_pct": reference,
                             "status": cell["status"],
                             "gap_pct_points": None if reference is None or cell["beat_spy_pct"] is None
                             else round(cell["beat_spy_pct"] - reference, 1)}
    stopped = [r for r in trades if r.get("exit_reason") in {"stop_loss", "take_profit"}]
    held = [{**r, "outcome_pct": r.get("open_close_pct")} for r in stopped if r.get("open_close_pct") is not None]
    control = [
        _compare("preopen_check", by_kind["preopen_demoted"], signals,
                 "Τα candidates που κατέβασε ο έλεγχος πριν το άνοιγμα πήγαν χειρότερα από όσα κράτησε;"),
        _compare("koulis", by_kind["koulis_left_out"], trades,
                 "Όσα άφησε έξω ο Koulis (όρια χαρτοφυλακίου) πήγαν χειρότερα από όσα ενέκρινε;"),
        _compare("miltos", by_kind["approved_not_entered"], trades,
                 "Οι εγκρίσεις που ο Miltos δεν επιβεβαίωσε πήγαν χειρότερα από όσες μπήκαν;"),
        _compare("monitors", held, stopped,
                 "Οι θέσεις που έκλεισαν σε stop ή take-profit θα πήγαιναν καλύτερα αν κρατούσαν ως το κλείσιμο;"),
    ]
    track = {name: {"trades": cell["known"], "days": cell["days"], "beat_spy_pct": cell["beat_spy_pct"],
                    "live_confidence_shadow": cell["beat_spy_wilson_lower_pct"] if cell["status"] == "judged"
                    else "insufficient_history"} for name, cell in strategies.items()}
    return {"schema": "ecosystem.paper_learning_evaluation.v1", "generated_at": datetime.now(timezone.utc).isoformat(),
            "minimums": {"trades": MIN_TRADES, "days": MIN_DAYS, "control_margin_pct": MARGIN_PCT},
            "sessions": len({r["session_date"] for r in records}), "trades": len(trades),
            "trades_with_result": sum(1 for r in trades if r.get("outcome_pct") is not None),
            "overall": metrics(trades), "strategies": strategies,
            "holding_classes": _group(trades, "holding_class"), "market_states": _group(trades, "market_state"),
            "exit_reasons": _group(trades, "exit_reason"), "live_vs_lab": live_vs_lab,
            "control_team": control, "live_track_record_shadow": track,
            "simulation_fidelity": simulation_fidelity(trades),
            "signals": {"records": len(signals), "overall": metrics(signals),
                        "simulated": sum(1 for r in signals if r.get("simulation") is not None),
                        "entered_pct": (round(100 * sum(1 for r in signals if (r.get("simulation") or {}).get("entered"))
                                              / max(1, sum(1 for r in signals if r.get("simulation") is not None)), 1)
                                        if any(r.get("simulation") is not None for r in signals) else None),
                        "strategies": _group(signals, "agent", "playbook", "direction")},
            "category_findings": category_findings(signals + trades),
            "holding_shadow": hs.summarize(records, metrics),
            "holding_category_findings": hs.category_findings(records, min_records=MIN_TRADES, min_days=MIN_DAYS),
            "active_decisions_changed": False}


def proposals(evaluation: dict[str, Any]) -> list[dict[str, Any]]:
    """Lesson candidates where the evidence is sufficient. The proposal text is fixed per criterion (the learning
    control plane refuses a changed definition); the numbers go in the evidence."""
    out = []
    for name, cell in evaluation["strategies"].items():
        excess, beat = cell["mean_excess_pct"], cell["beat_spy_pct"]
        if cell["status"] == "judged" and excess is not None and beat is not None and excess < 0 and beat < 45:
            agent, playbook, direction = name.split(" · ")
            out.append({"target_agent": "koulis", "criterion_id": f"live_strategy_underperforms:{agent}:{playbook}:{direction}",
                        "proposed_change": f"Give {agent} {playbook} ({direction}) at most one slot a day until its live "
                                           "trades beat SPY again.", "support": cell["known"], "evidence": cell})
    for finding in (evaluation.get("category_findings") or {}).get("findings") or []:
        agent, playbook, direction = finding["strategy"].split(" · ")
        lean = "prefer" if finding["effect_pct_points"] > 0 else "avoid"
        out.append({"target_agent": "koulis",
                    "criterion_id": f"category:{lean}:{agent}:{playbook}:{direction}:{finding['tag']}={finding['value']}",
                    "proposed_change": f"In {agent} {playbook} ({direction}), {lean} candidates with "
                                       f"{finding['tag']} = {finding['value']}.",
                    "support": finding["records"], "evidence": finding})
    out += holding_proposals(evaluation.get("holding_shadow") or {})
    for f in (evaluation.get("holding_category_findings") or {}).get("findings") or []:
        lean = "prefer" if f["effect_pct_points"] > 0 else "avoid"
        for agent in ledger.AGENTS:
            out.append({"target_agent": agent,
                        "criterion_id": f"holding_by_category:{f['class']}:{f['tag']}={f['value']}:{lean}",
                        "proposed_change": f"For candidates with {f['tag']} = {f['value']}, {lean} the {f['class']} hold: "
                                           "there a longer hold beat closing the same day more often than elsewhere.",
                        "support": f["records"], "evidence": f})
    texts = {"preopen_check": ("preopen", "Loosen the pre-open gap rule (1.5x ATR instead of 1x): the candidates it "
                                          "took down did better than the ones it kept."),
             "koulis": ("koulis", "Review Koulis's ranking: the candidates left out did better than the approved ones."),
             "miltos": ("miltos", "Loosen Miltos's 15-minute confirmation: the approvals he did not enter did better."),
             "monitors": ("position_monitor", "Widen the stop / raise the take-profit: positions closed early would "
                                              "have done better held to the close.")}
    for check in evaluation["control_team"]:
        if check["verdict"] == "the other side did better":
            target, text = texts[check["check"]]
            out.append({"target_agent": target, "criterion_id": f"control_team:{check['check']}",
                        "proposed_change": text, "support": check["other"]["known"] + check["traded"]["known"],
                        "evidence": check})
    return out


def holding_proposals(holding: dict[str, Any]) -> list[dict[str, Any]]:
    """Lessons from the shadow multi-day holds (owner's decision 2026-10-17): a longer class that beat the same-day
    class, or a thesis exit that beat holding to the end, for one strategy. Both sides must be judged (>= MIN_TRADES
    records from >= MIN_DAYS sessions) and the gap at least MARGIN_PCT per trade with most records beating SPY.
    Candidates only: Sokratis teaches them to the trader; nothing changes until the owner promotes one."""
    out = []
    for name, cells in (holding.get("strategies") or {}).items():
        agent, playbook, direction = name.split(" · ")
        day = cells.get("day · fixed") or {}

        def better(cell: dict[str, Any], base: dict[str, Any]) -> bool:
            return (cell.get("status") == "judged" and base.get("status") == "judged"
                    and cell.get("mean_excess_pct") is not None and base.get("mean_excess_pct") is not None
                    and cell["mean_excess_pct"] - base["mean_excess_pct"] >= MARGIN_PCT
                    and cell.get("beat_spy_pct") is not None and cell["beat_spy_pct"] > 50)

        for cls in ("week", "month", "quarter"):
            for rule in ("fixed", "thesis"):
                cell = cells.get(f"{cls} · {rule}") or {}
                if better(cell, day):
                    out.append({"target_agent": agent, "criterion_id": f"holding_class:{agent}:{playbook}:{direction}:{cls}:{rule}",
                                "proposed_change": f"Hold {playbook} ({direction}) for a {cls} with the {rule} exit instead "
                                                   "of closing the same day.",
                                "support": cell["known"], "evidence": {"longer": cell, "same_day": day}})
            thesis, fixed = cells.get(f"{cls} · thesis") or {}, cells.get(f"{cls} · fixed") or {}
            if better(thesis, fixed):
                out.append({"target_agent": "position_monitor",
                            "criterion_id": f"thesis_exit:{agent}:{playbook}:{direction}:{cls}",
                            "proposed_change": f"For {agent} {playbook} ({direction}) held a {cls}, close when the thesis "
                                               "breaks instead of holding to the end.",
                            "support": thesis["known"], "evidence": {"thesis": thesis, "fixed": fixed}})
    return out


def shadow_lessons(db_path: Path, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lessons the owner put in shadow, followed on our own records (owner 2026-10-22): a results-journal lesson
    "tag = value is better / worse" is checked on the candidates and trades whose company carries that tag
    (``results_<tag>`` in the categories, from the feature registry), against the rest - over all sessions and in
    the week of a results release. Nothing changes a decision; the numbers are for the owner's promotion call."""
    if not db_path.exists():
        return []
    import market_intel_db

    try:
        with closing(market_intel_db.connect(db_path)) as conn:
            rows = conn.execute("SELECT lesson_id, target_agent, criterion_id FROM agent_lesson_candidates"
                                " WHERE status='shadow' AND task_id='earnings_journal'").fetchall()
    except Exception:  # noqa: BLE001 - no learning tables yet
        return []
    out = []
    for lesson_id, agent, criterion in rows:
        try:
            _, outcome, condition, lean = criterion.split(":")
            tag, value = condition.split("=", 1)
        except ValueError:
            continue
        key = f"results_{tag}"
        known = [r for r in records if (r.get("categories") or {}).get(key) not in (None, "unknown")]
        week = [r for r in known if (r.get("categories") or {}).get("results_this_week") == "yes"]

        def compare(items: list[dict[str, Any]], key: str = key, value: str = value, lean: str = lean) -> dict[str, Any]:
            inside = [r for r in items if r["categories"].get(key) == value]
            rest = [r for r in items if r["categories"].get(key) != value]
            a, b = metrics(inside), metrics(rest)
            gap = None if a["beat_spy_pct"] is None or b["beat_spy_pct"] is None else \
                round(a["beat_spy_pct"] - b["beat_spy_pct"], 1)
            agrees = None if gap is None else (gap > 0) == (lean == "better")
            return {"with_tag": a, "rest": b, "beat_spy_gap_points": gap, "agrees_with_lesson": agrees}
        out.append({"lesson_id": lesson_id, "target_agent": agent, "criterion_id": criterion, "tag": key,
                    "value": value, "lean": lean, "all_sessions": compare(known), "results_week": compare(week)})
    return out


def record_lessons(db_path: Path, items: list[dict[str, Any]], *, task_id: str = "paper_trading",
                   rubric: str = RUBRIC) -> list[dict[str, Any]]:
    if not items:
        return []
    import agent_learning_control_plane as learning
    import market_intel_db

    recorded = []
    with closing(market_intel_db.connect(db_path)) as conn:
        learning.init_schema(conn)
        for item in items:
            proposal = {"schema": "ecosystem.agent_lesson_candidate.v1", "target_agent": item["target_agent"],
                        "criterion_id": item["criterion_id"], "proposed_change": item["proposed_change"],
                        "activation": "candidate_only", "changes_active_scores": False,
                        "requires_shadow_validation": True, "requires_manual_promotion": True}
            lesson_id, created = learning.upsert_lesson_candidate(
                conn, target_agent=item["target_agent"], task_id=task_id, rubric_version=rubric,
                criterion_id=item["criterion_id"], support_count=int(item["support"]), proposal=proposal,
                evidence={"evidence": item["evidence"], "generated_at": datetime.now(timezone.utc).isoformat()})
            recorded.append({"lesson_id": lesson_id, "created": created, **{k: item[k] for k in
                             ("target_agent", "criterion_id", "proposed_change")}})
    return recorded


# ----- reports -------------------------------------------------------------------------------------------------

def period_bounds(period: str, end: date) -> tuple[date, date, str]:
    """The period that contains ``end``: week Monday-Sunday, calendar month, quarter, year."""
    if period == "week":
        start = end - timedelta(days=end.weekday())
        return start, start + timedelta(days=6), f"{start:%Y}-W{start.isocalendar()[1]:02d}"
    if period == "month":
        start = end.replace(day=1)
        nxt = (start + timedelta(days=32)).replace(day=1)
        return start, nxt - timedelta(days=1), f"{start:%Y-%m}"
    if period == "quarter":
        q = (end.month - 1) // 3
        start = date(end.year, 3 * q + 1, 1)
        nxt = date(end.year + (q == 3), (3 * q + 3) % 12 + 1, 1)
        return start, nxt - timedelta(days=1), f"{end.year}-Q{q + 1}"
    if period == "year":
        return date(end.year, 1, 1), date(end.year, 12, 31), str(end.year)
    raise ValueError(period)


def due_periods(today: date) -> list[tuple[str, date]]:
    """Reports to write at the nightly start of ``today``: the week on Saturday, a month/quarter/year on its
    first day (each for the period that just ended)."""
    due = []
    yesterday = today - timedelta(days=1)
    if today.weekday() == 5:
        due.append(("week", yesterday))
    if today.day == 1:
        due.append(("month", yesterday))
        if today.month in (1, 4, 7, 10):
            due.append(("quarter", yesterday))
        if today.month == 1:
            due.append(("year", yesterday))
    return due


def trade_review(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Sokratis's review per trader: the best and worst trades with the context they were taken in."""
    review = {}
    for agent in ledger.AGENTS:
        trades = sorted([r for r in records if r["kind"] == "trade" and r["agent"] == agent
                         and r.get("outcome_pct") is not None], key=lambda r: r["outcome_pct"])
        review[agent] = {"best": trades[-3:][::-1], "worst": trades[:3], "count": len(trades)}
    return review


def _v(value: Any, suffix: str = "") -> str:
    return "άγνωστο" if value is None else f"{value}{suffix}"


def render_report(period: str, label: str, start: date, end: date, evaluation: dict[str, Any],
                  review: dict[str, Any], lessons: list[dict[str, Any]]) -> str:
    names = {"week": "Εβδομαδιαία", "month": "Μηνιαία", "quarter": "Τριμηνιαία", "year": "Ετήσια", "day": "Ημερήσια"}
    o = evaluation["overall"]
    lines = [f"# {names.get(period, period)} αναφορά μάθησης · {label}", "",
             f"Περίοδος {start} – {end} · συνεδρίες {evaluation['sessions']} · trades {evaluation['trades']} "
             f"(με αποτέλεσμα {evaluation['trades_with_result']}).",
             f"Κρίση μόνο με ≥{MIN_TRADES} trades από ≥{MIN_DAYS} μέρες· αλλιώς «ανεπαρκές δείγμα». Τίποτα δεν αλλάζει "
             "χωρίς την έγκριση του ιδιοκτήτη.", "",
             "## Συνολικά", "",
             f"- Κερδισμένα {_v(o['win_rate_pct'], '%')} · μέσο αποτέλεσμα {_v(o['mean_pct'], '%')} · μέση υπέρβαση "
             f"έναντι SPY {_v(o['mean_excess_pct'], '%')} · νίκησαν το SPY {_v(o['beat_spy_pct'], '%')} · κατάσταση {o['status']}.",
             "", "## Ανά στρατηγική (ζωντανά έναντι backtest)", "",
             "| trader · στρατηγική · κατεύθυνση | trades | μέρες | κερδισμένα % | μέσο % | νίκησαν SPY % | backtest % | κατάσταση |",
             "|---|---|---|---|---|---|---|---|"]
    for name, cell in evaluation["strategies"].items():
        lab = evaluation["live_vs_lab"].get(name, {}).get("lab_beat_spy_pct")
        lines.append(f"| {name} | {cell['known']} | {cell['days']} | {_v(cell['win_rate_pct'])} | {_v(cell['mean_pct'])} | "
                     f"{_v(cell['beat_spy_pct'])} | {_v(lab)} | {cell['status']} |")
    lines += ["", "## Ανά κατάσταση αγοράς και τρόπο εξόδου", ""]
    for title, groups in (("Κατάσταση αγοράς", evaluation["market_states"]), ("Έξοδος", evaluation["exit_reasons"])):
        lines.append(f"- {title}: " + "; ".join(f"{k}: {g['known']} trades, μέσο {_v(g['mean_pct'], '%')}"
                                                for k, g in groups.items()) or "-")
    lines += ["", "## Ομάδα ελέγχου (τι θα είχε γίνει)", ""]
    for check in evaluation["control_team"]:
        lines.append(f"- **{check['check']}**: {check['question']} Άλλη πλευρά {check['other']['known']} με μέσο "
                     f"{_v(check['other']['mean_pct'], '%')}, trades {check['traded']['known']} με μέσο "
                     f"{_v(check['traded']['mean_pct'], '%')} → {VERDICT_GR.get(check['verdict'], check['verdict'])}.")
    sig = evaluation.get("signals") or {}
    so = sig.get("overall") or {}
    lines += ["", "## Ποιότητα σημάτων (όλα τα candidates, σκιώδη trades)", "",
              f"- Σκιώδη trades {sig.get('records', 0)} · προσομοιώθηκαν {sig.get('simulated', 0)} · θα είχαν ανοίξει "
              f"(επιβεβαίωση Miltos) {_v(sig.get('entered_pct'), '%')} · με αποτέλεσμα {so.get('known', 0)} · κερδισμένα "
              f"{_v(so.get('win_rate_pct'), '%')} · νίκησαν το SPY {_v(so.get('beat_spy_pct'), '%')}.", "",
              "| trader · στρατηγική · κατεύθυνση | σήματα | μέρες | νίκησαν SPY % | μέσο % | κατάσταση |", "|---|---|---|---|---|---|"]
    for name, cell in (sig.get("strategies") or {}).items():
        lines.append(f"| {name} | {cell['known']} | {cell['days']} | {_v(cell['beat_spy_pct'])} | {_v(cell['mean_pct'])} | "
                     f"{cell['status']} |")
    cf = evaluation.get("category_findings") or {}
    lines += ["", "## Ευρήματα ανά κατηγορία", "",
              f"Έλεγχοι {cf.get('tests', 0)} · έλεγχος τυχαίων ευρημάτων (FDR q ≤ {cf.get('fdr_q')}) · το εύρημα πρέπει "
              f"να επαναλαμβάνεται σε ≥{cf.get('min_weeks_same_direction')} εβδομάδες.", ""]
    found = cf.get("findings") or []
    lines += [f"- {f['strategy']} · {f['tag']} = {f['value']}: νίκησαν το SPY {f['beat_spy_pct']}% έναντι "
              f"{f['rest_beat_spy_pct']}% στα υπόλοιπα ({f['effect_pct_points']:+} μονάδες, {f['records']} σήματα, "
              f"q = {f['q_value']})" for f in found] or ["- Κανένα ακόμη που να περνά τους ελέγχους."]
    by_category = (evaluation.get("holding_category_findings") or {})
    lines += ["", "## Ποια διάρκεια ταιριάζει σε ποιες εταιρείες και συνθήκες (σκιώδη κρατήματα)", "",
              f"Έλεγχοι {by_category.get('tests', 0)} (q ≤ {by_category.get('fdr_q')}, ≥ 2 εβδομάδες ίδια φορά)."]
    lines += [f"- {f['tag']} = {f['value']}: η {f['class']} νίκησε την ημέρα στο {f['longer_won_pct']}% έναντι "
              f"{f['rest_longer_won_pct']}% αλλού ({f['effect_pct_points']:+} μονάδες, {f['records']} εγγραφές, "
              f"q = {f['q_value']})" for f in by_category.get("findings") or []] or ["- Κανένα ακόμη."]
    shadow = evaluation.get("shadow_lessons") or []
    if shadow:
        lines += ["", "## Μαθήματα σε σκιώδη δοκιμή (στα δικά μας candidates και trades)", "",
                  "| agent | μάθημα | με την ετικέτα | νίκησαν SPY % | υπόλοιπα % | διαφορά | συμφωνεί; | κατάσταση |",
                  "|---|---|---|---|---|---|---|---|"]
        for item in shadow:
            cell = item["all_sessions"]
            lines.append(f"| {item['target_agent']} | {item['tag']} = {item['value']} ({item['lean']}) | "
                         f"{cell['with_tag']['known']} | {_v(cell['with_tag']['beat_spy_pct'])} | "
                         f"{_v(cell['rest']['beat_spy_pct'])} | {_v(cell['beat_spy_gap_points'])} | "
                         f"{_v(cell['agrees_with_lesson'])} | {cell['with_tag']['status']} |")
    held = evaluation.get("holding_shadow") or {}
    names_cls = {"day": "ημέρα", "week": "εβδομάδα", "month": "μήνας", "quarter": "τρίμηνο"}
    recheck = evaluation.get("preopen_recheck") or {}
    if recheck:
        restored = (recheck.get("summary") or {}).get("restored") or {}
        lines += ["", "## Ξανακρίσιμο στο άνοιγμα (χωρίς εντολές)", "",
                  f"- Ιδέες που κατέβηκαν πριν το άνοιγμα {recheck.get('ideas')} · θα επέστρεφαν στο άνοιγμα "
                  f"{recheck.get('restored')} · μέσο {_v(restored.get('mean_pct'), '%')} · υπέρβαση SPY "
                  f"{_v(restored.get('mean_excess_pct'), '%')} · συμπέρασμα: {recheck.get('verdict')}. "
                  "Πλήρης ανάλυση: `output/paper_learning/preopen_recheck_latest.md`."]
    lines += ["", "## Σκιώδη κρατήματα πολλών ημερών (χωρίς εντολές)", "",
              "Κάθε εγγραφή κρατημένη από το άνοιγμα της συνεδρίας της για ημέρα / εβδομάδα / μήνα / τρίμηνο: fixed (ως το "
              "τέλος), atr_bracket (τα δικά της stop/take-profit) και thesis (όσο ισχύει ο λόγος, για εβδομάδα/μήνα/τρίμηνο). Μια διάρκεια μετρά μόλις υπάρχει η τελευταία της "
              "συνεδρία στην αποθήκη τιμών.", "",
              "| διάρκεια · κανόνας | με αποτέλεσμα | μέρες | μέσο % | υπέρβαση SPY % | νίκησαν SPY % | κατάσταση |",
              "|---|---|---|---|---|---|---|"]
    for name, cell in (held.get("overall") or {}).items():
        cls, rule = name.split(" · ")
        lines.append(f"| {names_cls.get(cls, cls)} · {rule} | {cell['known']} | {cell['days']} | {_v(cell['mean_pct'])} | "
                     f"{_v(cell['mean_excess_pct'])} | {_v(cell['beat_spy_pct'])} | {cell['status']} |")
    lines += ["", "| trader · στρατηγική · κατεύθυνση (fixed) | ημέρα | εβδομάδα | μήνας | τρίμηνο |", "|---|---|---|---|---|"]
    for name, cells in (held.get("strategies") or {}).items():
        if not any(c["known"] for c in cells.values()):
            continue
        lines.append(f"| {name} | " + " | ".join(
            "—" if not cells[f"{cls} · fixed"]["known"] else
            f"{_v(cells[f'{cls} · fixed']['mean_excess_pct'])} ({cells[f'{cls} · fixed']['known']})"
            for cls in names_cls) + " |")
    lines += ["", "## Sokratis · ανασκόπηση trades", ""]
    for agent, view in review.items():
        lines.append(f"- **{agent}** ({view['count']} trades με αποτέλεσμα)")
        for title, items in (("καλύτερα", view["best"]), ("χειρότερα", view["worst"])):
            for r in items:
                pre = (r.get("preopen") or {})
                lines.append(f"  - {title}: {r['session_date']} {r['ticker']} {r['playbook']} {r['direction']} "
                             f"{_v(r.get('outcome_pct'), '%')} · έξοδος {r.get('exit_reason')} · κατάσταση "
                             f"{r.get('market_state')} · pre-market gap {_v(pre.get('gap_pct'), '%')}")
    lines += ["", "## Υποψήφιες αλλαγές (μόνο με έγκριση)", ""]
    lines += [f"- {l['target_agent']}: {l['proposed_change']} ({l['lesson_id']})" for l in lessons] or \
        ["- Καμία ακόμη: δεν υπάρχει αρκετό δείγμα ή καμία διαφορά δεν ξεπερνά το περιθώριο."]
    return "\n".join(lines) + "\n"


def default_bars(root: Path) -> Any:
    """Alpaca 15-minute bars (read-only market data) when the keys are configured, else None."""
    import os

    import paper_simulation as sim
    from research_workflow_orchestrator import load_env

    load_env(root / ".env")  # Alpaca keys, never printed
    key_id, secret = os.getenv("ALPACA_API_KEY_ID", "").strip(), os.getenv("ALPACA_API_SECRET_KEY", "").strip()
    return sim.alpaca_bars(key_id, secret) if key_id and secret else None


def run_evaluation(root: Path, *, since: date | None = None, until: date | None = None,
                   prices: Prices | None = None, bars: Any = "default", frames: Any = None,
                   write_lessons: bool = True) -> tuple[dict[str, Any], list, dict]:
    bars = default_bars(root) if bars == "default" else bars
    with closing(ledger.connect(root / "data" / "paper_learning.db")) as conn:
        everything = ledger.load(conn)
        if bars is not None:
            fill_simulations(conn, everything, bars)
        fill_counterfactuals(conn, everything, prices or store_prices(root / "data" / "price_history"))
        frames = frames or hs.store_frames(root / "data" / "price_history")
        hs.fill(conn, everything, frames, root / "output" / "runs")
        records = ledger.load(conn, since, until)
        attach_counterfactuals(conn, records)
        hs.attach(conn, records)
    lab_dir = root / "output" / "strategy_lab"
    lab = json.loads((lab_dir / "strategy_lab_latest.json").read_text(encoding="utf-8")) \
        if (lab_dir / "strategy_lab_latest.json").is_file() else {}
    legacy = json.loads((lab_dir / "strategy_lab_legacy_latest.json").read_text(encoding="utf-8")) \
        if (lab_dir / "strategy_lab_legacy_latest.json").is_file() else {}
    evaluation = evaluate(records, lab=lab, legacy=legacy)
    # the recheck at the open of the ideas the pre-open check took down (owner 2026-10-22): its own report, so the
    # what-if is never mixed with what really happened; only its verdict travels with the evaluation
    recheck = pr.evaluate(records, frames, metrics, MARGIN_PCT)
    evaluation["preopen_recheck"] = {k: recheck[k] for k in ("ideas", "restored", "verdict", "summary")}
    if write_lessons:
        out = root / "output" / "paper_learning"
        out.mkdir(parents=True, exist_ok=True)
        (out / "preopen_recheck_latest.json").write_text(json.dumps(recheck, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "preopen_recheck_latest.md").write_text(pr.render(recheck), encoding="utf-8")
    lessons = record_lessons(root / "data" / "market_intel.db", proposals(evaluation) + pr.proposal(recheck))         if write_lessons else []
    evaluation["lesson_candidates"] = lessons
    evaluation["shadow_lessons"] = shadow_lessons(root / "data" / "market_intel.db", records)
    return evaluation, records, trade_review(records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("evaluate", help="all sessions so far (after each afternoon session)")
    report = sub.add_parser("report", help="a period report")
    report.add_argument("--period", choices=PERIODS)
    report.add_argument("--end", help="a date inside the period (default: yesterday)")
    report.add_argument("--auto", action="store_true", help="the reports due today (nightly start)")
    args = parser.parse_args(argv)
    out = args.root / "output" / "paper_learning"
    out.mkdir(parents=True, exist_ok=True)
    if args.command == "evaluate":
        evaluation, records, review = run_evaluation(args.root)
        (out / "evaluation_latest.json").write_text(json.dumps(evaluation, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "category_findings_latest.json").write_text(json.dumps(  # shared with every agent
            evaluation["category_findings"], ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "live_track_record_latest.json").write_text(json.dumps(
            evaluation["live_track_record_shadow"], ensure_ascii=False, indent=2), encoding="utf-8")
        first = min((r["session_date"] for r in records), default=str(date.today()))
        (out / "evaluation_latest.md").write_text(render_report(
            "day", "μέχρι σήμερα", date.fromisoformat(first), date.today(), evaluation, review,
            evaluation["lesson_candidates"]), encoding="utf-8")
        print(json.dumps({"sessions": evaluation["sessions"], "trades": evaluation["trades"],
                          "lessons": len(evaluation["lesson_candidates"])}))
        return 0
    if args.auto:
        jobs = due_periods(date.today())
    elif args.period:
        jobs = [(args.period, date.fromisoformat(args.end) if args.end else date.today() - timedelta(days=1))]
    else:
        parser.error("report needs --period or --auto")
    reports = out / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    for period, end in jobs:
        start, stop, label = period_bounds(period, end)
        evaluation, records, review = run_evaluation(args.root, since=start, until=stop, write_lessons=False)
        text = render_report(period, label, start, stop, evaluation, review, [])
        (reports / f"{period}_{label}.md").write_text(text, encoding="utf-8")
        (reports / f"{period}_latest.md").write_text(text, encoding="utf-8")
        print(f"[REPORT] {period} {label}: {evaluation['trades']} trades")
    if not jobs:
        print("[REPORT] nothing due today")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
