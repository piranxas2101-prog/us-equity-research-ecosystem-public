from .base import analyze_sector_evidence
SECTOR = "communication_services"
AGENT_ID = "sector_communication_services"
def analyze(identity, evidence): return analyze_sector_evidence(SECTOR, identity, evidence)
