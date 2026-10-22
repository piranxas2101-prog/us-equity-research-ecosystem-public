from .base import analyze_sector_evidence
SECTOR = "real_estate"
AGENT_ID = "sector_real_estate"
def analyze(identity, evidence): return analyze_sector_evidence(SECTOR, identity, evidence)
