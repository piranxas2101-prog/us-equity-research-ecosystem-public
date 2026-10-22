from .base import analyze_sector_evidence
SECTOR = "technology"
AGENT_ID = "sector_technology"
def analyze(identity, evidence): return analyze_sector_evidence(SECTOR, identity, evidence)
