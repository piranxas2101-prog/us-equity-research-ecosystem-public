"""Start, stop, and inspect the windowless research-session service."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_session_service import DEFAULT_STATE, DEFAULT_STOP, PROJECT_ROOT, atomic_json, creation_flags, process_exists


def read_state(path: Path = DEFAULT_STATE) -> dict[str, Any]:
    if not path.exists():
        return {"status": "stopped", "service_pid": None, "gateway_pid": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "unknown", "service_pid": None, "gateway_pid": None}


def live_status() -> dict[str, Any]:
    state = read_state()
    state["service_process_alive"] = process_exists(state.get("service_pid"))
    state["gateway_process_alive"] = process_exists(state.get("gateway_pid"))
    return state


def pythonw_path() -> Path:
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return candidate if candidate.exists() else Path(sys.executable)


def terminate_tree(pid: int | None) -> None:
    if not process_exists(pid):
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags(),
            check=False,
        )
    else:
        os.kill(int(pid), 15)


def start(timeout_seconds: int) -> dict[str, Any]:
    current = live_status()
    if current.get("status") in {"starting", "running"} and current["service_process_alive"]:
        return {"result": "already_running", **current}
    DEFAULT_STOP.unlink(missing_ok=True)
    log_root = PROJECT_ROOT / "logs"
    log_root.mkdir(parents=True, exist_ok=True)
    stdout = (log_root / "research_session_service.stdout.log").open("a", encoding="utf-8")
    stderr = (log_root / "research_session_service.stderr.log").open("a", encoding="utf-8")
    command = [str(pythonw_path()), str(PROJECT_ROOT / "research_session_service.py")]
    kwargs: dict[str, Any] = {
        "cwd": PROJECT_ROOT,
        "stdin": subprocess.DEVNULL,
        "stdout": stdout,
        "stderr": stderr,
        "creationflags": creation_flags(),
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    stdout.close()
    stderr.close()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = live_status()
        if state.get("status") == "running" and state.get("service_process_alive"):
            return {"result": "started", **state}
        if state.get("status") == "failed" or process.poll() is not None:
            return {"result": "failed", **state}
        time.sleep(0.25)
    return {"result": "timeout", **live_status()}


def stop(timeout_seconds: int) -> dict[str, Any]:
    current = live_status()
    if not current.get("service_process_alive") and not current.get("gateway_process_alive"):
        return {"result": "already_stopped", **current}
    atomic_json(
        DEFAULT_STOP,
        {"requested_at": datetime.now(timezone.utc).isoformat(), "requested_by": "research_session_control"},
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = live_status()
        if not state.get("service_process_alive") and not state.get("gateway_process_alive"):
            return {"result": "stopped", **state}
        time.sleep(0.25)
    stale = live_status()
    terminate_tree(stale.get("gateway_pid"))
    terminate_tree(stale.get("service_pid"))
    time.sleep(0.5)
    forced = live_status()
    forced.update(
        {
            "status": "stopped",
            "service_pid": None,
            "gateway_pid": None,
            "stopped_at": datetime.now(timezone.utc).isoformat(),
            "stop_mode": "forced_after_timeout",
        }
    )
    atomic_json(DEFAULT_STATE, forced)
    DEFAULT_STOP.unlink(missing_ok=True)
    return {"result": "forced_stopped", **live_status()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control the research-scoped background services.")
    parser.add_argument("action", choices=("start", "stop", "status"))
    parser.add_argument("--timeout-seconds", type=int, default=30)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = start(args.timeout_seconds) if args.action == "start" else stop(args.timeout_seconds) if args.action == "stop" else live_status()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("result") not in {"failed", "timeout"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
