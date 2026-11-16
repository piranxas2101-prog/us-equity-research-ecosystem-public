"""Fundamental filter on the traders' entries (owner approved 2026-11-16, docs/RULES_REVIEW.md section 10).

A decision is blocked when its company sits at the bad end of a fundamental composite on the run's day:

* a short on a company in the top 20% of ``long_quality`` (cash profitability, improving ROA, not issuing stock);
* a long on a company in the top 20% of ``avoid`` (the mirror image).

The composites are the ones Giannis attaches to each row (``fundamental_factors.composites``), ranked across that
run's Giannis rows. The same rule decides the archived replays (``shadow_replay.py``) and the live traders, so what
was measured is what runs.

What the filter does and does not do:

* It only takes the entry away: ``execution_eligible`` becomes False with a named reason, which Koulis, Thymios and
  the paper trader already honour. The trader's own verdict, direction and score stay as they were, so learning,
  reports and the exits of positions already held (thesis_exit reads the verdicts) see exactly what the trader saw.
* An unknown composite blocks nothing: unknown is not a bad grade.
* ``mode`` "shadow" only tags what it would block; "off" does nothing at all.
"""
from __future__ import annotations

from typing import Any

SCHEMA = "ecosystem.fundamental_filter.v1"
RULE_KEYS = {"short_max_long_quality", "long_max_avoid"}
MODES = {"off", "shadow", "active"}
REASONS = {"short_max_long_quality": "fundamental_filter_short_on_quality",
           "long_max_avoid": "fundamental_filter_long_on_avoid"}
BLOCKABLE_VERDICTS = {"candidate", "watch"}


def validate(config: dict[str, Any] | None, where: str = "fundamental_filter") -> dict[str, Any]:
    """The filter section of a trader's playbook file; missing means off. Bad values raise, never pass silently."""
    if not config:
        return {"mode": "off"}
    mode = config.get("mode")
    if mode not in MODES:
        raise ValueError(f"{where}: mode must be one of {sorted(MODES)}")
    rules = {key: value for key, value in config.items() if key != "mode" and not key.startswith("_")}
    unknown = set(rules) - RULE_KEYS
    if unknown:
        raise ValueError(f"{where}: unknown keys {sorted(unknown)}")
    for key, value in rules.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
            raise ValueError(f"{where}: {key} must be a number from 0 to 100")
    if mode != "off" and not rules:
        raise ValueError(f"{where}: mode {mode} needs at least one rule")
    return {"mode": mode, **{key: float(value) for key, value in rules.items()}}


def composites_of(row: dict[str, Any]) -> dict[str, Any]:
    """The run's composites for one merged Giannis row ({} when unknown)."""
    factors = row.get("fundamental_factors")
    combo = factors.get("composites") if isinstance(factors, dict) else None
    return combo if isinstance(combo, dict) else {}


def blocking_rule(direction: str, combo: dict[str, Any], rules: dict[str, Any]) -> str | None:
    """The rule that blocks this direction for these composites, or None."""
    quality, avoid = combo.get("long_quality"), combo.get("avoid")
    if direction == "short" and "short_max_long_quality" in rules and quality is not None \
            and quality >= rules["short_max_long_quality"]:
        return "short_max_long_quality"
    if direction == "long" and "long_max_avoid" in rules and avoid is not None and avoid >= rules["long_max_avoid"]:
        return "long_max_avoid"
    return None


def apply(signal: dict[str, Any], combo: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Return the signal with the filter's tag, and with the entry taken away when the mode is active."""
    if config.get("mode", "off") == "off":
        return signal
    row = dict(signal)
    direction = str(row.get("trade_direction") or "")
    verdict = str(row.get("signal_verdict") or "").lower()
    rule = blocking_rule(direction, combo, config) if verdict in BLOCKABLE_VERDICTS else None
    row["fundamental_composites"] = {"long_quality": combo.get("long_quality"), "avoid": combo.get("avoid")}
    tag: dict[str, Any] = {"schema": SCHEMA, "mode": config["mode"], "blocked": False, "rule": rule}
    if rule:
        field = "long_quality" if rule == "short_max_long_quality" else "avoid"
        tag.update({"value": combo.get(field), "threshold": config[rule], "reason": REASONS[rule],
                    "eligible_before": row.get("execution_eligible")})
        if config["mode"] == "active":
            tag["blocked"] = True
            row["execution_eligible"] = False
            row["gate_decision_reasons"] = list(dict.fromkeys([*(row.get("gate_decision_reasons") or []),
                                                                REASONS[rule]]))
            row["rationale"] = [*(row.get("rationale") or []),
                                f"Fundamental filter: {field} {combo.get(field)} >= {config[rule]:g}; entry blocked."]
    row["fundamental_filter"] = tag
    return row


def summary(rows: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    """What the filter did in one run, for the trader's payload and the daily report."""
    hit = [r for r in rows if (r.get("fundamental_filter") or {}).get("rule")]
    return {"schema": SCHEMA, "mode": config.get("mode", "off"),
            "rules": {k: v for k, v in config.items() if k in RULE_KEYS},
            "matched": len(hit), "blocked": sum(1 for r in hit if r["fundamental_filter"]["blocked"]),
            "blocked_candidates": sorted({f"{r.get('ticker')}:{r.get('trade_direction')}" for r in hit
                                          if r["fundamental_filter"]["blocked"]
                                          and str(r.get("signal_verdict") or "").lower() == "candidate"})}
