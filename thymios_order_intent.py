from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import agent_methodology
import agent_personality
import holding_horizon
from trader_trade_plan import digest as trade_plan_digest


SCHEMA = "ecosystem.approved_order_intents.v1"
VALIDATION_SCHEMA = "ecosystem.thymios_validation.v1"
ALLOWED_AGENTS = {"yankos", "periklis"}
ALLOWED_DIRECTIONS = {"long", "short"}


class IntentValidationError(ValueError):
    """Raised when a broker-facing order intent is missing or invalid."""


def canonical_digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def stable_intent_id(run_id: str, agent: str, ticker: str, direction: str, playbook: str) -> str:
    raw = "|".join(
        [run_id.strip(), agent.strip().lower(), ticker.strip().upper(), direction.strip().lower(), playbook.strip().lower()]
    )
    return "intent_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def load_approved_intents(path: Path, *, today: date | None = None) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    validate_intent_payload(payload, today=today)
    return payload


def validate_intent_payload(payload: dict[str, Any], *, today: date | None = None) -> None:
    if payload.get("schema") != SCHEMA:
        raise IntentValidationError(f"Unsupported intent schema: {payload.get('schema')!r}")
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise IntentValidationError("Approved-intent payload has no run_id.")
    if payload.get("gate_status") != "approved":
        raise IntentValidationError(f"Koulis gate status is not approved: {payload.get('gate_status')!r}")
    expected_koulis = agent_methodology.methodology_identity("koulis")
    supplied_koulis = payload.get("methodology") or {}
    if (
        supplied_koulis.get("methodology_id") != expected_koulis["methodology_id"]
        or supplied_koulis.get("methodology_sha256") != expected_koulis["methodology_sha256"]
    ):
        raise IntentValidationError("Koulis methodology identity is missing or stale.")
    payload["validation_methodology"] = agent_methodology.methodology_identity("thymios")
    expected_koulis_personality = agent_personality.personality_identity("koulis")
    supplied_koulis_personality = payload.get("personality") or {}
    if (
        supplied_koulis_personality.get("personality_id") != expected_koulis_personality["personality_id"]
        or supplied_koulis_personality.get("personality_sha256") != expected_koulis_personality["personality_sha256"]
    ):
        raise IntentValidationError("Koulis personality identity is missing or stale.")
    payload["validation_personality"] = agent_personality.personality_identity("thymios")

    session_date = str(payload.get("session_date") or "").strip()
    expected_date = (today or datetime.now(timezone.utc).date()).isoformat()
    if session_date != expected_date:
        raise IntentValidationError(
            f"Approved intents are not valid for this session: session_date={session_date!r}, expected={expected_date!r}."
        )

    seen_ids: set[str] = set()
    seen_tickers: set[str] = set()
    total_notional = 0.0
    for item in payload.get("approved_intents", []):
        if not isinstance(item, dict):
            raise IntentValidationError("Every approved intent must be an object.")
        agent = str(item.get("agent") or "").strip().lower()
        ticker = str(item.get("ticker") or "").strip().upper()
        direction = str(item.get("trade_direction") or "").strip().lower()
        playbook = str(item.get("playbook_name") or "").strip()
        intent_id = str(item.get("intent_id") or "").strip()
        status = str(item.get("status") or "").strip().lower()
        fundamental_packet_hash = str(item.get("fundamental_packet_hash") or "").strip()
        fundamental_thesis_id = str(item.get("fundamental_thesis_id") or "").strip()
        fundamental_thesis_hash = str(item.get("fundamental_thesis_hash") or "").strip()
        try:
            notional = float(item.get("notional_usd"))
        except (TypeError, ValueError) as exc:
            raise IntentValidationError(f"Invalid notional for {ticker or '<missing ticker>'}.") from exc

        if agent not in ALLOWED_AGENTS:
            raise IntentValidationError(f"Intent agent is not allowed: {agent!r}")
        if not ticker or not ticker.replace(".", "").replace("-", "").isalnum():
            raise IntentValidationError(f"Invalid ticker in approved intent: {ticker!r}")
        if direction not in ALLOWED_DIRECTIONS:
            raise IntentValidationError(f"Invalid trade direction for {ticker}: {direction!r}")
        if not playbook:
            raise IntentValidationError(f"Missing playbook for {ticker}.")
        if status != "approved":
            raise IntentValidationError(f"Intent {intent_id or ticker} is not approved.")
        if item.get("execution_eligible") is not True or item.get("fundamental_gate_status") != "pass":
            raise IntentValidationError(f"Fundamental execution gate is not passed for {ticker}.")
        if not fundamental_packet_hash or not fundamental_thesis_id or not fundamental_thesis_hash:
            raise IntentValidationError(f"Fundamental thesis identity is incomplete for {ticker}.")
        if notional <= 0:
            raise IntentValidationError(f"Notional must be positive for {ticker}.")
        notional_source = str(item.get("notional_source") or "default")
        trade_plan = item.get("trade_plan")
        if notional_source == "agent_suggestion":
            if not isinstance(trade_plan, dict):
                raise IntentValidationError(f"Agent-sized intent has no frozen trade plan for {ticker}.")
            if trade_plan.get("execution_authority") is not False:
                raise IntentValidationError(f"Trade plan improperly claims execution authority for {ticker}.")
            identity_ok = (
                str(trade_plan.get("run_id") or "") == run_id
                and str(trade_plan.get("agent") or "").lower() == agent
                and str(trade_plan.get("ticker") or "").upper() == ticker
                and str(trade_plan.get("direction") or "").lower() == direction
                and str(trade_plan.get("playbook_id") or "") == playbook
            )
            if not identity_ok:
                raise IntentValidationError(f"Frozen trade-plan identity mismatch for {ticker}.")
            if abs(float(trade_plan.get("suggested_notional_usd") or 0.0) - notional) > 1e-9:
                raise IntentValidationError(f"Approved notional differs from the agent plan for {ticker}.")
            hash_basis = {
                key: value for key, value in trade_plan.items()
                if key not in {"trade_plan_sha256", "execution_authority"}
            }
            if str(trade_plan.get("trade_plan_sha256") or "") != trade_plan_digest(hash_basis):
                raise IntentValidationError(f"Frozen trade-plan hash mismatch for {ticker}.")
            if not trade_plan.get("invalidators") or not trade_plan.get("exit_logic"):
                raise IntentValidationError(f"Trade plan lacks invalidators or exit logic for {ticker}.")
            rule = str(trade_plan.get("holding_period_rule") or "")
            # owner 2026-10-23: a position is same-day or held for the horizon its agent decided from the evidence
            # (holding_horizon.py); any other rule, or a horizon a monitor cannot act on, is refused.
            if rule == holding_horizon.RULE:
                if not holding_horizon.valid(trade_plan.get("holding_horizon")):
                    raise IntentValidationError(f"Agent holding horizon is missing or invalid for {ticker}.")
            elif rule != holding_horizon.SAME_DAY:
                raise IntentValidationError(f"Experiment position is not same-day-flat for {ticker}.")
            open_gates = set(trade_plan.get("unresolved_short_gates_at_signal") or [])
            if direction == "short" and open_gates:
                # owner's choice B (2026-10-24): Koulis resolved them from today's broker and short-interest data
                resolution = item.get("short_gate_resolution") or {}
                if resolution.get("as_of") != session_date or not open_gates <= set(resolution.get("resolved") or {}):
                    raise IntentValidationError(f"SHORT gates are unresolved for {ticker}.")
        expected_id = stable_intent_id(run_id, agent, ticker, direction, playbook)
        if intent_id != expected_id:
            raise IntentValidationError(f"Intent id mismatch for {ticker}.")
        if intent_id in seen_ids:
            raise IntentValidationError(f"Duplicate intent id: {intent_id}")
        if ticker in seen_tickers:
            raise IntentValidationError(f"More than one approved order exists for ticker {ticker}.")
        seen_ids.add(intent_id)
        seen_tickers.add(ticker)
        total_notional += notional

    limits = payload.get("portfolio_limits") or {}
    max_orders = int(limits.get("max_total_orders") or 0)
    max_notional = float(limits.get("max_total_notional_usd") or 0.0)
    approved = payload.get("approved_intents", [])
    if max_orders < 0 or len(approved) > max_orders:
        raise IntentValidationError("Approved intents exceed the portfolio order limit.")
    if max_notional < 0 or total_notional > max_notional + 1e-9:
        raise IntentValidationError("Approved intents exceed the portfolio notional limit.")


