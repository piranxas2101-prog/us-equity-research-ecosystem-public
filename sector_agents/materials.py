from .base import analyze_sector_evidence
SECTOR = "materials"
AGENT_ID = "sector_materials"
def analyze(identity, evidence): return analyze_sector_evidence(SECTOR, identity, evidence)
