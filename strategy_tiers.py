"""Three tiers of strategies (owner's choice 3 for T5, 2026-10-26; docs/T5_STRATEGY_EVIDENCE_2026-10-24.md).

* **horizon** - a stable edge over a random entry of the same stock at some holding period (the exit lab's horizon
  curve): the position opens and is held for its agent's horizon (holding_horizon.py).
* **same_day_measured** - no stable edge at any horizon, and not worse than a random entry within the first days:
  the position opens and closes the same day, and is judged on the ledger (``review``).
* **shadow_only** - ``paper_session.shadow_only_strategies`` in scanner_config.json (``playbook:direction``):
  worse than a random entry already within the first days (gap_continuation long, mean_reversion_overextension
  long, gap_fade long). Koulis places no order; the candidates are still recorded and learned from.

``review``: a same-day strategy is judged once it has ``REVIEW_TRADES`` trades from ``REVIEW_SESSIONS`` sessions.
Its live trades are compared with the same strategy's candidates that got no order on the same days (the live
"random" comparison): beating SPY clearly less often (two-proportion test, p < ``REVIEW_P``) proposes shadow-only;
clearly more often proposes a lab check for a holding horizon. A proposal only: the owner decides, as with every
lesson (candidate -> shadow -> champion).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import holding_horizon

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "scanner_config.json"
REVIEW_TRADES = 30
REVIEW_SESSIONS = 20
REVIEW_P = 0.05


def shadow_only(config: Path = CONFIG) -> set[str]:
    try:
        items = (json.loads(config.read_text(encoding="utf-8-sig")).get("paper_session") or {}).get(
            "shadow_only_strategies")
    except (OSError, ValueError):
        items = None
    return {str(item) for item in items or []}


def tier(playbook: str, direction: str, curve: dict, shadow: set[str]) -> str:
    if f"{playbook}:{direction}" in shadow:
        return "shadow_only"
    if any(option["edge_pct"] > 0 for option in curve.get((playbook, direction), [])):
        return "horizon"
    return "same_day_measured"


def _beats(rows: list[dict[str, Any]]) -> tuple[int, int]:
    known = [r["excess_pct"] for r in rows if r.get("excess_pct") is not None]
    return sum(1 for x in known if x > 0), len(known)


def _p_two_proportions(w1: int, n1: int, w2: int, n2: int) -> float | None:
    if not n1 or not n2:
        return None
    pooled = (w1 + w2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return None
    z = (w1 / n1 - w2 / n2) / se
    return math.erfc(abs(z) / math.sqrt(2))


def review(trades: list[dict[str, Any]], signals: list[dict[str, Any]], *, curve: dict | None = None,
           shadow: set[str] | None = None) -> list[dict[str, Any]]:
    curve = holding_horizon.load_curve() if curve is None else curve
    shadow = shadow_only() if shadow is None else shadow
    keys = {(r.get("agent"), r.get("playbook"), r.get("direction")) for r in trades + signals}
    out = []
    for agent, playbook, direction in sorted(k for k in keys if all(k)):
        level = tier(playbook, direction, curve, shadow)
        mine = [r for r in trades if (r.get("agent"), r.get("playbook"), r.get("direction")) == (agent, playbook, direction)
                and r.get("exit_reason") != "held_on_horizon"]
        days = {r["session_date"] for r in mine if r.get("excess_pct") is not None}
        others = [r for r in signals if (r.get("agent"), r.get("playbook"), r.get("direction")) == (agent, playbook, direction)
                  and r["session_date"] in days]
        w1, n1 = _beats(mine)
        w2, n2 = _beats(others)
        entry = {"agent": agent, "playbook": playbook, "direction": direction, "tier": level,
                 "trades": n1, "sessions": len(days), "trades_beat_spy_pct": round(100 * w1 / n1, 1) if n1 else None,
                 "candidates_same_days": n2, "candidates_beat_spy_pct": round(100 * w2 / n2, 1) if n2 else None}
        if level != "same_day_measured":
            entry["verdict"] = level
        elif n1 < REVIEW_TRADES or len(days) < REVIEW_SESSIONS or n2 < REVIEW_TRADES:
            entry["verdict"] = "measuring"
        else:
            p = _p_two_proportions(w1, n1, w2, n2)
            entry["p_value"] = None if p is None else round(p, 4)
            entry["verdict"] = ("propose_shadow_only" if p is not None and p < REVIEW_P and w1 / n1 < w2 / n2 else
                                "propose_lab_check" if p is not None and p < REVIEW_P and w1 / n1 > w2 / n2 else
                                "no_clear_difference")
        out.append(entry)
    return out


VERDICT_GR = {"horizon": "με διάρκεια agent", "shadow_only": "μόνο καταγραφή", "measuring": "μετριέται",
              "propose_shadow_only": "ΠΡΟΤΑΣΗ: μόνο καταγραφή", "propose_lab_check": "ΠΡΟΤΑΣΗ: έλεγχος διάρκειας",
              "no_clear_difference": "καμία σαφής διαφορά"}


def render(entries: list[dict[str, Any]]) -> list[str]:
    lines = ["", "## Επίπεδα στρατηγικών (T5)", "",
             f"Μια στρατηγική της ίδιας μέρας κρίνεται με ≥{REVIEW_TRADES} trades σε ≥{REVIEW_SESSIONS} συνεδρίες, "
             "έναντι των υποψηφίων της χωρίς εντολή τις ίδιες μέρες. Το σύστημα μόνο προτείνει· αποφασίζει ο ιδιοκτήτης.",
             "", "| trader · στρατηγική · κατεύθυνση | επίπεδο | trades | συνεδρίες | νίκησαν SPY % | υποψήφια % | κρίση |",
             "|---|---|---|---|---|---|---|"]
    for e in entries:
        lines.append(f"| {e['agent']} · {e['playbook']} · {e['direction']} | {e['tier']} | {e['trades']} | {e['sessions']} | "
                     f"{e['trades_beat_spy_pct'] if e['trades_beat_spy_pct'] is not None else '-'} | "
                     f"{e['candidates_beat_spy_pct'] if e['candidates_beat_spy_pct'] is not None else '-'} | "
                     f"{VERDICT_GR.get(e['verdict'], e['verdict'])} |")
    return lines
