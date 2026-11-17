"""Jarvis's suggestion for each classification review (owner 2026-11-17: Jarvis does the homework, the owner clicks).

Deterministic, from the votes the review item already holds - no model. Each source votes with a weight (Claude's
call, 2026-11-17, from how reliable each source has been): the provider's explicit industry 3, an exact SEC SIC 3, the
directory crosswalk 2, a SEC SIC that only knows the sector 1, the local model's reading of the description 1
(advisory). Votes count per sector; a suggestion needs a single leading sector backed by two sources or by twice the
runner-up's weight, and names the most specific node of the strongest vote in that sector. A prior owner approval is
kept; a probable SPAC waits for its second signal. The suggestion is only shown: applying it is the owner's click,
through the existing review decision.
"""
from __future__ import annotations

from typing import Any

WEIGHTS = {("profile", "explicit_provider_alias"): 3, ("directory", "explicit_internal_industry_crosswalk"): 2,
           ("sec", "sic_range_sector_only"): 1, ("sec", None): 3, ("description", None): 1}


def _weight(vote: dict[str, Any]) -> int:
    source, basis = str(vote.get("source")), str(vote.get("basis") or "")
    if (source, basis) in WEIGHTS:
        return WEIGHTS[(source, basis)]
    return WEIGHTS.get((source, None), 1)


def suggest(item: dict[str, Any]) -> dict[str, Any]:
    reason = item.get("reason")
    if reason == "would_contradict_prior_approval" and item.get("current_node"):
        return {"decision": "confirm", "node": item["current_node"], "confidence": "strong",
                "reason": "το είχες ήδη εγκρίνει· οι νέες ψήφοι δεν φέρνουν δεύτερη πηγή που να το αλλάζει"}
    if reason == "probable_spac_needs_second_signal":
        return {"decision": None, "node": None, "confidence": None,
                "reason": "πιθανό SPAC: δεν προτείνω πριν φανεί η δεύτερη ένδειξη"}
    votes = [v for v in (item.get("votes") or []) + (item.get("advisory_votes") or []) if v.get("node")]
    if not votes:
        return {"decision": None, "node": None, "confidence": None, "reason": "δεν υπάρχουν ψήφοι"}
    sectors: dict[str, dict[str, Any]] = {}
    for vote in votes:
        sector = str(vote["node"]).split(".")[0]
        cell = sectors.setdefault(sector, {"weight": 0, "sources": set(), "best": None})
        cell["weight"] += _weight(vote)
        cell["sources"].add(vote.get("source"))
        if cell["best"] is None or _weight(vote) > _weight(cell["best"]) or (
                _weight(vote) == _weight(cell["best"]) and str(vote["node"]).count(".") > str(cell["best"]["node"]).count(".")):
            cell["best"] = vote
    ranked = sorted(sectors.items(), key=lambda kv: -kv[1]["weight"])
    top_sector, top = ranked[0]
    runner = ranked[1][1]["weight"] if len(ranked) > 1 else 0
    two_sources = len(top["sources"]) >= 2
    if top["weight"] <= runner or not (two_sources or top["weight"] >= 2 * runner):
        return {"decision": None, "node": None, "confidence": None,
                "reason": "δεν μπορώ να προτείνω: οι πηγές διαφωνούν χωρίς καθαρή πλειοψηφία"}
    agreeing = ", ".join(sorted(str(s) for s in top["sources"]))
    confidence = "strong" if two_sources and top["weight"] >= 2 * max(runner, 1) else "weak"
    return {"decision": "choose", "node": top["best"]["node"], "confidence": confidence,
            "reason": f"τομέας {top_sector}: {agreeing} (βάρος {top['weight']} έναντι {runner})"}
