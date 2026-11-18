"""Which trained models are worth the owner's look (owner 2026-11-17: Jarvis filters, the owner promotes).

Petros retrains every night and registers each model as ``candidate``; dozens pile up that are the same model again.
Only one can be worth a look: the latest walk-forward-validated candidate, and only if it beats the champion's
ROC AUC by ``MIN_GAIN`` (or there is no champion yet). The rest are counted as superseded. Read-only: promotion stays
manual, done with Claude.
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from typing import Any

MIN_GAIN = 0.01
VALIDATED = "walk_forward_validated"
# owner 2026-11-17: only the fundamental-only model can ever be used by Petros (the legacy one mixes trader signals and
# prices, and Petros does not load it); it stays in shadow for MIN_SHADOW_NIGHTS trainings before it is worth a look.
USABLE_TASKS = {("petros", "fundamental_only_synthesis")}
MIN_SHADOW_NIGHTS = 20
# Pre-registered (owner 2026-11-18, RULES_REVIEW section 14) for a model with no champion yet: after the 20 nights the
# latest validated model is worth a look only if its walk-forward ROC AUC is at least MIN_AUC and no validated nightly
# training of the shadow nights fell below FLOOR_AUC (a steady model, not one good night). The first trainings gave
# 0.61. With a champion the rule is unchanged (beat it by MIN_GAIN). Promotion stays manual.
MIN_AUC = 0.60
FLOOR_AUC = 0.55


def _rows(root: Path) -> list[dict[str, Any]] | None:
    path = root / "data" / "market_intel.db"
    if not path.exists():
        return None
    try:
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as conn:
            rows = conn.execute("SELECT model_id, agent_id, task_id, model_version, state, training_rows, metrics_json,"
                                " created_at FROM agent_model_registry ORDER BY created_at").fetchall()
    except sqlite3.Error:
        return None
    out = []
    for model_id, agent, task, version, state, rows_n, metrics, created in rows:
        try:
            m = json.loads(metrics or "{}")
        except ValueError:
            m = {}
        out.append({"model_id": model_id, "agent_id": agent, "task_id": task, "model_version": version,
                    "state": state, "training_rows": rows_n, "roc_auc": m.get("roc_auc"), "status": m.get("status"),
                    "created_at": created})
    return out


def worth(root: Path) -> dict[str, Any] | None:
    rows = _rows(root)
    if rows is None:
        return None
    result: dict[str, Any] = {"candidates": 0, "worth": [], "superseded": 0, "unused": 0, "in_shadow": 0,
                              "not_good_enough": []}
    for key in sorted({(r["agent_id"], r["task_id"]) for r in rows}):
        group = [r for r in rows if (r["agent_id"], r["task_id"]) == key]
        candidates = [r for r in group if r["state"] == "candidate"]
        result["candidates"] += len(candidates)
        if key not in USABLE_TASKS:
            result["unused"] += len(candidates)
            continue
        nights = len({str(r["created_at"])[:10] for r in group})
        if nights < MIN_SHADOW_NIGHTS:
            result["in_shadow"] += len(candidates)
            continue
        champions = [r for r in group if r["state"] == "champion" and isinstance(r["roc_auc"], (int, float))]
        validated = [r for r in candidates if r["status"] == VALIDATED and isinstance(r["roc_auc"], (int, float))]
        pick = None
        if validated:
            latest = validated[-1]
            if not champions:
                floor = min(r["roc_auc"] for r in validated)
                if latest["roc_auc"] >= MIN_AUC and floor >= FLOOR_AUC:
                    pick = {**latest, "reason": f"δεν υπάρχει champion ακόμα· το πιο πρόσφατο ελεγμένο μοντέλο "
                                                f"(ROC AUC {latest['roc_auc']}, χαμηλότερο των νυχτών {floor}, "
                                                f"{latest['training_rows']} γραμμές)"}
                else:
                    result["not_good_enough"].append({
                        **latest, "reason": f"ROC AUC {latest['roc_auc']} (χρειάζεται ≥ {MIN_AUC}), χαμηλότερο των "
                                            f"νυχτών {floor} (χρειάζεται ≥ {FLOOR_AUC})"})
            else:
                champion = champions[-1]
                gain = round(latest["roc_auc"] - champion["roc_auc"], 3)
                if gain >= MIN_GAIN:
                    pick = {**latest, "reason": f"ROC AUC {latest['roc_auc']} έναντι {champion['roc_auc']} του champion "
                                                f"({gain:+g})"}
        if pick:
            result["worth"].append(pick)
        result["superseded"] += len(candidates) - (1 if pick else 0)
    return result
