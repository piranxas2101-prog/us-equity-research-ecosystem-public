"""Results journal: every periodic report of every shortlisted company, what it said and how the market took it.

Owner's decision 2026-10-22: the ecosystem itself must keep this journal and learn from it ("every datum must do
a job"). On 2026-10-21 the system saw NFLX open -6.6% after its results but never connected the move with the
results that caused it. This journal does, every night, from data already stored:

* event: a periodic report (10-Q, 10-K, 20-F, 40-F) filed with the SEC, from the companyfacts store - five years
  back and every new one each night, for every listed company (``research_universe.py``); a company whose stored
  companyfacts did not change is not read again. The filing date is close to, not always equal to, the results announcement
  (large companies file the 10-Q a day or more after the press release); the reaction window covers that;
* what it said: the results per period known on that filing date (``periodic_results.analyse`` as of the filing,
  so nothing later leaks in) - revenue growth and its trend, margin changes, losses, dilution, cash runway;
* how the market took it: the announcement window (close before the filing day -> close of the session after it)
  and the drift afterwards (from that close, 5 and 20 sessions), each against SPY over the same days;
* what our agents thought before (only for events since the archived nightly boards start): Giannis's score and
  the traders' verdicts on the night before the filing.

Learning: every tag value is tested against the rest for the announcement reaction and the 5 / 20-session drift
(two-proportion test on beating SPY, Benjamini-Hochberg q <= 0.10 across all tests, the same sign in more than half
of the years with data). New tags join the tests by themselves. A finding becomes a lesson candidate for Giannis
(how to weigh results) - candidate only; promotion stays manual. Unknown stays unknown throughout.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import gzip
import json
import math
from pathlib import Path
import sqlite3
import time
from typing import Any

import numpy as np
import pandas as pd

import periodic_filing
import research_universe
import periodic_results as pr

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "earnings_journal.db"
EVENT_FORMS = frozenset({"10-Q", "10-K", "20-F", "40-F"})
BACKFILL_YEARS = 5
DRIFTS = (5, 20)
FDR_Q = 0.10
MIN_GROUP = 50          # records on each side of a comparison
RUBRIC = "earnings-journal.v1"
TAGS = ("revenue_trend", "gross_margin", "operating_margin", "losses", "dilution", "growth_bucket", "runway_bucket")


def schema(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS earnings_event (accession TEXT PRIMARY KEY, ticker TEXT NOT NULL,"
                 " cik TEXT NOT NULL, form TEXT NOT NULL, filed TEXT NOT NULL, period_end TEXT, cadence TEXT,"
                 " results_json TEXT NOT NULL, tags_json TEXT NOT NULL, agents_json TEXT,"
                 " reaction_pct REAL, drift5_pct REAL, drift20_pct REAL, recorded_at TEXT NOT NULL,"
                 " reaction_done INTEGER NOT NULL DEFAULT 0)")
    # companies whose companyfacts file was read, with its modification time: an unchanged file has no new report
    conn.execute("CREATE TABLE IF NOT EXISTS journal_company (cik TEXT PRIMARY KEY, mtime REAL NOT NULL)")


def filings(raw: dict[str, Any], since: date) -> dict[str, dict[str, str]]:
    """Accession -> {form, filed} of the periodic reports carrying revenue or net income facts."""
    out: dict[str, dict[str, str]] = {}
    for concepts in (pr.FLOWS["revenue"], pr.FLOWS["net_income"]):
        for concept in concepts:
            for taxonomy in ("us-gaap", "ifrs-full"):
                units = (((raw.get("facts") or {}).get(taxonomy) or {}).get(concept) or {}).get("units", {})
                for rows in units.values():
                    for fact in rows or []:
                        filed = pr._day(fact.get("filed"))
                        if fact.get("form") in EVENT_FORMS and fact.get("accn") and filed and filed >= since:
                            out.setdefault(fact["accn"], {"form": fact["form"], "filed": str(filed)})
    return out


def _bucket(value: float | None, edges: tuple[float, ...], names: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    return next((n for e, n in zip(edges, names) if value < e), names[-1])


def event_tags(results: dict[str, Any]) -> dict[str, Any]:
    tags = dict(results.get("tags") or {})
    last = (results.get("periods") or [{}])[-1]
    tags["growth_bucket"] = _bucket(last.get("revenue_yoy_pct"), (-10, 0, 10, 25),
                                    ("<-10%", "-10..0%", "0..10%", "10..25%", ">=25%"))
    tags["runway_bucket"] = _bucket(tags.get("cash_runway_months"), (12, 24), ("<12m", "12-24m", ">=24m"))
    return {k: tags.get(k) for k in TAGS}


def reaction(frame: pd.DataFrame | None, spy: pd.DataFrame, filed: date) -> dict[str, float | None]:
    """Announcement window (close before the filing day -> close of the next session after it) and the drift
    after it, each minus SPY over the same days. A window not stored yet stays None."""
    empty = {"reaction_pct": None, "drift5_pct": None, "drift20_pct": None}
    if frame is None:
        return empty
    closes = frame["Close"].dropna()
    dates = closes.index.date
    base = int((dates < filed).sum()) - 1
    if base < 0:
        return empty
    spy_close = spy["Close"].reindex(closes.index).to_numpy(float)
    c = closes.to_numpy(float)

    def excess(a: int, b: int) -> float | None:
        if b >= len(c) or not (np.isfinite(spy_close[a]) and np.isfinite(spy_close[b])) or c[a] <= 0:
            return None
        return round(float((c[b] / c[a] - 1) * 100 - (spy_close[b] / spy_close[a] - 1) * 100), 3)

    window_end = base + 2  # the filing day and the session after it
    out = {"reaction_pct": excess(base, window_end)}
    for n in DRIFTS:
        out[f"drift{n}_pct"] = excess(window_end, window_end + n)
    return out


def agents_before(boards: list[tuple[date, Any]], ticker: str, filed: date) -> dict[str, Any] | None:
    """The traders' view on the last archived night before the filing day (None before the archive starts)."""
    earlier = [(d, b) for d, b in boards if d <= filed]
    if not earlier or filed - earlier[-1][0] > timedelta(days=4):
        return None
    board = earlier[-1][1]
    view: dict[str, Any] = {"board_date": str(earlier[-1][0])}
    for agent, table in board.items():
        rows = table.get(ticker) or []
        candidates = [r for r in rows if r.get("signal_verdict") == "candidate"]
        view[agent] = {"candidate": [f"{r.get('playbook_name')}:{r.get('trade_direction')}" for r in candidates],
                       "giannis_score": next((r.get("giannis_score") for r in rows), None), "on_board": bool(rows)}
    return view