def intents_for_agent(payload: dict[str, Any], agent: str) -> dict[tuple[str, str, str], dict[str, Any]]:
    slug = agent.strip().lower()
    return {
        (
            str(item["ticker"]).upper(),
            str(item["trade_direction"]).lower(),
            str(item["playbook_name"]).lower(),
        ): item
        for item in payload.get("approved_intents", [])
        if str(item.get("agent") or "").lower() == slug
    }


def build_validation_report(
    payload: dict[str, Any],
    *,
    source_artifact: str = "output/koulis_order_intents_latest.json",
    today: date | None = None,
) -> dict[str, Any]:
    """Create Thymios' own observable artifact, including valid zero-intent runs."""

    generated_at = datetime.now(timezone.utc)
    gate_status = str(payload.get("gate_status") or "").strip().lower()
    approved = [item for item in payload.get("approved_intents", []) if isinstance(item, dict)]
    validation_error = None
    validated = False
    if gate_status == "approved":
        try:
            validate_intent_payload(payload, today=today or generated_at.date())
            validated = True
        except IntentValidationError as exc:
            validation_error = str(exc)
    else:
        validation_error = f"Koulis gate is not approved: {gate_status or 'missing'}"

    if validated and approved:
        status = "validated"
    elif validated:
        status = "validated_no_intents"
    else:
        status = "upstream_gate_blocked" if gate_status == "blocked" else "validation_failed"

    return {
        "schema": VALIDATION_SCHEMA,
        "generated_at": generated_at.isoformat(),
        "run_timestamp": generated_at.isoformat(),
        "session_date": str(payload.get("session_date") or generated_at.date().isoformat()),
        "run_id": payload.get("run_id"),
        "agent_name": "thymios",
        "validation_methodology": agent_methodology.methodology_identity("thymios"),
        "validation_personality": agent_personality.personality_identity("thymios"),
        "status": status,
        "validated": validated,
        "validation_error": validation_error,
        "source_artifact": source_artifact,
        "source_schema": payload.get("schema"),
        "source_payload_sha256": canonical_digest(payload),
        "source_gate_status": gate_status,
        "approved_intent_count": len(approved),
        "validated_intents": [
            {
                "intent_id": item.get("intent_id"),
                "agent": item.get("agent"),
                "ticker": item.get("ticker"),
                "trade_direction": item.get("trade_direction"),
                "playbook_name": item.get("playbook_name"),
                "notional_usd": item.get("notional_usd"),
                "status": item.get("status"),
            }
            for item in approved
        ],
        "checks": {
            "source_schema_supported": payload.get("schema") == SCHEMA,
            "koulis_gate_approved": gate_status == "approved",
            "run_id_present": bool(str(payload.get("run_id") or "").strip()),
            "session_date_valid": str(payload.get("session_date") or "")
            == (today or generated_at.date()).isoformat(),
            "approved_intents_contract_valid": validated,
        },
    }


def write_validation_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
