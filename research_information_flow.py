"""Versioned research envelopes, packet references and checkpoints."""

from __future__ import annotations
from dataclasses import asdict, dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

OBSERVATION_SCHEMA = "ecosystem.observation_envelope.v1"
RUN_STATE_SCHEMA = "ecosystem.research_run_state.v1"
ALLOWED_STATUSES = {"ok", "missing", "blocked", "degraded", "conflict"}
REASON_CODES = {"rate_limited", "daily_budget_exhausted", "minute_budget_exhausted",
    "minimum_interval", "not_configured", "not_entitled", "no_data", "stale_data",
    "identity_conflict", "schema_error", "network_failure", "source_unavailable",
    "provider_policy_missing", "conflicting_sources"}

def utc_now() -> str: return datetime.now(timezone.utc).isoformat()
def canonical_json(value: Any) -> str: return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
def digest(value: Any) -> str: return hashlib.sha256(canonical_json(value).encode()).hexdigest()

def normalize_reason(status: str) -> str:
    value = str(status or "").strip().lower()
    mapping = {"missing_configuration": "not_configured", "auth_failed": "not_entitled",
        "forbidden_or_plan": "not_entitled", "timeout_or_network": "network_failure",
        "invalid_json": "schema_error", "http_error": "source_unavailable",
        "ticker_not_found": "no_data", "empty_or_ambiguous_response": "no_data"}
    if value.startswith("shared_cooldown") or "rate_limited" in value: return "rate_limited"
    return mapping.get(value, value if value in REASON_CODES else "source_unavailable")

@dataclass(frozen=True)
class ObservationEnvelope:
    observation_id: str; schema: str; provider: str; capability: str; subject_id: str
    ticker: str | None; source_url: str | None; observed_at: str; as_of: str | None
    status: str; reason_code: str | None; raw_sha256: str | None
    payload_schema: str; payload: dict[str, Any]

    @classmethod
    def create(cls, *, provider: str, capability: str, subject_id: str,
               payload_schema: str, payload: dict[str, Any], ticker: str | None = None,
               source_url: str | None = None, observed_at: str | None = None,
               as_of: str | None = None, status: str = "ok", reason_code: str | None = None,
               raw_sha256: str | None = None) -> "ObservationEnvelope":
        body = {"provider": provider, "capability": capability, "subject_id": subject_id,
            "ticker": ticker, "source_url": source_url, "observed_at": observed_at or utc_now(),
            "as_of": as_of, "status": status, "reason_code": reason_code,
            "raw_sha256": raw_sha256, "payload_schema": payload_schema, "payload": payload}
        result = cls("obs_" + digest(body)[:24], OBSERVATION_SCHEMA, **body); result.validate(); return result

    def validate(self) -> None:
        if not self.provider or not self.capability or not self.subject_id: raise ValueError("provider, capability and subject_id are required")
        if self.status not in ALLOWED_STATUSES: raise ValueError(f"Unsupported observation status: {self.status}")
        if self.status != "ok" and not self.reason_code: raise ValueError("A non-ok observation requires reason_code")
        if self.reason_code and self.reason_code not in REASON_CODES: raise ValueError(f"Unsupported reason_code: {self.reason_code}")
        if not isinstance(self.payload, dict): raise ValueError("payload must be an object")

    def as_dict(self) -> dict[str, Any]: return asdict(self)

