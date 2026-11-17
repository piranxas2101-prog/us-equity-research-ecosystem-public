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
    result: dict[str, Any] = {"candidates": 0, "worth": [], "superseded": 0}
    for key in sorted({(r["agent_id"], r["task_id"]) for r in rows}):
        group = [r for r in rows if (r["agent_id"], r["task_id"]) == key]
        candidates = [r for r in group if r["state"] == "candidate"]
        result["candidates"] += len(candidates)
        champions = [r for r in group if r["state"] == "champion" and isinstance(r["roc_auc"], (int, float))]
        validated = [r for r in candidates if r["status"] == VALIDATED and isinstance(r["roc_auc"], (int, float))]
        pick = None
        if validated:
            latest = validated[-1]
            if not champions:
                pick = {**latest, "reason": f"δεν υπάρχει champion ακόμα· το πιο πρόσφατο ελεγμένο μοντέλο "
                                            f"(ROC AUC {latest['roc_auc']}, {latest['training_rows']} γραμμές)"}
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
