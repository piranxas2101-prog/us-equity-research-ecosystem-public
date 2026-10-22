from .base import analyze_sector_evidence
SECTOR = "utilities"
AGENT_ID = "sector_utilities"
def analyze(identity, evidence): return analyze_sector_evidence(SECTOR, identity, evidence)
