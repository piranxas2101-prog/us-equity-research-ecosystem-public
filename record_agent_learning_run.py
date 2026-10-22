"""Record every same-run agent artifact that is present; skip absent or stale artifacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import agent_learning_control_plane as learning
import agent_run_audit
import market_intel_db

ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--db", type=Path, default=market_intel_db.DEFAULT_DB_PATH)
    args = parser.parse_args()
    objectives = json.loads((ROOT / "agent_learning_objectives.json").read_text(encoding="utf-8"))["agents"]
    recorded, skipped = {}, {}
    with market_intel_db.connect(args.db) as conn:
        learning.init_schema(conn)
        for agent, relative in agent_run_audit.ARTIFACTS.items():
            path = ROOT / relative
            if not path.exists() or agent not in objectives:
                skipped[agent] = "artifact_absent"
                continue
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if str(payload.get("run_id") or "") != args.run_id:
                skipped[agent] = "different_run"
                continue
            config = objectives[agent]
            key = config["rows_key"]
            if not isinstance(payload.get(key), list):
                skipped[agent] = f"rows_key_absent:{key}"
                continue
            recorded[agent] = learning.record_artifact(
                conn, path, agent, config["task"], key,
                f"{agent}.{config['task']}.v1",
            )
    print(json.dumps({"run_id": args.run_id, "recorded": recorded, "skipped": skipped}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
