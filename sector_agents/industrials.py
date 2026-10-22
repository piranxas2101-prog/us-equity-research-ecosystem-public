from .base import analyze_sector_evidence
SECTOR = "industrials"
AGENT_ID = "sector_industrials"
def analyze(identity, evidence): return analyze_sector_evidence(SECTOR, identity, evidence)
