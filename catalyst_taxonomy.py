from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_TAXONOMY_PATH = Path(__file__).with_name("catalyst_taxonomy.json")
ITEM_RE = re.compile(r"\b\d+\.\d+\b")
NON_MACHINE_ID_RE = re.compile(r"[^a-z0-9]+")


def _to_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _machine_id(value: Any) -> str:
    normalized = NON_MACHINE_ID_RE.sub("_", str(value or "").strip().lower()).strip("_")
    return normalized or "unknown"


def _age_days(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc).date() - parsed.astimezone(timezone.utc).date()).days)


def load_taxonomy(path: Path | str = DEFAULT_TAXONOMY_PATH) -> dict[str, Any]:
    taxonomy_path = Path(path)
    payload = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    categories = payload.get("categories")
    scoring = payload.get("scoring", {})
    components = scoring.get("components", {})
    if not isinstance(categories, dict) or "unknown" not in categories:
        raise ValueError("Catalyst taxonomy must define a categories object and an unknown category.")
    component_total = sum(float(value) for value in components.values())
    if abs(component_total - 1.0) > 1e-9:
        raise ValueError(f"Catalyst score component weights must sum to 1.0, got {component_total}.")
    known_categories = set(categories)
    for group_name in ("sec_form_rules", "sec_item_rules"):
        for rule_name, rule in payload.get(group_name, {}).items():
            category_id = str(rule.get("category_id") or "")
            if category_id not in known_categories:
                raise ValueError(f"{group_name}.{rule_name} references unknown category {category_id!r}.")
    return payload


