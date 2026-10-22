from .base import analyze_sector_evidence
SECTOR = "healthcare"
AGENT_ID = "sector_healthcare"
def analyze(identity, evidence): return analyze_sector_evidence(SECTOR, identity, evidence)
