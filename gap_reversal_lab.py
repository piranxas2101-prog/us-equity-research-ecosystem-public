"""Gap reversal lab: does a large opening gap reverse, and over which holding period? Research only.

Owner's question 2026-10-22: the ideas the pre-open check takes down after a big gap (ISRG -8.6% at the open on
2026-10-21, then +7% by the close; ANET, TSM, VRT on 2026-10-15) often bounced during the session. Five years of
daily bars from the price store answer whether that is an edge or luck:

* event: the open is beyond the previous close by more than ``MIN_GAP_ATR`` x ATR(14) (ATR up to the previous
  session), on a liquid session (price >= MIN_PRICE, 20-day dollar volume >= MIN_DOLLAR_VOLUME, as in the strategy
  lab);
* trade: the fade, entered at the open - long after a gap down, short after a gap up - held to the close of the
  1st, 5th or 20th session (day / week / month; the fixed hold the exit lab found best);
* buckets: gap size (1-2, 2-3, >= 3 x ATR). The session's volume against its 20-day average (>= 2.5 x, a news
  proxy: the SEC filing index only reaches back to May 2026) is shown as a description only: the day's volume is
  not known at the open, so those cells use the future and are never judged as an edge;
* judged against a random entry: each event is paired with one random liquid session of the same stock, same side
  and hold, entered at the open. The edge is the paired difference of the excess over SPY (same window), with a
  95% month-block bootstrap, a Benjamini-Hochberg control (q <= 0.10) across all cells and per-year stability.

The fade in one direction is the continuation in the other, so a negative edge is a continuation finding. The
daily bars cannot see Miltos's 15-minute confirmation; this measures the raw open-to-close fade.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any

import numpy as np
import pandas as pd

import exit_lab as el
import price_history_store as phs

ROOT = Path(__file__).resolve().parent
CLASSES = {"day": 1, "week": 5, "month": 20}
MIN_GAP_ATR = 1.0
SIZES = ((1.0, 2.0, "1-2 ATR"), (2.0, 3.0, "2-3 ATR"), (3.0, float("inf"), ">=3 ATR"))
NEWS_VOLUME = 2.5
MIN_PRICE, MIN_DOLLAR_VOLUME = 3.0, 2e6
FDR_Q = 0.10


def events_for(frame: pd.DataFrame, spy: pd.DataFrame, rng: np.random.Generator) -> list[tuple]:
    """(date, side, size bucket, news flag, class, excess, random excess) for one stock."""
    frame = frame[["Open", "High", "Low", "Close", "Volume"]].dropna()
    if len(frame) < 60:
        return []
    o, h, low, c, v = (frame[k].to_numpy(float) for k in ("Open", "High", "Low", "Close", "Volume"))
    prev = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - low, np.abs(h - prev), np.abs(low - prev)]), axis=0)
    atr_prev = np.r_[np.nan, (pd.Series(tr).rolling(14).mean().to_numpy() / c * 100)[:-1]]  # known before the open
    dollar = pd.Series(c * v).rolling(20).mean().to_numpy()
    liquid = np.r_[False, ((c >= MIN_PRICE) & (dollar >= MIN_DOLLAR_VOLUME))[:-1]]
    volume_ratio = v / np.r_[np.nan, pd.Series(v).rolling(20).mean().to_numpy()[:-1]]
    gap = (o / prev - 1) * 100
    size = np.abs(gap) / atr_prev
    spy_open = spy["Open"].reindex(frame.index).to_numpy(float)
    spy_close = spy["Close"].reindex(frame.index).to_numpy(float)
    eligible = np.flatnonzero(liquid & np.isfinite(atr_prev))
    rows = []
    for i in np.flatnonzero(liquid & np.isfinite(size) & (size >= MIN_GAP_ATR)):
        side = 1.0 if gap[i] < 0 else -1.0  # the fade
        bucket = next(name for lo, hi, name in SIZES if lo <= size[i] < hi)
        news = bool(np.isfinite(volume_ratio[i]) and volume_ratio[i] >= NEWS_VOLUME)
        k = int(rng.choice(eligible)) if len(eligible) else None
        for cls, n in CLASSES.items():
            def excess(start: int, n: int = n, side: float = side) -> float | None:
                end = start + n - 1
                if end >= len(c) or not (np.isfinite(spy_open[start]) and np.isfinite(spy_close[end])):
                    return None
                own = side * (c[end] / o[start] - 1) * 100
                bench = side * (spy_close[end] / spy_open[start] - 1) * 100
                return own - bench
            mine = excess(i)
            if mine is None:
                continue
            rows.append((frame.index[i], "long" if side > 0 else "short", bucket, news, cls, round(mine, 4),
                         None if k is None else excess(k)))
    return rows


def collect(directory: Path, limit: int = 0, seed: int = 9) -> pd.DataFrame:
    spy = phs.load("SPY", directory)
    if spy is None:
        raise SystemExit("SPY history is missing from the price store")
    spy = spy[["Open", "Close"]].dropna()
    rng = np.random.default_rng(seed)
    symbols = sorted(p.name[:-7] for p in directory.glob("*.csv.gz") if p.name[:-7] not in phs.BENCHMARKS)
    rows = []
    for count, symbol in enumerate(symbols[:limit] if limit else symbols, 1):
        frame = phs.load(symbol, directory)
        if frame is not None:
            rows += [(symbol, *r) for r in events_for(frame, spy, rng)]
        if count % 500 == 0:
            print(f"[GAP-LAB] {count} symbols, {len(rows)} rows", flush=True)
    events = pd.DataFrame(rows, columns=["symbol", "date", "side", "size", "news", "class", "excess", "random_excess"])
    events["random_excess"] = pd.to_numeric(events["random_excess"], errors="coerce")
    return events


def summarize(events: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    events = events.assign(month=pd.to_datetime(events["date"]).dt.to_period("M"))
    groups = [("all", events), ("high_volume_day", events[events["news"]]), ("normal_volume_day", events[~events["news"]])]
    for label, part in groups:
        for (side, size, cls), cell in part.groupby(["side", "size", "class"]):
            paired = cell.dropna(subset=["random_excess"])
            diffs = paired["excess"] - paired["random_excess"]
            entry = {"volume": label, "side": side, "size": size, "class": cls, "n": int(len(cell)),
                     "mean_excess_pct": round(float(cell["excess"].mean()), 3),
                     "beat_spy_pct": round(float((cell["excess"] > 0).mean() * 100), 1)}
            test = el._block_test(diffs.to_numpy(), paired["month"].to_numpy()) if len(paired) else None
            if test:
                years = pd.to_datetime(paired["date"]).dt.year
                by_year = diffs.groupby(years.to_numpy()).agg(["mean", "count"])
                by_year = by_year[by_year["count"] >= el.MIN_YEAR_PAIRS]
                entry.update({"edge_vs_random_pct": test["mean"], "edge_ci": test["ci"], "p_value": test["p"],
                              "by_year": {int(y): round(float(m), 3) for y, m in by_year["mean"].items()},
                              "years": int(len(by_year)), "years_positive": int((by_year["mean"] > 0).sum())})
            out.append(entry)
    tested = [e for e in out if "p_value" in e]
    for e, q in zip(tested, el._bh([e["p_value"] for e in tested])):
        e["q_value"] = round(q, 4)
        stable = e["years_positive"] * 2 > e["years"] if e["edge_vs_random_pct"] > 0 else \
            (e["years"] - e["years_positive"]) * 2 > e["years"]
        e["verdict"] = ("fade edge" if q <= FDR_Q and e["edge_vs_random_pct"] > 0 and stable else
                        "continuation edge" if q <= FDR_Q and e["edge_vs_random_pct"] < 0 and stable else
                        "no stable edge")
        if e["volume"] != "all":  # the day's volume is not known at the open: description, never an edge
            e["verdict"] = "descriptive: " + e["verdict"]
        e["p_value"] = round(e["p_value"], 5)
    return out


def render(report: dict[str, Any]) -> str:
    names = {"day": "ημέρα", "week": "εβδομάδα", "month": "μήνας"}
    sides = {"long": "long μετά από gap κάτω", "short": "short μετά από gap πάνω"}
    lines = ["# Εργαστήριο αναστροφής gap · 5 χρόνια ημερήσιων τιμών", "",
             f"Σύμβολα {report['symbols']} · γεγονότα (gap ≥ {MIN_GAP_ATR}×ATR στο άνοιγμα) {report['events']} · "
             f"γραμμές {report['rows']}. Είσοδος στο άνοιγμα, αντίθετα στο gap· έξοδος στο κλείσιμο της 1ης / 5ης / "
             "20ής συνεδρίας. Πλεονέκτημα = υπέρβαση SPY έναντι τυχαίας εισόδου στην ίδια μετοχή (ίδια πλευρά και "
             f"διάρκεια), με έλεγχο τυχαίων ευρημάτων (q ≤ {FDR_Q}) και σταθερότητα ανά χρονιά. «high_volume_day»: όγκος "
             f"της μέρας ≥ {NEWS_VOLUME}× του μέσου 20 ημερών· μόνο περιγραφικό, γιατί ο όγκος της μέρας δεν είναι "
             "γνωστός στο άνοιγμα (το SEC index δεν φτάνει 5 χρόνια πίσω για τα αποτελέσματα).", "",
             "| όγκος | πλευρά | μέγεθος gap | διάρκεια | γεγονότα | υπέρβαση SPY % | νίκησαν SPY % | πλεονέκτημα έναντι τυχαίας % (95%) | θετικές χρονιές | συμπέρασμα |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    order = {"day": 0, "week": 1, "month": 2}
    for e in sorted(report["results"], key=lambda e: (e["volume"], e["side"], e["size"], order[e["class"]])):
        edge = "—" if "edge_vs_random_pct" not in e else \
            f"{e['edge_vs_random_pct']:+.2f} ({e['edge_ci'][0]:+.2f} έως {e['edge_ci'][1]:+.2f})"
        years = "—" if "years" not in e else f"{e['years_positive']}/{e['years']}"
        lines.append(f"| {e['volume']} | {sides[e['side']]} | {e['size']} | {names[e['class']]} | {e['n']} | "
                     f"{e['mean_excess_pct']:+.2f} | {e['beat_spy_pct']} | {edge} | {years} | {e.get('verdict', '—')} |")
    lines += ["", "Αρνητικό πλεονέκτημα = το gap συνεχίζει (continuation), όχι αναστροφή. Μόνο ημερήσιες τιμές: δεν "
              "βλέπει την επιβεβαίωση του Miltos στα 15λεπτα. Τίποτα δεν αλλάζει χωρίς την έγκριση του ιδιοκτήτη."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=phs.DEFAULT_DIR)
    parser.add_argument("--out", type=Path, default=ROOT / "output" / "strategy_lab")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)
    started = time.perf_counter()
    events = collect(args.dir, args.limit)
    results = summarize(events) if len(events) else []
    report = {"schema": "ecosystem.gap_reversal_lab.v1", "generated_at": datetime.now(timezone.utc).isoformat(),
              "symbols": int(events["symbol"].nunique()) if len(events) else 0,
              "events": int(events.drop_duplicates(["symbol", "date"]).shape[0]) if len(events) else 0,
              "rows": int(len(events)), "parameters": {"min_gap_atr": MIN_GAP_ATR, "news_volume": NEWS_VOLUME},
              "results": results, "seconds": round(time.perf_counter() - started, 1)}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "gap_reversal_lab_latest.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (args.out / "gap_reversal_lab_latest.md").write_text(render(report), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("symbols", "events", "rows", "seconds")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
