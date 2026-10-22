from .base import analyze_sector_evidence
SECTOR = "energy"
AGENT_ID = "sector_energy"
def analyze(identity, evidence): return analyze_sector_evidence(SECTOR, identity, evidence)
