"""How long a trader agent plans to hold a position (owner 2026-10-23: the agents decide which positions open and
for how long - five hours, three days, a week - and the monitors close them whenever their rules say so).

Each trader (Yankos, Periklis) writes its horizon into the position's frozen trade plan, decided from evidence: the
exit lab's horizon curve (``exit_lab.py --horizon-curve``: the fixed hold of every strategy and side on 1, 2, 3, 5,
7, 10, 15, 20, 30, 40 and 60 sessions against a random entry of the same stock, FDR q <= 0.10, per year). Rule:

* only horizons with a real edge count: significant after the FDR control and positive in more than half of the
  years it can be measured (the exit lab's ``real_edges`` test);
* among them the agent takes the one with the most edge per unit of risk - edge / sqrt(sessions), since a price's
  spread grows with the square root of time - so a slightly larger edge does not buy a much longer exposure;
* no horizon with a real edge (or no curve at all) is not a guess: the position stays a same-day position, open
  for the hours left in the session and flat 5 minutes before the close.

The horizon is a ceiling, not a promise: the monitors still close earlier on the widening stop, a broken thesis
or an abrupt change of the market's mood (alpaca_multi_day_monitor.py). It is counted in trading minutes from the
fill (``trading_clock``). Research input only: the decision is the agent's plan; Koulis and Thymios still gate it.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import trading_clock

ROOT = Path(__file__).resolve().parent
CURVE = ROOT / "output" / "strategy_lab" / "horizon_curve_latest.json"
RULE = "agent_horizon"
SAME_DAY = "same_day_flat"


def load_curve(path: Path = CURVE) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """(strategy, side) -> the horizons with a real edge, from the latest horizon curve ({} when missing)."""
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    horizons = report.get("horizons") or {}
    curve: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in report.get("results") or []:
        sessions = horizons.get(entry.get("class"))
        if (entry.get("rule") != "fixed" or not sessions or entry.get("edge_verdict") != "edge"
                or entry.get("years_positive", 0) * 2 <= entry.get("years", 0)):
            continue
        curve.setdefault((entry["strategy"], entry["side"]), []).append(
            {"sessions": int(sessions), "edge_pct": float(entry["edge_vs_random_pct"]),
             "edge_ci": entry.get("edge_ci"), "years": f"{entry.get('years_positive')}/{entry.get('years')}",
             "n": entry.get("n")})
    return curve


def decide(playbook: str, direction: str, curve: dict[tuple[str, str], list[dict[str, Any]]],
           source: str = "horizon_curve_latest.json") -> dict[str, Any]:
    """The agent's horizon for one position: rule, sessions, trading minutes and the evidence behind it."""
    options = [o for o in curve.get((str(playbook), str(direction).lower()), []) if o["edge_pct"] > 0]
    if not options:
        return {"rule": SAME_DAY, "sessions": None, "minutes": None,
                "basis": "no_stable_edge_at_any_horizon" if curve else "no_horizon_curve", "source": source}
    best = max(options, key=lambda o: (o["edge_pct"] / math.sqrt(o["sessions"]), -o["sessions"]))
    return {"rule": RULE, "sessions": best["sessions"], "minutes": best["sessions"] * trading_clock.SESSION_MINUTES,
            "basis": "max_edge_per_sqrt_session", "edge_pct": round(best["edge_pct"], 3),
            "edge_ci": best["edge_ci"], "years_positive": best["years"], "n": best["n"],
            "alternatives": sorted((o["sessions"], round(o["edge_pct"], 3)) for o in options), "source": source}


def valid(horizon: Any) -> bool:
    """A horizon a monitor can act on: whole sessions 1-60 and the matching trading minutes."""
    if not isinstance(horizon, dict) or horizon.get("rule") != RULE:
        return False
    sessions, minutes = horizon.get("sessions"), horizon.get("minutes")
    return (isinstance(sessions, int) and 1 <= sessions <= 60
            and minutes == sessions * trading_clock.SESSION_MINUTES)


def rules_class(sessions: int) -> str:
    """Which thesis rules of thesis_exit fit the horizon: technical up to a week, both up to a month, fundamental
    beyond (the same split as the holding classes)."""
    return "week" if sessions <= 5 else "month" if sessions <= 20 else "quarter"
