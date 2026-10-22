"""Sector research agents plus the unresolved-classification specialist."""

AGENT_MODULES = {
    "communication_services": "sector_agents.communication_services",
    "consumer_discretionary": "sector_agents.consumer_discretionary",
    "consumer_staples": "sector_agents.consumer_staples",
    "energy": "sector_agents.energy",
    "financials": "sector_agents.financials",
    "healthcare": "sector_agents.healthcare",
    "industrials": "sector_agents.industrials",
    "materials": "sector_agents.materials",
    "real_estate": "sector_agents.real_estate",
    "technology": "sector_agents.technology",
    "utilities": "sector_agents.utilities",
}

SPECIAL_AGENT_MODULES = {
    "unresolved_classification": "sector_agents.unresolved_classification",
}