def update(conn: sqlite3.Connection, tickers: list[str], *, as_of: date, companyfacts: Path, sec_tickers: Path,
           price_dir: Path, runs_dir: Path | None) -> dict[str, int]:
    import price_history_store as phs
    import thesis_exit as te

    schema(conn)
    mapping = periodic_filing.ticker_to_cik(sec_tickers)
    # accession -> (reaction complete, the ticker it belongs to): a company with two share classes files one
    # report, recorded once under the first ticker and measured on that ticker's prices only
    known = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT accession, reaction_done, ticker FROM earnings_event")}
    spy = phs.load("SPY", price_dir)
    boards = te.load_boards(runs_dir) if runs_dir else []
    since = as_of - timedelta(days=365 * BACKFILL_YEARS)
    added = completed = 0
    stamp = datetime.now(timezone.utc).isoformat()
    if spy is None:
        return {"added": 0, "completed": 0}
    # 1. events still waiting for prices: only their price windows are measured again (no companyfacts read)
    waiting: dict[str, list[tuple[str, str]]] = {}
    for accession, ticker, filed in conn.execute(
            "SELECT accession, ticker, filed FROM earnings_event WHERE reaction_done = 0"):
        waiting.setdefault(ticker, []).append((accession, filed))
    for ticker, items in waiting.items():
        frame = phs.load(ticker, price_dir)
        for accession, filed in items:
            moves = reaction(frame, spy, date.fromisoformat(filed))
            finished = int(all(v is not None for v in moves.values()))
            conn.execute("UPDATE earnings_event SET reaction_pct=?, drift5_pct=?, drift20_pct=?, reaction_done=?"
                         " WHERE accession=?", (moves["reaction_pct"], moves["drift5_pct"], moves["drift20_pct"],
                                               finished, accession))
            completed += finished
    conn.commit()
    # 2. new reports: only companies whose companyfacts file changed since it was last read
    seen = {r[0]: r[1] for r in conn.execute("SELECT cik, mtime FROM journal_company")}
    for ticker in tickers:
        cik = mapping.get(ticker.upper())
        path = companyfacts / f"CIK{int(cik):010d}.json.gz" if cik else None
        if path is None or not path.exists():
            continue
        mtime = path.stat().st_mtime
        if seen.get(str(cik)) == mtime:
            continue
        raw = json.loads(gzip.decompress(path.read_bytes()))
        frame = None
        for accession, meta in sorted(filings(raw, since).items(), key=lambda kv: kv[1]["filed"]):
            if accession in known:
                continue  # recorded already, possibly under the other share class of the same company
            filed = date.fromisoformat(meta["filed"])
            if frame is None:
                frame = phs.load(ticker, price_dir)
            moves = reaction(frame, spy, filed)
            finished = int(all(v is not None for v in moves.values()))
            results = pr.analyse(raw, filed)
            last = (results.get("periods") or [{}])[-1]
            conn.execute("INSERT INTO earnings_event VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (accession, ticker, str(cik), meta["form"], meta["filed"], last.get("end"),
                          results.get("cadence"), json.dumps(last, default=str), json.dumps(event_tags(results)),
                          json.dumps(agents_before(boards, ticker, filed), default=str),
                          moves["reaction_pct"], moves["drift5_pct"], moves["drift20_pct"], stamp, finished))
            known[accession] = (finished, ticker)
            added += 1
            completed += finished
        conn.execute("INSERT OR REPLACE INTO journal_company VALUES (?, ?)", (str(cik), mtime))
        seen[str(cik)] = mtime
        conn.commit()
    return {"added": added, "completed": completed}


