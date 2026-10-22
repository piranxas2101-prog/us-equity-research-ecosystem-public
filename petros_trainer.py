from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, balanced_accuracy_score, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

import market_intel_db
import petros_features
import agent_learning_control_plane


DEFAULT_MODEL_PATH = Path("models/petros_model_latest.json")
DEFAULT_REPORT_PATH = Path("output/petros_training_latest.md")
DEFAULT_DATASET_PATH = Path("output/petros_training_dataset_latest.csv")
POSITIVE_FEEDBACK = {"agree", "interested", "watch"}
NEGATIVE_FEEDBACK = {"disagree", "reject"}


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def load_json_text(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def init_schema(conn: sqlite3.Connection) -> None:
    market_intel_db.init_db(conn)


def outcome_examples(
    conn: sqlite3.Connection,
    horizon_days: int,
    target_return_pct: float,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            pd.source_run_timestamp,
            pd.ticker,
            pd.payload_json AS petros_payload,
            po.horizon_days,
            po.return_pct
        FROM petros_decisions pd
        JOIN candidate_setups cs
          ON cs.source_run_timestamp = pd.source_run_timestamp
         AND cs.ticker = pd.ticker
        JOIN price_outcomes po
          ON po.setup_id = cs.id
        WHERE po.horizon_days = ?
          AND po.return_pct IS NOT NULL
        ORDER BY pd.source_run_timestamp, pd.id
        """,
        (horizon_days,),
    ).fetchall()

    examples: list[dict[str, Any]] = []
    for row in rows:
        payload = load_json_text(row["petros_payload"])
        if not payload:
            continue
        return_pct = market_intel_db.to_float(row["return_pct"])
        if return_pct is None:
            continue
        examples.append(
            {
                "ticker": row["ticker"],
                "source_run_timestamp": row["source_run_timestamp"],
                "label_source": "price_outcome",
                "label": 1 if return_pct >= target_return_pct else 0,
                "return_pct": return_pct,
                "horizon_days": row["horizon_days"],
                "payload": payload,
            }
        )
    return examples


def feedback_examples(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            pf.ticker,
            pf.created_at,
            pf.feedback_verdict,
            pf.notes,
            pf.source_run_timestamp,
            pd.payload_json AS petros_payload
        FROM petros_feedback pf
        JOIN petros_decisions pd
          ON pd.ticker = pf.ticker
         AND (
                pf.source_run_timestamp = ''
             OR pf.source_run_timestamp IS NULL
             OR pd.source_run_timestamp = pf.source_run_timestamp
         )
        WHERE pf.feedback_verdict IN ('agree', 'interested', 'watch', 'disagree', 'reject')
        GROUP BY pf.id
        ORDER BY pf.id
        """
    ).fetchall()

    examples: list[dict[str, Any]] = []
    for row in rows:
        verdict = str(row["feedback_verdict"] or "").lower()
        if verdict in POSITIVE_FEEDBACK:
            label = 1
        elif verdict in NEGATIVE_FEEDBACK:
            label = 0
        else:
            continue
        payload = load_json_text(row["petros_payload"])
        if not payload:
            continue
        examples.append(
            {
                "ticker": row["ticker"],
                "source_run_timestamp": row["source_run_timestamp"] or "",
                "label_source": "user_feedback",
                "label": label,
                "feedback_verdict": verdict,
                "notes": row["notes"] or "",
                "payload": payload,
            }
        )
    return examples


def build_training_examples(
    db_path: Path,
    horizon_days: int,
    target_return_pct: float,
    include_feedback: bool,
) -> list[dict[str, Any]]:
    with market_intel_db.connect(db_path) as conn:
        init_schema(conn)
        examples = outcome_examples(conn, horizon_days, target_return_pct)
        if include_feedback:
            examples.extend(feedback_examples(conn))
    examples.sort(key=lambda row: (str(row.get("source_run_timestamp") or ""), str(row.get("ticker") or "")))
    return examples


def matrix_from_examples(examples: list[dict[str, Any]]) -> tuple[list[list[float]], list[int], list[str]]:
    payloads = [example["payload"] for example in examples]
    feature_names = petros_features.feature_names_from_rows(payloads)
    x_rows = [
        petros_features.vectorize(petros_features.build_features(example["payload"]), feature_names)
        for example in examples
    ]
    y_rows = [int(example["label"]) for example in examples]
    return x_rows, y_rows, feature_names


def safe_probability(intercept: float, coefficients: list[float], values: list[float]) -> float:
    z = intercept + sum(coef * value for coef, value in zip(coefficients, values))
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    exp_z = math.exp(z)
    return exp_z / (1.0 + exp_z)


def evaluate_model(x_rows: list[list[float]], y_rows: list[int]) -> dict[str, Any]:
    counts = Counter(y_rows)
    if len(counts) < 2 or min(counts.values()) < 2 or len(y_rows) < 12:
        return {"status": "too_few_rows_for_walk_forward_validation"}

    splits = min(5, max(2, len(y_rows) // 8))
    probabilities: list[float] = []
    observed: list[int] = []
    valid_folds = 0
    for train_indexes, test_indexes in TimeSeriesSplit(n_splits=splits).split(x_rows):
        train_y = [y_rows[index] for index in train_indexes]
        if len(set(train_y)) < 2:
            continue
        train_x = [x_rows[index] for index in train_indexes]
        test_x = [x_rows[index] for index in test_indexes]
        scaler = StandardScaler()
        train_scaled = scaler.fit_transform(train_x)
        test_scaled = scaler.transform(test_x)
        model = LogisticRegression(class_weight="balanced", max_iter=1000, random_state=42)
        model.fit(train_scaled, train_y)
        probabilities.extend(float(value) for value in model.predict_proba(test_scaled)[:, 1])
        observed.extend(y_rows[index] for index in test_indexes)
        valid_folds += 1
    if not probabilities:
        return {"status": "no_valid_walk_forward_fold"}
    predictions = [1 if prob >= 0.5 else 0 for prob in probabilities]
    metrics: dict[str, Any] = {
        "status": "walk_forward_validated",
        "validation_scheme": "expanding_time_ordered_splits",
        "valid_folds": valid_folds,
        "evaluated_rows": len(observed),
        "accuracy": round(float(accuracy_score(observed, predictions)), 4),
        "balanced_accuracy": round(float(balanced_accuracy_score(observed, predictions)), 4),
    }
    if len(set(observed)) == 2:
        metrics["roc_auc"] = round(float(roc_auc_score(observed, probabilities)), 4)
    return metrics


def train_model(
    examples: list[dict[str, Any]],
    horizon_days: int,
    target_return_pct: float,
    min_rows: int,
) -> tuple[dict[str, Any] | None, str]:
    if len(examples) < min_rows:
        return None, f"insufficient_rows: need {min_rows}, have {len(examples)}"

    label_counts = Counter(int(example["label"]) for example in examples)
    if len(label_counts) < 2:
        return None, f"insufficient_classes: labels={dict(label_counts)}"

    x_rows, y_rows, feature_names = matrix_from_examples(examples)
    scaler = StandardScaler()
    x_scaled = scaler.fit_transform(x_rows)
    model = LogisticRegression(class_weight="balanced", max_iter=1000, random_state=42)
    model.fit(x_scaled, y_rows)
    metrics = evaluate_model(x_rows, y_rows)

    coefficients = [float(value) for value in model.coef_[0].tolist()]
    intercept = float(model.intercept_[0])
    mean = [float(value) for value in scaler.mean_.tolist()]
    scale = [float(value) if float(value) != 0.0 else 1.0 for value in scaler.scale_.tolist()]

    model_json = {
        "model_type": "petros_logistic_regression_json_v1",
        "label_objective": (
            "market_outcome"
            if {str(example.get("label_source")) for example in examples} == {"price_outcome"}
            else "mixed_market_outcome_and_user_preference"
        ),
        "safe_format": "json_coefficients_only",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "training_rows": len(examples),
        "label_counts": {str(key): int(value) for key, value in sorted(label_counts.items())},
        "horizon_days": horizon_days,
        "target_return_pct": target_return_pct,
        "feature_names": feature_names,
        "scaler_mean": mean,
        "scaler_scale": scale,
        "coefficients": coefficients,
        "intercept": intercept,
        "metrics": metrics,
        "safety_notes": [
            "This file contains JSON numbers only; Petros does not load pickle/joblib models.",
            "Predictions are research signals, not trade instructions.",
            "No broker/order-execution API is called by this trainer.",
        ],
    }
    return model_json, "trained"


def render_report(
    examples: list[dict[str, Any]],
    model_json: dict[str, Any] | None,
    status: str,
    model_path: Path,
) -> str:
    counts = Counter(int(example["label"]) for example in examples)
    source_counts = Counter(str(example.get("label_source") or "unknown") for example in examples)
    lines = [
        "# Petros Training Report",
        "",
        f"- Status: {status}",
        f"- Training examples found: {len(examples)}",
        f"- Label counts: {dict(counts)}",
        f"- Label sources: {dict(source_counts)}",
        f"- Model path: {model_path}",
        "",
        "## Safety",
        "",
        "- The trainer reads only the local SQLite memory.",
        "- The model is saved as plain JSON coefficients, not pickle/joblib.",
        "- The model cannot place trades.",
        "- The default fundamental-only Petros review does not load this model; it is retained for explicit legacy research comparisons.",
    ]
    if model_json:
        lines.extend(
            [
                "",
                "## Model",
                "",
                f"- Type: {model_json['model_type']}",
                f"- Horizon: {model_json['horizon_days']} days",
                f"- Target return: {model_json['target_return_pct']}%",
                f"- Metrics: {model_json.get('metrics', {})}",
                "",
                "## Strongest Weights",
                "",
            ]
        )
        weights = sorted(
            zip(model_json["feature_names"], model_json["coefficients"]),
            key=lambda item: abs(float(item[1])),
            reverse=True,
        )
        for name, weight in weights[:12]:
            lines.append(f"- {name}: {round(float(weight), 4)}")
    return "\n".join(lines)


def write_dataset(path: Path, examples: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = ["ticker", "source_run_timestamp", "label", "label_source", "return_pct", "feedback_verdict"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for example in examples:
            writer.writerow(example)


def command_train(args: argparse.Namespace) -> None:
    db_path = Path(args.db)
    model_path = Path(args.model)
    report_path = Path(args.report)
    dataset_path = Path(args.dataset)

    examples = build_training_examples(
        db_path=db_path,
        horizon_days=args.horizon_days,
        target_return_pct=args.target_return_pct,
        include_feedback=args.include_feedback,
    )
    write_dataset(dataset_path, examples)
    model_json, status = train_model(
        examples=examples,
        horizon_days=args.horizon_days,
        target_return_pct=args.target_return_pct,
        min_rows=args.min_rows,
    )

    if model_json:
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_text(as_json(model_json), encoding="utf-8")
        dataset_hash = hashlib.sha256(
            json.dumps(examples, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        feature_hash = hashlib.sha256(
            json.dumps(model_json["feature_names"], separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        with market_intel_db.connect(db_path) as conn:
            agent_learning_control_plane.init_schema(conn)
            model_id = agent_learning_control_plane.register_model(
                conn, agent_id="petros", task_id="fundamental_synthesis",
                model_version=str(model_json["trained_at"]),
                model_type=str(model_json["model_type"]), model_path=model_path,
                dataset_sha256=dataset_hash, feature_schema_sha256=feature_hash,
                training_rows=int(model_json["training_rows"]),
                metrics=model_json.get("metrics") or {},
                gates={"minimum_rows": int(args.min_rows), "requires_shadow": True,
                       "manual_promotion_only": True, "execution_authority": False},
            )
        model_json["registry_model_id"] = model_id
        print(f"[PETROS_TRAINER] trained model: {model_path}")
        print(f"[PETROS_TRAINER] registered candidate: {model_id}")
    else:
        print(f"[PETROS_TRAINER] model not trained: {status}")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(examples, model_json, status, model_path), encoding="utf-8")
    print(f"[OUTPUT] report: {report_path}")
    print(f"[OUTPUT] dataset: {dataset_path}")


def command_status(args: argparse.Namespace) -> None:
    examples = build_training_examples(
        db_path=Path(args.db),
        horizon_days=args.horizon_days,
        target_return_pct=args.target_return_pct,
        include_feedback=args.include_feedback,
    )
    counts = Counter(int(example["label"]) for example in examples)
    source_counts = Counter(str(example.get("label_source") or "unknown") for example in examples)
    print(f"Training examples: {len(examples)}")
    print(f"Label counts: {dict(counts)}")
    print(f"Label sources: {dict(source_counts)}")
    model_path = Path(args.model)
    print(f"Model exists: {model_path.exists()} ({model_path})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safe JSON trainer for Petros decision memory.")
    parser.add_argument("--db", default=str(market_intel_db.DEFAULT_DB_PATH))
    parser.add_argument("--model", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--horizon-days", type=int, default=20)
    parser.add_argument("--target-return-pct", type=float, default=2.0)
    parser.add_argument(
        "--include-feedback",
        action="store_true",
        help="Opt in to mixing subjective user-preference labels with market outcomes (off by default).",
    )
    parser.add_argument("--no-feedback", action="store_true", help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)

    train = subparsers.add_parser("train", help="Train Petros model when enough labels exist.")
    train.add_argument("--min-rows", type=int, default=20)
    train.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    train.add_argument("--dataset", default=str(DEFAULT_DATASET_PATH))
    train.set_defaults(func=command_train)

    status = subparsers.add_parser("status", help="Show available training data.")
    status.set_defaults(func=command_status)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
