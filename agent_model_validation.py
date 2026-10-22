"""Small dependency-light validation gates for local agent models."""
from __future__ import annotations

import math
import itertools
import statistics
from typing import Any


def calibration_error(labels: list[int], probabilities: list[float], bins: int = 10) -> float:
    if len(labels) != len(probabilities) or not labels:
        raise ValueError("labels and probabilities must have equal non-zero length")
    total = len(labels)
    error = 0.0
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        members = [i for i, value in enumerate(probabilities)
                   if low <= value < high or (index == bins - 1 and value == 1.0)]
        if members:
            observed = sum(labels[i] for i in members) / len(members)
            predicted = sum(probabilities[i] for i in members) / len(members)
            error += len(members) / total * abs(observed - predicted)
    return round(error, 6)


def population_stability_index(reference: list[float], current: list[float], bins: int = 10) -> float | None:
    if len(reference) < bins or len(current) < bins:
        return None
    ordered = sorted(reference)
    edges = [ordered[min(len(ordered) - 1, int(len(ordered) * i / bins))] for i in range(1, bins)]
    def counts(values: list[float]) -> list[float]:
        result = [0] * bins
        for value in values:
            bucket = sum(value > edge for edge in edges)
            result[bucket] += 1
        return [max(count / len(values), 1e-6) for count in result]
    expected, actual = counts(reference), counts(current)
    return round(sum((a - e) * math.log(a / e) for e, a in zip(expected, actual)), 6)


def promotion_gate(*, rows: int, balanced_accuracy: float | None,
                   calibration: float | None, drift_psi: float | None,
                   minimum_rows: int = 100) -> dict[str, Any]:
    checks = {
        "enough_rows": rows >= minimum_rows,
        "balanced_accuracy": balanced_accuracy is not None and balanced_accuracy >= 0.55,
        "calibration": calibration is not None and calibration <= 0.15,
        "drift": drift_psi is None or drift_psi < 0.25,
    }
    return {"eligible_for_manual_shadow_review": all(checks.values()), "checks": checks,
            "automatic_promotion": False, "execution_authority": False}


def purged_combinatorial_splits(size: int, groups: int = 6, test_groups: int = 2,
                                embargo: int = 1) -> list[tuple[list[int], list[int]]]:
    if size < groups or not 0 < test_groups < groups:
        raise ValueError("Invalid CPCV dimensions")
    boundaries = [(size * i // groups, size * (i + 1) // groups) for i in range(groups)]
    result = []
    for chosen in itertools.combinations(range(groups), test_groups):
        test = [index for group in chosen for index in range(*boundaries[group])]
        blocked = set(test)
        for index in test:
            blocked.update(range(max(0, index - embargo), min(size, index + embargo + 1)))
        train = [index for index in range(size) if index not in blocked]
        if train and test:
            result.append((train, test))
    return result


def sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    deviation = statistics.stdev(returns)
    return statistics.mean(returns) / deviation * math.sqrt(252) if deviation else 0.0


def probability_backtest_overfit(strategy_returns: dict[str, list[float]], *, groups: int = 6,
                                  test_groups: int = 2, embargo: int = 1) -> dict[str, Any]:
    if len(strategy_returns) < 2:
        return {"status": "insufficient_strategies"}
    lengths = {len(values) for values in strategy_returns.values()}
    if len(lengths) != 1 or next(iter(lengths)) < groups:
        return {"status": "insufficient_or_misaligned_history"}
    names = sorted(strategy_returns)
    failures = trials = 0
    for train, test in purged_combinatorial_splits(next(iter(lengths)), groups, test_groups, embargo):
        best = max(names, key=lambda name: sharpe([strategy_returns[name][i] for i in train]))
        test_scores = sorted((sharpe([strategy_returns[name][i] for i in test]), name) for name in names)
        rank = next(index for index, (_, name) in enumerate(test_scores) if name == best)
        failures += int(rank < len(names) / 2)
        trials += 1
    return {"status": "assessed", "pbo": round(failures / trials, 6), "trials": trials,
            "interpretation": "lower_is_better", "research_only": True}