def _p_two(w1: int, n1: int, w2: int, n2: int) -> float:
    pooled = (w1 + w2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    return 1.0 if se == 0 else math.erfc(abs(w1 / n1 - w2 / n2) / se / math.sqrt(2))


def findings(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute("SELECT filed, tags_json, reaction_pct, drift5_pct, drift20_pct FROM earnings_event").fetchall()
    events = [{"year": r[0][:4], **json.loads(r[1]), "reaction": r[2], "drift5": r[3], "drift20": r[4]} for r in rows]
    tests = []
    for outcome in ("reaction", "drift5", "drift20"):
        known = [e for e in events if e[outcome] is not None]
        # every tag any event carries is tested, so new data (press-release tags, future ones) joins by itself
        tags = sorted({k for e in known for k in e if k not in ("year", "reaction", "drift5", "drift20")})
        for tag in tags:
            for value in sorted({str(e.get(tag)) for e in known if e.get(tag) is not None}):
                group = [e for e in known if str(e.get(tag)) == value]
                rest = [e for e in known if e.get(tag) is not None and str(e.get(tag)) != value]
                if len(group) < MIN_GROUP or len(rest) < MIN_GROUP:
                    continue
                w1, w2 = sum(e[outcome] > 0 for e in group), sum(e[outcome] > 0 for e in rest)
                effect = w1 / len(group) * 100 - w2 / len(rest) * 100
                years = {}
                for e in group:
                    years.setdefault(e["year"], []).append(e[outcome] > 0)
                rest_rate = w2 / len(rest)
                same = sum(1 for v in years.values() if len(v) >= 10 and ((sum(v) / len(v) > rest_rate) == (effect > 0)))
                measured = sum(1 for v in years.values() if len(v) >= 10)
                tests.append({"outcome": outcome, "tag": tag, "value": value, "records": len(group),
                              "beat_spy_pct": round(w1 / len(group) * 100, 1),
                              "rest_beat_spy_pct": round(rest_rate * 100, 1), "effect_pct_points": round(effect, 1),
                              "mean_excess_pct": round(float(np.mean([e[outcome] for e in group])), 3),
                              "years_same_sign": same, "years": measured,
                              "p_value": _p_two(w1, len(group), w2, len(rest))})
    order = sorted(range(len(tests)), key=lambda k: tests[k]["p_value"])
    running = 1.0
    for rank in range(len(order) - 1, -1, -1):
        k = order[rank]
        running = min(running, tests[k]["p_value"] * len(tests) / (rank + 1))
        tests[k]["q_value"] = round(running, 4)
    found = [t for t in tests if t["q_value"] <= FDR_Q and t["years"] and t["years_same_sign"] * 2 > t["years"]]
    return {"events": len(events), "with_reaction": sum(1 for e in events if e["reaction"] is not None),
            "tests": len(tests), "findings": sorted(found, key=lambda t: -abs(t["effect_pct_points"])),
            "fdr_q": FDR_Q, "min_group": MIN_GROUP}


TRADERS = ("yankos", "periklis")


def proposals(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Each finding becomes a lesson candidate for every agent it concerns (owner 2026-10-22): Giannis weighs the
    results; the traders choose entries in the sessions after results (the drift); the position monitors follow
    positions held across a results release (the reaction and the drift). Fixed text per criterion."""
    names = {"reaction": "the announcement reaction", "drift5": "the 5-session drift", "drift20": "the 20-session drift"}
    out = []
    for f in result["findings"]:
        lean = "better" if f["effect_pct_points"] > 0 else "worse"
        key = f"{f['outcome']}:{f['tag']}={f['value']}:{lean}"
        what = f"{names[f['outcome']]} has been {lean} than for other results"
        out.append({"target_agent": "giannis", "criterion_id": f"results:{key}",
                    "proposed_change": f"Weigh results with {f['tag']} = {f['value']}: {what}.",
                    "support": f["records"], "evidence": f})
        if f["outcome"] != "reaction":
            side = "prefer" if lean == "better" else "avoid"
            for trader in TRADERS:
                out.append({"target_agent": trader, "criterion_id": f"results_entry:{key}",
                            "proposed_change": f"After results with {f['tag']} = {f['value']}, {side} long entries in "
                                               f"the following sessions: {what}.",
                            "support": f["records"], "evidence": f})
        action = "hold through" if lean == "better" else "reduce or close before"
        out.append({"target_agent": "position_monitor", "criterion_id": f"results_hold:{key}",
                    "proposed_change": f"For a long position whose company reports results with {f['tag']} = "
                                       f"{f['value']}, {action} the release: {what}.",
                    "support": f["records"], "evidence": f})
    return out


def render(result: dict[str, Any], recent: list[dict[str, Any]]) -> str:
    lines = ["# Ημερολόγιο αποτελεσμάτων · τι είπαν τα αποτελέσματα και πώς αντέδρασε η αγορά", "",
             f"Γεγονότα (10-Q / 10-K / 20-F / 40-F) {result['events']} · με αντίδραση {result['with_reaction']} · "
             f"έλεγχοι {result['tests']} (q ≤ {result['fdr_q']}, ≥ {result['min_group']} ανά πλευρά, ίδιο πρόσημο στις "
             "περισσότερες χρονιές). Αντίδραση: κλείσιμο πριν την κατάθεση → κλείσιμο της επόμενης συνεδρίας· drift: "
             "από εκεί 5 / 20 συνεδρίες, όλα έναντι SPY. Τίποτα δεν αλλάζει απόφαση.", "",
             "## Ευρήματα", "", "| αποτέλεσμα | ετικέτα | τιμή | γεγονότα | νίκησαν SPY % | υπόλοιπα % | διαφορά | μέση υπέρβαση % | χρονιές | q |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    lines += [f"| {f['outcome']} | {f['tag']} | {f['value']} | {f['records']} | {f['beat_spy_pct']} | "
              f"{f['rest_beat_spy_pct']} | {f['effect_pct_points']:+} | {f['mean_excess_pct']:+} | "
              f"{f['years_same_sign']}/{f['years']} | {f['q_value']} |" for f in result["findings"]] or ["| — | | | | | | | | | |"]
    lines += ["", "## Πρόσφατα γεγονότα", "", "| μετοχή | έντυπο | κατάθεση | περίοδος | ανάπτυξη εσόδων % | τάση | λειτ. περιθ. | αντίδραση % | drift 5 % | agents πριν |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for e in recent:
        def v(x: Any) -> str:
            return "άγνωστο" if x is None else str(x)
        agents = e.get("agents") or {}
        seen = "; ".join(f"{a}: {', '.join(x['candidate']) or 'όχι candidate'}" for a, x in agents.items()
                         if isinstance(x, dict)) or "—"
        lines.append(f"| {e['ticker']} | {e['form']} | {e['filed']} | {v(e['period_end'])} | {v(e['growth'])} | "
                     f"{v(e['tags'].get('revenue_trend'))} | {v(e['tags'].get('operating_margin'))} | {v(e['reaction'])} | "
                     f"{v(e['drift5'])} | {seen} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    import price_history_store as phs

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shortlist", type=Path, default=ROOT / "output" / "babis_shortlist_latest.json")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--companyfacts", type=Path, default=periodic_filing.DEFAULT_DIR)
    parser.add_argument("--sec-tickers", type=Path, default=periodic_filing.DEFAULT_TICKERS)
    parser.add_argument("--prices", type=Path, default=phs.DEFAULT_DIR)
    parser.add_argument("--runs", type=Path, default=ROOT / "output" / "runs")
    parser.add_argument("--out", type=Path, default=ROOT / "output")
    parser.add_argument("--no-lessons", action="store_true")
    args = parser.parse_args(argv)
    started = time.perf_counter()
    # every listed company, not only the shortlist (owner 2026-10-22): a company cut at stage 1 keeps its history
    tickers = research_universe.company_tickers(args.shortlist.parent)
    args.db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(args.db)) as conn:
        counts = update(conn, tickers, as_of=date.today(), companyfacts=args.companyfacts, sec_tickers=args.sec_tickers,
                        price_dir=args.prices, runs_dir=args.runs if args.runs.is_dir() else None)
        result = findings(conn)
        recent = [{"ticker": r[0], "form": r[1], "filed": r[2], "period_end": r[3],
                   "growth": json.loads(r[4]).get("revenue_yoy_pct"), "tags": json.loads(r[5]),
                   "agents": json.loads(r[6]) if r[6] else None, "reaction": r[7], "drift5": r[8]}
                  for r in conn.execute("SELECT ticker, form, filed, period_end, results_json, tags_json, agents_json,"
                                        " reaction_pct, drift5_pct FROM earnings_event ORDER BY filed DESC LIMIT 40")]
    lessons = []
    if not args.no_lessons and result["findings"]:
        import paper_learning as pl
        lessons = pl.record_lessons(ROOT / "data" / "market_intel.db", proposals(result), task_id="earnings_journal",
                                    rubric=RUBRIC)
    report = {"schema": "ecosystem.earnings_journal.v1", "generated_at": datetime.now(timezone.utc).isoformat(),
              **counts, **result, "lesson_candidates": lessons, "seconds": round(time.perf_counter() - started, 1),
              "decisions_changed": False}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "earnings_journal_latest.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    (args.out / "earnings_journal_latest.md").write_text(render(result, recent), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("added", "completed", "events", "tests", "seconds")}
                     | {"findings": len(result["findings"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
