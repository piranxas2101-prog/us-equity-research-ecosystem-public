from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ITEM_SCHEMA = "ecosystem.catalyst_content_review_item.v1"
QUEUE_SCHEMA = "ecosystem.catalyst_content_review_queue.v1"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _unknown_subcategories(row: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    lenses = row.get("catalyst_trade_lenses") or {}
    for lens in ("long", "short"):
        for category in ((lenses.get(lens) or {}).get("categories") or []):
            if not isinstance(category, dict) or str(category.get("category_id") or "") != "unknown":
                continue
            for item in category.get("subcategories") or []:
                if isinstance(item, dict):
                    found.append(item)
    if str(row.get("catalyst_category_id") or "") == "unknown":
        for item in row.get("catalyst_evidence") or []:
            if isinstance(item, dict):
                found.append(item)
    return found


def build_queue(rows: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        ticker = str(row.get("ticker") or row.get("symbol") or "").upper()
        for source in _unknown_subcategories(row):
            source_url = str(source.get("source_url") or source.get("url") or "")
            source_form = str(source.get("sec_form") or source.get("form") or "")
            source_date = str(source.get("event_date") or source.get("filing_date") or "")
            key = (ticker, source_url, source_form)
            if not ticker or key in seen:
                continue
            seen.add(key)
            body = {
                "schema": ITEM_SCHEMA,
                "run_id": run_id,
                "ticker": ticker,
                "source_url": source_url or None,
                "source_kind": source.get("source_kind") or "sec_filing",
                "sec_form": source_form or None,
                "source_date": source_date or None,
                "event_name": source.get("event_name"),
                "reason_code": "insufficient_source_detail",
                "status": "pending_content_review",
                "allowed_outputs": ["classified_with_source", "remain_unknown_with_reason"],
                "prohibited_outputs": ["invented_numeric_value", "automatic_trade_direction"],
                "immutable_source": True,
            }
            items.append({**body, "review_item_id": f"review_{digest(body)[:24]}"})
    queue = {
        "schema": QUEUE_SCHEMA,
        "run_id": run_id,
        "item_count": len(items),
        "items": items,
        "execution_authority": False,
        "note": "This queue requests source-content review. It does not infer a catalyst or trade direction.",
    }
    return {**queue, "queue_sha256": digest(queue)}


def write_queue(payload: dict[str, Any], run_path: Path, latest_path: Path) -> None:
    run_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    run_path.write_text(encoded, encoding="utf-8")
    latest_path.write_text(encoded, encoding="utf-8")


def _evidence_span(text: str, token: str, width: int = 240) -> str:
    lowered = text.lower()
    index = lowered.find(token.lower())
    if index < 0:
        return text[:width].strip()
    start = max(0, index - width // 3)
    end = min(len(text), start + width)
    return text[start:end].strip()


def resolve_text(
    text: str,
    *,
    source_url: str | None = None,
    source_hash: str | None = None,
    taxonomy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify bounded source text without creating a trade instruction.

    This deterministic resolver is intentionally conservative.  Ties and text
    without a taxonomy keyword remain UNKNOWN, which is a valid benchmark result.
    """
    registry = taxonomy or json.loads(
        (Path(__file__).resolve().parent / "catalyst_taxonomy.json").read_text(
            encoding="utf-8-sig"
        )
    )
    clean = " ".join(str(text or "").split())
    immutable_hash = source_hash or hashlib.sha256(clean.encode("utf-8")).hexdigest()
    lowered = clean.lower()
    scored: list[tuple[int, int, str, list[str]]] = []
    for category_id, spec in (registry.get("categories") or {}).items():
        if category_id == "unknown":
            continue
        matches = [
            keyword
            for keyword in (spec.get("keywords") or [])
            if keyword.lower() in lowered
        ]
        if matches:
            longest = max(len(keyword) for keyword in matches)
            scored.append((len(matches), longest, category_id, matches))
    scored.sort(reverse=True)
    if not scored:
        return {
            "status": "remain_unknown_with_reason",
            "category_id": "unknown",
            "direction": "unknown",
            "confidence": "low",
            "reason": "no_taxonomy_evidence",
            "evidence_span": None,
            "source_url": source_url,
            "source_hash": immutable_hash,
            "automatic_trade_direction": False,
        }
    top = scored[0]
    if len(scored) > 1 and scored[1][:2] == top[:2]:
        return {
            "status": "remain_unknown_with_reason",
            "category_id": "unknown",
            "direction": "unknown",
            "confidence": "low",
            "reason": "ambiguous_category_evidence",
            "evidence_span": _evidence_span(clean, top[3][0]),
            "source_url": source_url,
            "source_hash": immutable_hash,
            "automatic_trade_direction": False,
        }
    category_id = top[2]
    category = registry["categories"][category_id]
    positive = [
        token
        for token in (category.get("positive_keywords") or [])
        if token.lower() in lowered
    ]
    negative = [
        token
        for token in (category.get("negative_keywords") or [])
        if token.lower() in lowered
    ]
    if positive and negative:
        direction = "mixed"
    elif positive:
        direction = "positive"
    elif negative:
        direction = "negative"
    else:
        direction = str(category.get("default_direction") or "unknown")
    confidence = "high" if len(top[3]) >= 2 or positive or negative else "medium"
    anchor = (positive or negative or top[3])[0]
    return {
        "status": "classified_with_source",
        "category_id": category_id,
        "direction": direction,
        "confidence": confidence,
        "reason": "taxonomy_evidence_match",
        "evidence_span": _evidence_span(clean, anchor),
        "source_url": source_url,
        "source_hash": immutable_hash,
        "matched_category_terms": top[3],
        "matched_positive_terms": positive,
        "matched_negative_terms": negative,
        "automatic_trade_direction": False,
    }


def evaluate_labelled_fixture(
    fixtures: list[dict[str, Any]], taxonomy: dict[str, Any] | None = None
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    category_correct = 0
    direction_false = 0
    classified = 0
    unresolved = 0
    for fixture in fixtures:
        result = resolve_text(
            str(fixture.get("text") or ""),
            source_url=fixture.get("source_url"),
            source_hash=fixture.get("source_hash"),
            taxonomy=taxonomy,
        )
        expected_category = fixture.get("expected_category")
        expected_direction = fixture.get("expected_direction")
        is_classified = result["status"] == "classified_with_source"
        classified += int(is_classified)
        unresolved += int(not is_classified)
        category_correct += int(result["category_id"] == expected_category)
        direction_false += int(
            is_classified
            and expected_direction not in {None, "unknown"}
            and result["direction"] != expected_direction
        )
        rows.append(
            {
                "fixture_id": fixture.get("fixture_id"),
                "expected_category": expected_category,
                "expected_direction": expected_direction,
                "result": result,
            }
        )
    total = len(fixtures)
    return {
        "schema": "ecosystem.catalyst_resolver_benchmark.v1",
        "fixture_count": total,
        "classified_count": classified,
        "unresolved_count": unresolved,
        "unresolved_pct": round(unresolved / total * 100.0, 2) if total else 0.0,
        "category_accuracy_pct": round(category_correct / total * 100.0, 2) if total else 0.0,
        "false_direction_count": direction_false,
        "false_direction_pct": round(direction_false / classified * 100.0, 2)
        if classified
        else 0.0,
        "rows": rows,
        "execution_authority": False,
    }
