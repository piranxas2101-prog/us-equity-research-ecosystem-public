from .base import analyze_sector_evidence
SECTOR = "financials"
AGENT_ID = "sector_financials"
def analyze(identity, evidence): return analyze_sector_evidence(SECTOR, identity, evidence)
