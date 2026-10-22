"""Shared routing and packet contract for all sector research agents.

This module creates no market claims and performs no network calls.  It owns
the common architecture used by the eleven sector agents and the dedicated
unresolved-classification specialist.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
from typing import Any

from research_registry.core import PROJECT, digest, taxonomy
from sector_agents import AGENT_MODULES, SPECIAL_AGENT_MODULES


REGISTRY_PATH = PROJECT / "sector_agent_registry.json"


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_registry(registry: dict[str, Any] | None = None) -> dict[str, Any]:
    registry = registry or load_registry()
    tax, _ = taxonomy()
    sectors = {row[0] for row in tax["rows"]}
    configured = set(registry["agents"])
    errors: list[str] = []
    if configured != sectors:
        errors.append(f"sector_mismatch:missing={sorted(sectors-configured)}:extra={sorted(configured-sectors)}")
    for row in tax["rows"]:
        sector, subcategory = row[0], row[2]
        if sector in registry["agents"] and subcategory not in registry["agents"][sector]["subcategories"]:
            errors.append(f"missing_subcategory_framework:{sector}.{subcategory}")
    agent_ids = [value["agent_id"] for value in registry["agents"].values()]
    if len(agent_ids) != len(set(agent_ids)):
        errors.append("duplicate_agent_id")
    for sector, module_name in AGENT_MODULES.items():
        try:
            module = importlib.import_module(module_name)
            if module.SECTOR != sector or module.AGENT_ID != registry["agents"][sector]["agent_id"]:
                errors.append(f"agent_module_identity_mismatch:{sector}")
        except Exception as exc:
            errors.append(f"agent_module_unavailable:{sector}:{type(exc).__name__}")
    for category, module_name in SPECIAL_AGENT_MODULES.items():
        try:
            module = importlib.import_module(module_name)
            configured_special = registry.get("special_agents", {}).get(category) or {}
            if module.CATEGORY != category or module.AGENT_ID != configured_special.get("agent_id"):
                errors.append(f"special_agent_module_identity_mismatch:{category}")
        except Exception as exc:
            errors.append(f"special_agent_module_unavailable:{category}:{type(exc).__name__}")
    return {
        "status": "valid" if not errors else "invalid",
        "registry_version": registry["version"],
        "taxonomy_version": tax["version"],
        "sector_agents": len(configured),
        "special_agents": len(registry.get("special_agents", {})),
        "subcategory_frameworks": sum(len(value["subcategories"]) for value in registry["agents"].values()),
        "errors": errors,
    }


def route_node(node_id: str, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    registry = registry or load_registry()
    parts = str(node_id or "").split(".")
    if len(parts) < 2:
        raise ValueError("A classified sector.subcategory node is required")
    sector, subcategory = parts[0], parts[1]
    agent = registry["agents"].get(sector)
    if not agent:
        raise ValueError(f"No sector agent for {sector}")
    kpis = agent["subcategories"].get(subcategory)
    if kpis is None:
        raise ValueError(f"No subcategory framework for {sector}.{subcategory}")
    return {
        "agent_id": agent["agent_id"],
        "sector": sector,
        "subcategory": subcategory,
        "leaf_node": node_id,
        "framework_id": f"{agent['agent_id']}.{subcategory}.v1",
        "required_sector_kpis": kpis,
    }


def scaffold_packet(symbol: str, venue_code: str, node_id: str, run_id: str,
                    snapshot_id: str, classification_id: str) -> dict[str, Any]:
    route = route_node(node_id)
    stamp = datetime.now(timezone.utc).isoformat()
    identity = [run_id, snapshot_id, venue_code, symbol.upper(), node_id, route["framework_id"]]
    return {
        "schema": "ecosystem.sector_context_packet.v1",
        "packet_id": "sector_packet_" + digest(identity)[:24],
        "run_id": run_id,
        "snapshot_id": snapshot_id,
        "symbol": symbol.upper(),
        "venue_code": venue_code,
        "classification_id": classification_id,
        "sector": route["sector"],
        "subcategory": route["subcategory"],
        "leaf_node": node_id,
        "agent_id": route["agent_id"],
        "framework_id": route["framework_id"],
        "required_sector_kpis": route["required_sector_kpis"],
        "business_model": None,
        "value_chain_position": None,
        "revenue_drivers": [],
        "cost_drivers": [],
        "peer_group": [],
        "competitive_position": None,
        "sector_kpis": {},
        "industry_risks": [],
        "company_specific_risks": [],
        "catalysts": [],
        "source_references": [],
        "unknown_fields": [
            "business_model", "value_chain_position", "revenue_drivers", "cost_drivers",
            "peer_group", "competitive_position", "sector_kpis", "industry_risks",
            "company_specific_risks", "catalysts"
        ],
        "evidence_gap_ids": [],
        "confidence": "insufficient",
        "status": "awaiting_sector_evidence",
        "created_at": stamp,
    }


def validate_packet(packet: dict[str, Any], registry: dict[str, Any] | None = None) -> dict[str, Any]:
    registry = registry or load_registry()
    required = set(registry["common_sector_packet"]["required_fields"])
    missing = sorted(field for field in required if field not in packet)
    route_errors: list[str] = []
    try:
        route = route_node(packet.get("leaf_node"), registry)
        if packet.get("agent_id") != route["agent_id"]:
            route_errors.append("agent_route_mismatch")
        if packet.get("framework_id") != route["framework_id"]:
            route_errors.append("framework_route_mismatch")
    except ValueError as exc:
        route_errors.append(str(exc))
    if packet.get("confidence") not in registry["common_sector_packet"]["confidence_values"]:
        route_errors.append("invalid_confidence")
    errors = [f"missing:{field}" for field in missing] + route_errors
    return {"status": "valid" if not errors else "invalid", "errors": errors}


def analyze_input(payload: dict[str, Any]) -> dict[str, Any]:
    identity = payload.get("identity") or {}
    evidence = payload.get("evidence") or {}
    route = route_node(identity.get("leaf_node"))
    module = importlib.import_module(AGENT_MODULES[route["sector"]])
    return module.analyze(identity, evidence)


def analyze_unresolved_input(payload: dict[str, Any]) -> dict[str, Any]:
    identity = payload.get("identity") or {}
    evidence = payload.get("evidence") or {}
    module = importlib.import_module(SPECIAL_AGENT_MODULES["unresolved_classification"])
    return module.analyze(identity, evidence)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate-registry")
    p = sub.add_parser("route"); p.add_argument("--node", required=True)
    p = sub.add_parser("scaffold")
    p.add_argument("--symbol", required=True); p.add_argument("--venue-code", required=True)
    p.add_argument("--node", required=True); p.add_argument("--run-id", required=True)
    p.add_argument("--snapshot-id", required=True); p.add_argument("--classification-id", required=True)
    p.add_argument("--out", type=Path)
    p = sub.add_parser("analyze"); p.add_argument("--input", type=Path, required=True); p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("analyze-unresolved"); p.add_argument("--input", type=Path, required=True); p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "validate-registry":
        result = validate_registry()
    elif args.command == "route":
        result = route_node(args.node)
    elif args.command == "scaffold":
        result = scaffold_packet(args.symbol, args.venue_code, args.node, args.run_id, args.snapshot_id, args.classification_id)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    elif args.command == "analyze":
        result = analyze_input(json.loads(args.input.read_text(encoding="utf-8")))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        result = analyze_unresolved_input(json.loads(args.input.read_text(encoding="utf-8")))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") != "invalid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
