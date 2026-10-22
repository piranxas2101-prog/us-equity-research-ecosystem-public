#!/usr/bin/env python3
"""
nightly_report.py
Input: JSON file with Daily Close Pack (see spec)
Output: Markdown nightly report to stdout (or to file with >)

Usage:
    python nightly_report.py inputs/sample.json > outputs/sample_report.md
"""
import sys
import json
import math
from datetime import datetime

def fmt(x, n=2):
    try:
        if x is None:
            return "N/A"
        if isinstance(x, int):
            return str(x)
        if abs(x) >= 1000 and isinstance(x, (int, float)):
            return f"{x:,.0f}"
        return f"{x:.{n}f}"
    except Exception:
        return str(x)

def safe(d, *keys):
    cur = d
    for k in keys:
        if cur is None: return None
        cur = cur.get(k)
    return cur

def compute_targets(er, atr):
    # Targets: 1x, 2x, 4x ATR moves above ER
    return [er + 1*atr, er + 2*atr, er + 4*atr]

def generate_report(data):
    date = data.get("date")
    ticker = data.get("ticker", "TICKER")
    ohlc = data.get("ohlc", {})
    vol = data.get("volume", {})
    atr14 = data.get("atr14", None)
    ema = data.get("ema", {})
    notes = data.get("notes", "")
    position = data.get("position", {})

    close = ohlc.get("close")
    high = ohlc.get("high")
    low = ohlc.get("low")

    ema20 = ema.get("ema20")
    ema50 = ema.get("ema50")
    ema200 = ema.get("ema200")

    # Tech Mid/Low/High
    tech_mid = None
    tech_low = None
    tech_high = None
    if ema20 is not None and ema200 is not None and atr14 is not None:
        tech_mid = (ema20 + ema200) / 2.0
        tech_low = tech_mid - 1.5 * atr14
        tech_high = tech_mid + 1.5 * atr14

    # Structure Log heuristics
    APL = low  # active pivot low: today's low (placeholder if no history)
    APH = high # active pivot high: today's high
    SPL = ema200

    # Support/Resistance ladder
    support_ladder = []
    resistance_ladder = []

    if ema200 is not None:
        support_ladder.append(("EMA200", ema200))
    if APL is not None:
        support_ladder.append(("Pivot Low (today)", APL))
    if ema50 is not None:
        support_ladder.append(("EMA50", ema50))
    if ema20 is not None:
        support_ladder.append(("EMA20", ema20))

    if APH is not None:
        resistance_ladder.append(("Todays High", APH))
    for o in position.get("open_orders", []):
        if isinstance(o, dict) and o.get("side") and o.get("price"):
            resistance_ladder.append((f"Open order ({o.get('side')})", o.get("price")))
    if close is not None:
        r1 = math.ceil(close*2)/2.0
        r2 = math.ceil(close)
        resistance_ladder.append(("Round(s)", r1))
        if r2 != r1:
            resistance_ladder.append(("Round", r2))
    if tech_high is not None:
        resistance_ladder.append(("Tech High (ATR zone)", tech_high))

    # Regime classification
    regime = "Range"
    try:
        if close is not None and ema50 is not None and ema200 is not None:
            if close > ema50 and ema50 > ema200:
                regime = "Trend (bull)"
            elif close < ema200:
                regime = "Breakdown (bear)"
            else:
                regime = "Range / Chop"
    except Exception:
        regime = "Unknown"

    # Reclaim / No-Add thresholds (heuristic)
    reclaim_rules = {}
    pivot_support_zone = min([x[1] for x in support_ladder if isinstance(x[1], (int, float))] or [None])
    reclaim_rules['No-Add'] = {"condition": "Close below pivot low and no reclaim above support zone",
                               "threshold": fmt(APL) + " (pivot low)"}
    reclaim_rules['Reclaim A'] = {"condition": "Wick below pivot but close back above support zone",
                                  "threshold": f"Close > {fmt(pivot_support_zone)}"}
    reclaim_rules['Reclaim B'] = {"condition": "Close above structure zone (support->resistance)",
                                  "threshold": f"Close > {fmt(tech_mid) if tech_mid else 'N/A'}"}
    reclaim_rules['Reclaim C'] = {"condition": "2 closes without new low (needs history)",
                                  "threshold": "N/A (requires multi-day data)"}

    # Risk/Reward snapshot
    ER = None
    if position and position.get("total_shares", 0) > 0:
        ER = position.get("avg_cost") or close
    else:
        ER = close
    SR = None
    if atr14 is not None and low is not None:
        SR = low - 0.5 * atr14
    if ema200 is not None:
        SR = min(SR, ema200) if SR is not None else ema200

    targets = []
    rrs = []
    if ER is not None and atr14 is not None:
        targets = compute_targets(ER, atr14)
        risk = ER - SR if (ER is not None and SR is not None) else None
        for t in targets:
            rr = (t - ER) / risk if risk and risk != 0 else None
            rrs.append(rr)

    # Playbook (concise)
    playbook = {'Bull': [], 'Base': [], 'Bear': []}
    if tech_high is not None and close is not None:
        playbook['Bull'].append(f"Target add / hold until {fmt(tech_high)} zone; favors trend entries.")
    playbook['Base'].append(f"Between {fmt(tech_low) if tech_low else 'N/A'} and {fmt(tech_high) if tech_high else 'N/A'}: range trades, tight RR.")
    playbook['Bear'].append(f"Close below {fmt(tech_low) if tech_low else 'N/A'} or EMA200: avoid adds, consider trimming / stop.")

    news_note = "No new material items found."

    # Build Markdown
    md = []
    md.append(f"# Nightly Report — {ticker} — {date}")
    md.append(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n")

    md.append("## 1) Close recap")
    md.append(f"- OHLC: O {fmt(ohlc.get('open'))}  H {fmt(high)}  L {fmt(low)}  C {fmt(close)}")
    md.append(f"- Volume: {fmt(vol.get('value'))}  AvgVol: {fmt(vol.get('avg'))}  (Vol/Avg: {fmt((vol.get('value')/(vol.get('avg') or 1)) if vol.get('avg') else None,2)})")
    md.append(f"- ATR14: {fmt(atr14)}  EMA20: {fmt(ema20)}  EMA50: {fmt(ema50)}  EMA200: {fmt(ema200)}")
    tape_one_liner = notes or "Tape: neutral"
    md.append(f"- Tape read: {tape_one_liner}\n")

    md.append("## 2) Tech Mid / Low / High")
    md.append(f"- Tech Mid = (EMA20 + EMA200)/2 = {fmt(tech_mid)}")
    md.append(f"- Tech Low = TechMid - 1.5*ATR14 = {fmt(tech_low)}")
    md.append(f"- Tech High = TechMid + 1.5*ATR14 = {fmt(tech_high)}\n")

    md.append("## 3) Structure Log (MSL)")
    md.append(f"- APL (Active Pivot Low): {fmt(APL)}")
    md.append(f"- SPL (Structural Pivot Low, approx): {fmt(SPL)}")
    md.append(f"- APH (Active Pivot High): {fmt(APH)}")
    md.append("- Support ladder (top->bottom):")
    for name, val in support_ladder:
        md.append(f"  - {name}: {fmt(val)}")
    md.append("- Resistance ladder (priority):")
    for name, val in resistance_ladder:
        md.append(f"  - {name}: {fmt(val)}")
    md.append(f"- Regime: {regime}\n")

    md.append("## 4) Reclaim / No-Add")
    for k, v in reclaim_rules.items():
        md.append(f"- {k}: {v['condition']}  — threshold: {v['threshold']}")
    md.append("")

    md.append("## 5) RR snapshot vs targets")
    md.append(f"- ER (Entry Reference): {fmt(ER)}")
    md.append(f"- SR (Stop / Invalidation): {fmt(SR)}")
    md.append(f"- Risk per share = ER - SR = {fmt((ER - SR) if (ER is not None and SR is not None) else None)}")
    for i, t in enumerate(targets):
        rr = rrs[i] if i < len(rrs) else None
        md.append(f"- Target T{i+1}: {fmt(t)}  Reward = {fmt(t - ER)}  RR = {fmt(rr,2)}")
    md.append("")

    md.append("## 6) Playbook")
    md.append("- Bull:")
    for s in playbook['Bull']:
        md.append(f"  - {s}")
    md.append("- Base:")
    for s in playbook['Base']:
        md.append(f"  - {s}")
    md.append("- Bear:")
    for s in playbook['Bear']:
        md.append(f"  - {s}")
    md.append("")

    md.append("## News")
    md.append(f"- {news_note}\n")

    md.append("## Position (user)")
    md.append(f"- Total shares: {fmt(position.get('total_shares',0),0)}  Avg cost: {fmt(position.get('avg_cost'))}")
    md.append(f"- Core shares: {fmt(position.get('core_shares',0),0)}")
    if position.get('open_orders'):
        md.append("- Open orders:")
        for o in position.get('open_orders', []):
            md.append(f"  - {o.get('side','?')} {fmt(o.get('shares',0),0)} @ {fmt(o.get('price'))}")
    else:
        md.append("- Open orders: none")
    md.append("")

    return "\n".join(md)

def main():
    if len(sys.argv) < 2:
        print("Usage: python nightly_report.py daily_close.json")
        sys.exit(1)
    fn = sys.argv[1]
    with open(fn, "r") as f:
        data = json.load(f)
    report = generate_report(data)
    print(report)

if __name__ == "__main__":
    main()