def normalize_sec_items(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = " ".join(str(item) for item in value)
    else:
        raw = str(value or "")
    items: list[str] = []
    for item in ITEM_RE.findall(raw):
        if item not in items:
            items.append(item)
    return items


def _matching_form_rule(form: str, taxonomy: dict[str, Any]) -> dict[str, Any] | None:
    rules = taxonomy.get("sec_form_rules", {})
    if form in rules:
        return dict(rules[form])
    for pattern, rule in rules.items():
        if pattern.endswith("*") and form.startswith(pattern[:-1]):
            return dict(rule)
    return None


def _recency_score(age_days: int | None, taxonomy: dict[str, Any]) -> float:
    if age_days is None:
        return 1.0
    for band in taxonomy.get("scoring", {}).get("recency_bands", []):
        if age_days <= int(band.get("max_age_days", 0)):
            return _clamp(float(band.get("score", 1)), 1.0, 5.0)
    return 1.0


def _priority(score: float, taxonomy: dict[str, Any]) -> str:
    thresholds = taxonomy.get("scoring", {}).get("priority_thresholds", {})
    ordered = sorted(
        ((str(label), float(value)) for label, value in thresholds.items()),
        key=lambda item: item[1],
        reverse=True,
    )
    for label, minimum in ordered:
        if score >= minimum:
            return label
    return "monitor"


def _direction_from_text(text: str, category: dict[str, Any], fallback: str) -> tuple[str, list[str]]:
    lowered = text.lower()
    positive = [term for term in category.get("positive_keywords", []) if str(term).lower() in lowered]
    negative = [term for term in category.get("negative_keywords", []) if str(term).lower() in lowered]
    if positive and negative:
        return "mixed", positive + negative
    if positive:
        return "positive", positive
    if negative:
        return "negative", negative
    return fallback, []


def _score_candidate(candidate: dict[str, Any], taxonomy: dict[str, Any]) -> dict[str, Any]:
    category_id = str(candidate.get("category_id") or "unknown")
    category = taxonomy["categories"].get(category_id, taxonomy["categories"]["unknown"])
    materiality = _clamp(
        float(candidate.get("materiality") or category.get("base_materiality", 1)),
        1.0,
        5.0,
    )
    source_kind = str(candidate.get("source_kind") or "unknown")
    scoring = taxonomy.get("scoring", {})
    evidence_confidence = _clamp(
        float(scoring.get("source_confidence", {}).get(source_kind, 1)),
        1.0,
        5.0,
    )
    specificity = _clamp(
        float(scoring.get("specificity", {}).get(candidate.get("specificity_kind", source_kind), 1)),
        1.0,
        5.0,
    )
    recency = _recency_score(candidate.get("age_days"), taxonomy)
    components = scoring.get("components", {})
    weighted = (
        materiality * float(components.get("category_materiality", 0.45))
        + evidence_confidence * float(components.get("evidence_confidence", 0.25))
        + recency * float(components.get("recency", 0.20))
        + specificity * float(components.get("specificity", 0.10))
    )
    attention_score = round((_clamp(weighted, 1.0, 5.0) / 5.0) * 100.0, 2)
    direction, direction_matches = _direction_from_text(
        str(candidate.get("search_text") or candidate.get("event_name") or ""),
        category,
        str(candidate.get("direction") or category.get("default_direction") or "unknown"),
    )
    confidence_label = "high" if evidence_confidence >= 4 else "medium" if evidence_confidence >= 3 else "low"
    trade_lens = taxonomy.get("trade_lens_scoring", {})
    direction_alignment = trade_lens.get("direction_alignment", {}).get(direction, {})
    confidence_multiplier = float(
        trade_lens.get("confidence_multiplier", {}).get(confidence_label, 0.0)
    )
    long_relevance = _clamp(float(category.get("long_relevance", 0)), 0.0, 5.0)
    short_relevance = _clamp(float(category.get("short_relevance", 0)), 0.0, 5.0)
    long_score = round(
        attention_score
        * float(direction_alignment.get("long", 0.0))
        * (long_relevance / 5.0)
        * confidence_multiplier,
        2,
    )
    short_score = round(
        attention_score
        * float(direction_alignment.get("short", 0.0))
        * (short_relevance / 5.0)
        * confidence_multiplier,
        2,
    )
    subcategory_id = str(candidate.get("subcategory_id") or "").strip()
    if not subcategory_id:
        if candidate.get("sec_item"):
            subcategory_id = f"sec_8k_item_{_machine_id(candidate.get('sec_item'))}"
        elif candidate.get("sec_form"):
            subcategory_id = f"sec_form_{_machine_id(candidate.get('sec_form'))}"
        else:
            subcategory_id = f"{_machine_id(source_kind)}_{_machine_id(category_id)}"
    return {
        **candidate,
        "category_id": category_id,
        "category_label": category.get("label", category_id),
        "reader_question": category.get("reader_question"),
        "legacy_bucket": category.get("legacy_bucket", "UNKNOWN"),
        "category_weight": materiality,
        "evidence_confidence_score": evidence_confidence,
        "classification_confidence": confidence_label,
        "recency_score": recency,
        "specificity_score": specificity,
        "attention_score": attention_score,
        "priority": _priority(attention_score, taxonomy),
        "direction": direction,
        "direction_keyword_matches": direction_matches,
        "subcategory_id": subcategory_id,
        "long_relevance_weight": long_relevance,
        "short_relevance_weight": short_relevance,
        "long_relevance_score": long_score,
        "short_relevance_score": short_score,
    }


def _weighted_top_scores(scores: list[float], taxonomy: dict[str, Any]) -> float:
    aggregation = taxonomy.get("trade_lens_scoring", {}).get("aggregation", {})
    weights = [
        float(aggregation.get("highest_child_weight", 1.0)),
        float(aggregation.get("second_child_weight", 0.2)),
        float(aggregation.get("third_child_weight", 0.1)),
    ]
    ordered = sorted((max(0.0, float(score)) for score in scores), reverse=True)
    total = sum(score * weight for score, weight in zip(ordered[:3], weights))
    return round(min(float(aggregation.get("maximum_score", 100)), total), 2)


def _aggregate_trade_lens(
    candidates: list[dict[str, Any]],
    side: str,
    taxonomy: dict[str, Any],
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        category_id = str(candidate.get("category_id") or "unknown")
        grouped.setdefault(category_id, []).append(candidate)

    categories: list[dict[str, Any]] = []
    for category_id, category_candidates in grouped.items():
        category = taxonomy["categories"].get(category_id, taxonomy["categories"]["unknown"])
        score_field = f"{side}_relevance_score"
        ordered = sorted(
            category_candidates,
            key=lambda item: float(item.get(score_field) or 0.0),
            reverse=True,
        )
        subcategories = [
            {
                "subcategory_id": item.get("subcategory_id"),
                "event_name": item.get("event_name"),
                "event_date": item.get("event_date"),
                "score": item.get(score_field),
                "direction": item.get("direction"),
                "classification_confidence": item.get("classification_confidence"),
                "source_kind": item.get("source_kind"),
                "source_url": item.get("source_url"),
                "sec_form": item.get("sec_form"),
                "sec_item": item.get("sec_item"),
            }
            for item in ordered
        ]
        categories.append(
            {
                "category_id": category_id,
                "category_label": category.get("label", category_id),
                "relevance_weight": category.get(f"{side}_relevance", 0),
                "score": _weighted_top_scores(
                    [float(item.get(score_field) or 0.0) for item in ordered],
                    taxonomy,
                ),
                "subcategories": subcategories,
            }
        )

    categories.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    return {
        "lens": side,
        "status": taxonomy.get("trade_lens_scoring", {}).get(
            "status", "advisory_only_no_auto_execution"
        ),
        "score": _weighted_top_scores(
            [float(category.get("score") or 0.0) for category in categories],
            taxonomy,
        ),
        "categories": categories,
    }


def _trade_bias(long_score: float, short_score: float, taxonomy: dict[str, Any]) -> str:
    config = taxonomy.get("trade_lens_scoring", {})
    minimum = float(config.get("bias_minimum_score", 55))
    minimum_gap = float(config.get("bias_minimum_gap", 15))
    if max(long_score, short_score) < minimum:
        return "catalyst_only_neutral"
    if abs(long_score - short_score) < minimum_gap:
        return "catalyst_only_balanced_review"
    return "catalyst_only_long" if long_score > short_score else "catalyst_only_short"


def _keyword_candidate(
    text: str,
    taxonomy: dict[str, Any],
    *,
    source_kind: str,
    specificity_kind: str,
    event_date: str | None,
    age_days: int | None,
    source_url: str | None,
) -> dict[str, Any] | None:
    lowered = text.lower()
    matches: list[tuple[int, float, str, list[str]]] = []
    for category_id, category in taxonomy.get("categories", {}).items():
        if category_id == "unknown":
            continue
        found = [term for term in category.get("keywords", []) if str(term).lower() in lowered]
        if found:
            matches.append((len(found), float(category.get("base_materiality", 1)), category_id, found))
    if not matches:
        return None
    _, _, category_id, found = max(matches, key=lambda item: (item[0], item[1]))
    return {
        "category_id": category_id,
        "event_name": text.strip(),
        "event_date": event_date,
        "age_days": age_days,
        "source_kind": source_kind,
        "specificity_kind": specificity_kind,
        "source_url": source_url,
        "search_text": text,
        "classification_reason": f"Matched category keywords: {', '.join(found[:5])}",
        "matched_keywords": found,
    }


def classify_catalyst(
    row: dict[str, Any],
    taxonomy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    taxonomy = taxonomy or load_taxonomy()
    candidates: list[dict[str, Any]] = []

    filings = row.get("sec_recent_filings") or []
    if isinstance(filings, list):
        for filing in filings:
            if not isinstance(filing, dict):
                continue
            form = str(filing.get("form") or "").upper().strip()
            filing_date = str(filing.get("filing_date") or "").strip() or None
            filing_age = filing.get("filing_age_days")
            if filing_age is None:
                filing_age = _age_days(filing_date)
            else:
                filing_age = int(filing_age)
            url = str(filing.get("url") or "").strip() or None
            item_codes = normalize_sec_items(filing.get("items"))
            mapped_item_found = False
            for item_code in item_codes:
                rule = taxonomy.get("sec_item_rules", {}).get(item_code)
                if not rule or bool(rule.get("supporting_only")):
                    continue
                mapped_item_found = True
                candidates.append(
                    _score_candidate(
                        {
                            **rule,
                            "event_date": filing_date,
                            "age_days": filing_age,
                            "source_kind": "sec_item",
                            "specificity_kind": "sec_item",
                            "source_url": url,
                            "sec_form": form,
                            "sec_item": item_code,
                            "search_text": f"{form} item {item_code} {rule.get('event_name', '')}",
                            "classification_reason": f"SEC {form} Item {item_code} maps to {rule.get('category_id')}.",
                        },
                        taxonomy,
                    )
                )
            form_rule = _matching_form_rule(form, taxonomy)
            if form_rule and (not mapped_item_found or form not in {"8-K", "8-K/A"}):
                candidates.append(
                    _score_candidate(
                        {
                            **form_rule,
                            "event_date": filing_date,
                            "age_days": filing_age,
                            "source_kind": "sec_form",
                            "specificity_kind": "sec_form",
                            "source_url": url,
                            "sec_form": form,
                            "sec_item": None,
                            "search_text": f"{form} {form_rule.get('event_name', '')}",
                            "classification_reason": f"SEC form {form} maps to {form_rule.get('category_id')}.",
                        },
                        taxonomy,
                    )
                )

    news_items = row.get("news") or []
    if isinstance(news_items, list):
        for item in news_items[:6]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            published_at = str(item.get("published_at") or "").strip() or None
            candidate = _keyword_candidate(
                title,
                taxonomy,
                source_kind="market_news",
                specificity_kind="keyword_news",
                event_date=published_at,
                age_days=_age_days(published_at),
                source_url=str(item.get("link") or "").strip() or None,
            )
            if candidate:
                candidates.append(_score_candidate(candidate, taxonomy))

    if candidates:
        candidates.sort(
            key=lambda item: (
                float(item.get("attention_score") or 0.0),
                float(item.get("evidence_confidence_score") or 0.0),
                float(item.get("specificity_score") or 0.0),
                -(int(item.get("age_days")) if item.get("age_days") is not None else 99999),
            ),
            reverse=True,
        )
        best = candidates[0]
    else:
        best = _score_candidate(
            {
                "category_id": "unknown",
                "event_name": "No source-backed catalyst classified",
                "event_date": None,
                "age_days": None,
                "source_kind": "unknown",
                "specificity_kind": "unknown",
                "source_url": None,
                "direction": "unknown",
                "search_text": "",
                "classification_reason": "No mapped SEC item, specific filing rule, or catalyst keyword was available.",
            },
            taxonomy,
        )

    lens_candidates = candidates if candidates else [best]
    trade_lenses = {
        "long": _aggregate_trade_lens(lens_candidates, "long", taxonomy),
        "short": _aggregate_trade_lens(lens_candidates, "short", taxonomy),
    }
    long_catalyst_score = float(trade_lenses["long"]["score"])
    short_catalyst_score = float(trade_lenses["short"]["score"])

    evidence = []
    seen: set[tuple[Any, ...]] = set()
    for item in candidates[:5]:
        key = (item.get("source_url"), item.get("sec_form"), item.get("sec_item"), item.get("event_name"))
        if key in seen:
            continue
        seen.add(key)
        evidence.append(
            {
                "category_id": item.get("category_id"),
                "subcategory_id": item.get("subcategory_id"),
                "event_name": item.get("event_name"),
                "event_date": item.get("event_date"),
                "source_kind": item.get("source_kind"),
                "source_url": item.get("source_url"),
                "sec_form": item.get("sec_form"),
                "sec_item": item.get("sec_item"),
                "attention_score": item.get("attention_score"),
                "direction": item.get("direction"),
                "long_relevance_score": item.get("long_relevance_score"),
                "short_relevance_score": item.get("short_relevance_score"),
            }
        )

    return {
        "taxonomy_version": taxonomy.get("schema_version", 1),
        "scoring_status": taxonomy.get("status", "advisory"),
        "category_id": best.get("category_id"),
        "category_label": best.get("category_label"),
        "legacy_bucket": best.get("legacy_bucket"),
        "reader_question": best.get("reader_question"),
        "event_name": best.get("event_name"),
        "event_date": best.get("event_date"),
        "direction": best.get("direction"),
        "category_weight": best.get("category_weight"),
        "attention_score": best.get("attention_score"),
        "priority": best.get("priority"),
        "classification_confidence": best.get("classification_confidence"),
        "evidence_confidence_score": best.get("evidence_confidence_score"),
        "recency_score": best.get("recency_score"),
        "specificity_score": best.get("specificity_score"),
        "classification_reason": best.get("classification_reason"),
        "source_kind": best.get("source_kind"),
        "source_url": best.get("source_url"),
        "sec_form": best.get("sec_form"),
        "sec_item": best.get("sec_item"),
        "evidence": evidence,
        "long_catalyst_score": long_catalyst_score,
        "short_catalyst_score": short_catalyst_score,
        "catalyst_trade_bias": _trade_bias(long_catalyst_score, short_catalyst_score, taxonomy),
        "trade_lenses": trade_lenses,
    }
