"""Jarvis learning: loss rules from the paper trade ledger (design 5.2).

An interpretable model (a shallow scikit-learn decision tree) is fitted on the older sessions to tell losing trades
from the rest; each leaf's positive conditions ("agent=periklis", "playbook=gap_fade", ...) form a candidate rule,
kept only when it also loses often on the newer sessions it never saw. A kept rule enters the learning store as a
lesson, which goes straight to shadow (owner 2026-11-05): it is followed on the sessions after that, changes no score,
policy, limit or order, and reaches the owner as a proposal once its follow has a verdict.

Starting values below are Claude's call (2026-11-15), to be revisited with the first real numbers.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from jarvis_state import Proposal

MIN_SESSIONS = 10
TRAIN_SHARE = 0.7
MAX_DEPTH = 3
MIN_LEAF = 10
TRAIN_LOSS_RATE = 0.65
TEST_MIN = 5
TEST_LOSS_RATE = 0.60
FOLLOW_MIN = 10
TASK_ID = "jarvis_learning"
RUBRIC = "jarvis-learning.v1"
PREFIX = "jarvis_loss_rule:"
FEATURES = ("agent", "playbook", "direction", "holding_class", "market_state", "market_state_fit", "standing")


def _feature(record: dict[str, Any], name: str) -> str:
    value = (record.get("categories") or {}).get(name) if name == "standing" else record.get(name)
    if isinstance(value, dict):
        value = value.get("state")
    return "unknown" if value in (None, "") else str(value)


def _trade(record: dict[str, Any]) -> dict[str, Any] | None:
    result = record.get("result_pct")
    if isinstance(result, bool) or not isinstance(result, (int, float)):
        return None
    return {"session_date": str(record["session_date"]), "result_pct": float(result),
            **{name: _feature(record, name) for name in FEATURES}}


def normalise(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ledger records or already-flat trades -> flat trades with every feature (missing ones "unknown")."""
    flat = (t if all(name in t for name in FEATURES) else _trade(t) for t in trades)
    return [t for t in flat if t is not None and isinstance(t.get("result_pct"), (int, float))]


def mature(record: dict[str, Any], as_of: date) -> bool:
    """The same rule as paper_learning.maturity (owner 2026-11-15): a held trade is judged only once its planned
    window has passed, since before that the closed ones are mostly those a stop took out (a bias towards losses).
    A same-day trade, or one without a planned horizon, is mature at once."""
    horizon = record.get("holding_horizon_sessions")
    if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon <= 1:
        return True
    import trading_clock

    return trading_clock.sessions_after(date.fromisoformat(str(record["session_date"])), as_of) >= horizon


def load_trades(db_path: Path, *, as_of: date | None = None) -> list[dict[str, Any]]:
    """Filled paper trades with a known result, read straight from the ledger (no broker code is imported); with
    ``as_of``, only the mature ones."""
    if not db_path.exists():
        return []
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as conn:
        rows = conn.execute("SELECT record_json FROM paper_trade_ledger WHERE kind='trade'"
                            " ORDER BY session_date, record_id").fetchall()
    records = [json.loads(r[0]) for r in rows]
    if as_of is not None:
        records = [r for r in records if mature(r, as_of)]
    return [t for t in (_trade(r) for r in records) if t is not None]


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    losses = sum(1 for r in rows if r["result_pct"] < 0)
    return {"n": len(rows), "losses": losses, "loss_rate_pct": round(100 * losses / len(rows), 1) if rows else None}


def _matches(row: dict[str, Any], conditions: dict[str, str]) -> bool:
    return all(row.get(k) == v for k, v in conditions.items())


def criterion_id(conditions: dict[str, str]) -> str:
    return PREFIX + "|".join(f"{k}={conditions[k]}" for k in sorted(conditions))


def parse_criterion(criterion: str) -> dict[str, str]:
    body = criterion[len(PREFIX):] if criterion.startswith(PREFIX) else ""
    return dict(part.split("=", 1) for part in body.split("|") if "=" in part)


def _leaf_conditions(model: Any, matrix: Any, rows: list[dict[str, Any]], names: list[str]) -> list[dict[str, str]]:
    """Per leaf, the features the tree split on along its path, each turned into an equality condition when every
    training row in the leaf has the same value for it ("direction != long" with only shorts left -> direction=short).
    A feature that still has several values in the leaf is left out, so a rule is always a plain "k=v and ..." group."""
    tree = model.tree_
    paths: dict[int, set[str]] = {}

    def walk(node: int, keys: frozenset[str]) -> None:
        left, right = tree.children_left[node], tree.children_right[node]
        if left == right:
            paths[node] = set(keys)
            return
        key = names[tree.feature[node]].split("=", 1)[0]
        walk(left, keys | {key})
        walk(right, keys | {key})

    walk(0, frozenset())
    members: dict[int, list[dict[str, Any]]] = {}
    for leaf, row in zip(model.apply(matrix), rows):
        members.setdefault(int(leaf), []).append(row)
    out = []
    for leaf, keys in sorted(paths.items()):
        inside = members.get(leaf, [])
        conditions = {k: inside[0][k] for k in sorted(keys) if inside and len({r[k] for r in inside}) == 1}
        out.append(conditions)
    return out


