from .base import analyze_sector_evidence
SECTOR = "consumer_staples"
AGENT_ID = "sector_consumer_staples"
def analyze(identity, evidence): return analyze_sector_evidence(SECTOR, identity, evidence)
