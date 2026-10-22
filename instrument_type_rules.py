"""One definition of "what kind of security is this" for Babis, Giannis, Petros, Yankos and Periklis.

Before (found 2026-10-03): each agent guessed the type from the last letter of the ticker (W -> warrant,
U -> unit, R -> right) and from words inside the name ("trust", "fund", "etf"). 118 of 1,064 shortlisted
ordinary companies (ACIW, ARW, AMKR, AR, ASR, ATR, AWR, ...) were labelled warrant / unit / right / fund, so
Giannis put them in "Avoid For Now" or "Complex Security Risk" and Petros rejected them.

Now:
1. the Babis registry is the authority: its instrument type (equity, ADR, warrant, unit, right, preferred,
   ETF/ETN, fund) is mapped to the agents' vocabulary and used as is;
2. only when the registry has no usable type does the name/ticker heuristic run: the words of the name decide
   (a name that states an ordinary security - "Common Stock", "Ordinary Shares", "Common Units" - is never turned
   into a unit, preferred or fund by a word such as "Trust"), and the last letter of the ticker is used only for a
   bare symbol that has no name at all.
The result is "common" for ordinary equity and a specific label for everything else.
"""
from __future__ import annotations

import re

# Registry instrument type -> agent vocabulary. Types not listed (spac, debt, unknown_instrument, empty)
# have no unambiguous mapping and fall back to the heuristic.
REGISTRY_TYPE_MAP = {
    "equity_candidate": "common",
    "adr_candidate": "common",
    "warrant": "warrant",
    "unit": "unit",
    "right": "right",
    "preferred": "preferred",
    "etf": "fund_or_trust",
    "etn": "fund_or_trust",
    "fund_candidate": "fund_or_trust",
}
AGENT_TYPES = frozenset({"common", "warrant", "unit", "right", "preferred", "fund_or_trust"})
FLAGS = {
    "warrant": "warrant_structure",
    "unit": "unit_structure",
    "right": "rights_structure",
    "preferred": "preferred_security",
    "fund_or_trust": "fund_or_trust_structure",
}

_ORDINARY = re.compile(
    r"\b(common stock|common shares?|ordinary shares?|common units?|capital stock|shares of beneficial interest"
    r"|class [a-z] (common|ordinary)|american depositary shares?)\b")
_WARRANT = re.compile(r"\bwarrants?\b")
_UNIT = re.compile(r"\bunits?\b")
_RIGHT = re.compile(r"\brights?\b")
_PREFERRED = re.compile(r"\b(preferred|preference)\b")
_FUND = re.compile(r"\b(fund|trust|etf|etn)\b")


def normalize_instrument_type(value: object) -> str:
    """A registry type becomes the agents' type; anything else is returned as given (stripped)."""
    text = str(value or "").strip()
    return REGISTRY_TYPE_MAP.get(text.lower(), text)


def infer_instrument_type(ticker: object, name: object, registry_type: object = None) -> tuple[str, str]:
    """(agent instrument type, basis) with basis 'registry' or 'ticker_name_heuristic'."""
    mapped = REGISTRY_TYPE_MAP.get(str(registry_type or "").strip().lower())
    if mapped:
        return mapped, "registry"
    symbol = str(ticker or "").upper().strip()
    text = str(name or "").lower()
    ordinary = bool(_ORDINARY.search(text))
    # The last letter of a ticker says nothing once the name is known (Equinor "EQNR" is not a right);
    # it is a last resort for a bare symbol with no name at all.
    by_suffix = not text.strip()
    if _WARRANT.search(text) or (by_suffix and symbol.endswith(("W", "WS"))):
        return "warrant", "ticker_name_heuristic"
    if not ordinary and (_UNIT.search(text) or (by_suffix and symbol.endswith("U"))):
        return "unit", "ticker_name_heuristic"
    if _RIGHT.search(text) or (by_suffix and symbol.endswith("R")):
        return "right", "ticker_name_heuristic"
    if "-P" in symbol or (_PREFERRED.search(text) and not ordinary):
        return "preferred", "ticker_name_heuristic"
    if not ordinary and _FUND.search(text):
        return "fund_or_trust", "ticker_name_heuristic"
    return "common", "ticker_name_heuristic"


def instrument_profile(ticker: object, name: object, registry_type: object = None) -> dict[str, object]:
    """The dict Babis and Giannis attach to a row."""
    instrument_type, basis = infer_instrument_type(ticker, name, registry_type)
    return {
        "instrument_type": instrument_type,
        "complex_security": instrument_type != "common",
        "instrument_flags": [FLAGS[instrument_type]] if instrument_type in FLAGS else [],
        "instrument_basis": basis,
    }