def find_rules(trades: list[dict[str, Any]]) -> dict[str, Any]:
    trades = normalise(trades)
    sessions = sorted({t["session_date"] for t in trades})
    if len(sessions) < MIN_SESSIONS:
        return {"status": "not_enough_data", "sessions": len(sessions), "minimum_sessions": MIN_SESSIONS, "rules": []}
    cut = max(1, int(len(sessions) * TRAIN_SHARE))
    train_days, test_days = set(sessions[:cut]), set(sessions[cut:])
    train = [t for t in trades if t["session_date"] in train_days]
    test = [t for t in trades if t["session_date"] in test_days]
    out: dict[str, Any] = {"status": "ok", "sessions": len(sessions), "train_sessions": [sessions[0], sessions[cut - 1]],
                           "test_sessions": [sessions[cut], sessions[-1]], "rules": []}
    labels = [1 if t["result_pct"] < 0 else 0 for t in train]
    if len(set(labels)) < 2:
        return out
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.tree import DecisionTreeClassifier

    vectorizer = DictVectorizer(sparse=False, separator="=")
    matrix = vectorizer.fit_transform([{name: t[name] for name in FEATURES} for t in train])
    model = DecisionTreeClassifier(max_depth=MAX_DEPTH, min_samples_leaf=MIN_LEAF, class_weight="balanced",
                                   random_state=0).fit(matrix, labels)
    seen: set[str] = set()
    for conditions in _leaf_conditions(model, matrix, train, list(vectorizer.get_feature_names_out())):
        if not conditions:
            continue
        cid = criterion_id(conditions)
        if cid in seen:
            continue
        seen.add(cid)
        a = _stats([t for t in train if _matches(t, conditions)])
        b = _stats([t for t in test if _matches(t, conditions)])
        if (a["n"] >= MIN_LEAF and a["loss_rate_pct"] >= 100 * TRAIN_LOSS_RATE
                and b["n"] >= TEST_MIN and b["loss_rate_pct"] >= 100 * TEST_LOSS_RATE):
            out["rules"].append({"criterion_id": cid, "conditions": conditions, "train": a, "test": b})
    return out


def rule_text(conditions: dict[str, str]) -> str:
    described = ", ".join(f"{k}={conditions[k]}" for k in sorted(conditions))
    return f"Προσοχή στις θέσεις με {described}: χάνουν συχνότερα (κανόνας του Jarvis, σε shadow)."


def record_rules(intel_db: Path, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each rule as a lesson; the learning store puts a new lesson straight into shadow. Nothing else is written."""
    if not rules:
        return []
    import agent_learning_control_plane as learning
    import market_intel_db

    recorded = []
    with closing(market_intel_db.connect(intel_db)) as conn:
        learning.init_schema(conn)
        for rule in rules:
            conditions = rule["conditions"]
            proposal = {"schema": "ecosystem.agent_lesson_candidate.v1", "target_agent": conditions.get("agent", "sokratis"),
                        "criterion_id": rule["criterion_id"], "proposed_change": rule_text(conditions),
                        "activation": "candidate_only", "changes_active_scores": False,
                        "requires_shadow_validation": True, "requires_manual_promotion": True}
            lesson_id, created = learning.upsert_lesson_candidate(
                conn, target_agent=proposal["target_agent"], task_id=TASK_ID, rubric_version=RUBRIC,
                criterion_id=rule["criterion_id"], support_count=int(rule["train"]["n"]), proposal=proposal,
                evidence={"train": rule["train"], "test": rule["test"],
                          "generated_at": datetime.now(timezone.utc).isoformat()})
            recorded.append({"lesson_id": lesson_id, "created": created, "criterion_id": rule["criterion_id"]})
    return recorded


def _shadow_since(evidence: dict[str, Any]) -> str | None:
    for decision in reversed(evidence.get("decisions") or []):
        if decision.get("to") == "shadow" and decision.get("at"):
            return str(decision["at"])[:10]
    return None


def follow(intel_db: Path, trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every Jarvis lesson in shadow, judged only on the sessions after it entered shadow (out of sample)."""
    if not intel_db.exists():
        return []
    try:
        with closing(sqlite3.connect(f"file:{intel_db.as_posix()}?mode=ro", uri=True)) as conn:
            rows = conn.execute("SELECT lesson_id, criterion_id, proposal_json, evidence_json FROM agent_lesson_candidates"
                                " WHERE status='shadow' AND task_id=?", (TASK_ID,)).fetchall()
    except sqlite3.Error:
        return []
    trades = normalise(trades)
    out = []
    for lesson_id, criterion, proposal_json, evidence_json in rows:
        since = _shadow_since(json.loads(evidence_json or "{}"))
        conditions = parse_criterion(criterion)
        after = [t for t in trades if since is not None and t["session_date"] > since and _matches(t, conditions)]
        stats = _stats(after)
        if since is None or stats["n"] < FOLLOW_MIN:
            verdict = "collecting"
        else:
            verdict = "confirmed" if stats["loss_rate_pct"] >= 100 * TEST_LOSS_RATE else "not_confirmed"
        out.append({"lesson_id": lesson_id, "criterion_id": criterion, "conditions": conditions,
                    "proposed_change": json.loads(proposal_json or "{}").get("proposed_change"),
                    "shadow_since": since, "minimum_trades": FOLLOW_MIN, "verdict": verdict, **stats})
    return out


def follow_proposal(item: dict[str, Any]) -> Proposal | None:
    if item.get("verdict") not in {"confirmed", "not_confirmed"}:
        return None
    confirmed = item["verdict"] == "confirmed"
    return Proposal(
        source="jarvis_learning", source_key=item["lesson_id"],
        title=("Επιβεβαιώθηκε: " if confirmed else "Δεν επιβεβαιώθηκε: ") + str(item.get("proposed_change") or ""),
        evidence={"verdict": item["verdict"], "n": item.get("n"), "losses": item.get("losses"),
                  "loss_rate_pct": item.get("loss_rate_pct"), "shadow_since": item.get("shadow_since"),
                  "criterion_id": item.get("criterion_id")},
        change=("Να γίνει ενεργός κανόνας (με tests, μαζί με το Claude)" if confirmed
                else "Να αποσυρθεί το δίδαγμα από το shadow"))