class ResearchPacketRegistry:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path); self.db_path.parent.mkdir(parents=True, exist_ok=True); self._schema()
    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path); db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()
    def _schema(self) -> None:
        with self._connect() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS research_observation(observation_id TEXT PRIMARY KEY,provider TEXT NOT NULL,capability TEXT NOT NULL,subject_id TEXT NOT NULL,ticker TEXT,status TEXT NOT NULL,reason_code TEXT,observed_at TEXT NOT NULL,payload_json TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS research_observation_subject ON research_observation(subject_id,capability,observed_at);
              CREATE TABLE IF NOT EXISTS research_packet(packet_id TEXT PRIMARY KEY,packet_schema TEXT NOT NULL,subject_id TEXT NOT NULL,input_signature TEXT NOT NULL,created_at TEXT NOT NULL,payload_json TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS research_run_state(run_id TEXT NOT NULL,subject_id TEXT NOT NULL,state_json TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(run_id,subject_id));
              CREATE TABLE IF NOT EXISTS research_stage_checkpoint(run_id TEXT NOT NULL,subject_id TEXT NOT NULL,stage TEXT NOT NULL,input_signature TEXT NOT NULL,output_packet_id TEXT,status TEXT NOT NULL,completed_at TEXT NOT NULL,PRIMARY KEY(run_id,subject_id,stage));""")
            db.execute("""CREATE TABLE IF NOT EXISTS research_evidence_gap(
              gap_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,subject_id TEXT NOT NULL,
              ticker TEXT,capability TEXT NOT NULL,provider TEXT NOT NULL,
              reason_code TEXT NOT NULL,status TEXT NOT NULL,next_eligible_at TEXT,
              observation_id TEXT,created_at TEXT NOT NULL,resolved_at TEXT)""")
    def put_observation(self, observation: ObservationEnvelope) -> str:
        observation.validate(); payload = observation.as_dict()
        with self._connect() as db: db.execute("INSERT OR IGNORE INTO research_observation VALUES(?,?,?,?,?,?,?,?,?)", (observation.observation_id, observation.provider, observation.capability, observation.subject_id, observation.ticker, observation.status, observation.reason_code, observation.observed_at, canonical_json(payload)))
        return observation.observation_id
    def put_packet(self, packet_schema: str, subject_id: str, payload: dict[str, Any], input_ids: list[str]) -> str:
        signature = digest({"schema": packet_schema, "inputs": sorted(input_ids)})
        body = {**payload, "schema": packet_schema, "subject_id": subject_id, "input_signature": signature}
        packet_id = "pkt_" + digest(body)[:24]; body["packet_id"] = packet_id
        with self._connect() as db: db.execute("INSERT OR IGNORE INTO research_packet VALUES(?,?,?,?,?,?)", (packet_id, packet_schema, subject_id, signature, utc_now(), canonical_json(body)))
        return packet_id
    def get_packet(self, packet_id: str) -> dict[str, Any] | None:
        with self._connect() as db: row = db.execute("SELECT payload_json FROM research_packet WHERE packet_id=?", (packet_id,)).fetchone()
        return json.loads(row[0]) if row else None
    def set_packet_ref(self, run_id: str, subject_id: str, ref_name: str, packet_id: str) -> None:
        with self._connect() as db:
            row = db.execute("SELECT state_json FROM research_run_state WHERE run_id=? AND subject_id=?", (run_id, subject_id)).fetchone()
            state = json.loads(row[0]) if row else {"schema": RUN_STATE_SCHEMA, "run_id": run_id, "subject_id": subject_id, "packet_refs": {}, "evidence_gap_ids": [], "completed_stages": []}
            state["packet_refs"][ref_name] = packet_id
            db.execute("INSERT OR REPLACE INTO research_run_state VALUES(?,?,?,?)", (run_id, subject_id, canonical_json(state), utc_now()))
    def record_gap(self, *, run_id: str, observation: ObservationEnvelope,
                   next_eligible_at: str | None = None) -> str:
        if observation.status == "ok" or not observation.reason_code: raise ValueError("Only failed observations can create evidence gaps")
        gap_id = "gap_" + digest([run_id, observation.subject_id, observation.capability, observation.provider, observation.reason_code])[:24]
        state = "cooldown" if observation.reason_code in {"rate_limited", "daily_budget_exhausted", "minute_budget_exhausted", "minimum_interval"} else "open"
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO research_evidence_gap VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (gap_id, run_id, observation.subject_id, observation.ticker, observation.capability,
                 observation.provider, observation.reason_code, state, next_eligible_at,
                 observation.observation_id, utc_now(), None))
            row = db.execute("SELECT state_json FROM research_run_state WHERE run_id=? AND subject_id=?", (run_id, observation.subject_id)).fetchone()
            run_state = json.loads(row[0]) if row else {"schema": RUN_STATE_SCHEMA, "run_id": run_id, "subject_id": observation.subject_id, "packet_refs": {}, "evidence_gap_ids": [], "completed_stages": []}
            run_state["evidence_gap_ids"] = list(dict.fromkeys([*run_state["evidence_gap_ids"], gap_id]))
            db.execute("INSERT OR REPLACE INTO research_run_state VALUES(?,?,?,?)", (run_id, observation.subject_id, canonical_json(run_state), utc_now()))
        return gap_id
    def checkpoint_matches(self, run_id: str, subject_id: str, stage: str, input_signature: str) -> bool:
        with self._connect() as db: row = db.execute("SELECT status,input_signature FROM research_stage_checkpoint WHERE run_id=? AND subject_id=? AND stage=?", (run_id, subject_id, stage)).fetchone()
        return bool(row and row["status"] == "completed" and row["input_signature"] == input_signature)
    def complete_stage(self, run_id: str, subject_id: str, stage: str, input_signature: str, output_packet_id: str | None) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO research_stage_checkpoint VALUES(?,?,?,?,?,?,?)", (run_id, subject_id, stage, input_signature, output_packet_id, "completed", utc_now()))
            row = db.execute("SELECT state_json FROM research_run_state WHERE run_id=? AND subject_id=?", (run_id, subject_id)).fetchone()
            state = json.loads(row[0]) if row else {"schema": RUN_STATE_SCHEMA, "run_id": run_id, "subject_id": subject_id, "packet_refs": {}, "evidence_gap_ids": [], "completed_stages": []}
            state["completed_stages"] = list(dict.fromkeys([*state["completed_stages"], stage]))
            db.execute("INSERT OR REPLACE INTO research_run_state VALUES(?,?,?,?)", (run_id, subject_id, canonical_json(state), utc_now()))
