from .base import analyze_sector_evidence
SECTOR = "consumer_discretionary"
AGENT_ID = "sector_consumer_discretionary"
def analyze(identity, evidence): return analyze_sector_evidence(SECTOR, identity, evidence)
