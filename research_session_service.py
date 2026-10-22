"""Windowless owner for one research session's Slack gateway and health loop."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from research_team_chat import load_dotenv
from slack_agent_gateway import GatewayProcessLock
from slack_research_health import maybe_notify, report


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_STATE = PROJECT_ROOT / "data/research_session_state.json"
DEFAULT_STOP = PROJECT_ROOT / "data/research_session.stop"
DEFAULT_LOCK = PROJECT_ROOT / "data/research_session.lock"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def creation_flags() -> int:
    if os.name != "nt":
        return 0
    return (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    try:
        for attempt in range(5):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def process_exists(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        process = kernel32.OpenProcess(0x1000, False, int(pid))
        if not process:
            return False
        exit_code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code))
        kernel32.CloseHandle(process)
        return bool(ok and exit_code.value == 259)  # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


class ResearchSessionService:
    def __init__(
        self,
        *,
        project_root: Path = PROJECT_ROOT,
        state_path: Path = DEFAULT_STATE,
        stop_path: Path = DEFAULT_STOP,
        lock_path: Path = DEFAULT_LOCK,
        health_interval_seconds: int = 900,
        poll_seconds: float = 1.0,
        max_restarts: int = 3,
        gateway_command: list[str] | None = None,
        health_runner: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.state_path = state_path
        self.stop_path = stop_path
        self.lock_path = lock_path
        self.health_interval_seconds = max(1, health_interval_seconds)
        self.poll_seconds = max(0.05, poll_seconds)
        self.max_restarts = max(0, max_restarts)
        self.gateway_command = gateway_command or [
            sys.executable,
            str(self.project_root / "slack_agent_gateway.py"),
            "--workers",
            "2",
        ]
        self.health_runner = health_runner or self._health
        self.session_lock = GatewayProcessLock(self.lock_path)
        self.gateway: subprocess.Popen[Any] | None = None
        self.gateway_stdout: Any | None = None
        self.gateway_stderr: Any | None = None
        self.restart_count = 0
        self.started_at = now_iso()
        self.last_health: dict[str, Any] | None = None

    def _write_state(self, status: str, **extra: Any) -> None:
        payload: dict[str, Any] = {
            "schema": "ecosystem.research_session.v1",
            "status": status,
            "service_pid": os.getpid(),
            "gateway_pid": self.gateway.pid if self.gateway and self.gateway.poll() is None else None,
            "started_at": self.started_at,
            "updated_at": now_iso(),
            "restart_count": self.restart_count,
            "research_only": True,
            "scheduled_tasks_required": False,
            "last_health": self.last_health,
        }
        payload.update(extra)
        atomic_json(self.state_path, payload)

    def _open_gateway_logs(self) -> None:
        log_root = self.project_root / "logs"
        log_root.mkdir(parents=True, exist_ok=True)
        self.gateway_stdout = (log_root / "slack_agent_gateway.stdout.log").open(
            "a", encoding="utf-8"
        )
        self.gateway_stderr = (log_root / "slack_agent_gateway.stderr.log").open(
            "a", encoding="utf-8"
        )

    def _start_gateway(self) -> None:
        self._open_gateway_logs()
        kwargs: dict[str, Any] = {
            "cwd": self.project_root,
            "stdout": self.gateway_stdout,
            "stderr": self.gateway_stderr,
            "stdin": subprocess.DEVNULL,
            "creationflags": creation_flags(),
        }
        if os.name != "nt":
            kwargs["start_new_session"] = True
        self.gateway = subprocess.Popen(self.gateway_command, **kwargs)

    def _stop_gateway(self) -> None:
        if self.gateway and self.gateway.poll() is None:
            self.gateway.terminate()
            try:
                self.gateway.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.gateway.kill()
                self.gateway.wait(timeout=5)
        for handle in (self.gateway_stdout, self.gateway_stderr):
            if handle is not None:
                handle.close()
        self.gateway_stdout = None
        self.gateway_stderr = None

    def _health(self) -> dict[str, Any]:
        args = SimpleNamespace(
            lock=str(self.project_root / "data/slack_agent_gateway.lock"),
            state=str(self.project_root / "data/slack_agent_gateway.sqlite3"),
            outbox=str(self.project_root / "output/integration_outbox/research_conversations"),
            acknowledgements=str(
                self.project_root / "output/integration_outbox/research_conversations/sent"
            ),
            claim_minutes=10,
            outbox_minutes=60,
        )
        current = report(args)
        maybe_notify(
            current,
            self.project_root / "data/slack_research_health_state.json",
            "C0BU132535F",
        )
        return current

    def run(self) -> int:
        load_dotenv(self.project_root / ".env")
        self.session_lock.acquire()
        self.stop_path.unlink(missing_ok=True)
        self._write_state("starting")
        exit_code = 0
        try:
            self._start_gateway()
            time.sleep(min(1.0, self.poll_seconds))
            if self.gateway.poll() is not None:
                raise RuntimeError("Slack gateway exited during research-session startup")
            next_health = time.monotonic()
            self._write_state("running")
            while not self.stop_path.exists():
                if self.gateway.poll() is not None:
                    if self.restart_count >= self.max_restarts:
                        raise RuntimeError("Slack gateway exceeded the session restart limit")
                    self.restart_count += 1
                    self._stop_gateway()
                    self._start_gateway()
                    time.sleep(min(1.0, self.poll_seconds))
                    self._write_state("running", recovery="gateway_restarted")
                    next_health = time.monotonic()
                if time.monotonic() >= next_health:
                    self.last_health = self.health_runner()
                    self._write_state("running")
                    next_health = time.monotonic() + self.health_interval_seconds
                time.sleep(self.poll_seconds)
        except Exception as exc:
            exit_code = 1
            self._write_state("failed", error=f"{type(exc).__name__}: {exc}")
        finally:
            try:
                self._stop_gateway()
                self.stop_path.unlink(missing_ok=True)
                self._write_state(
                    "stopped" if exit_code == 0 else "failed",
                    stopped_at=now_iso(),
                    gateway_pid=None,
                )
            finally:
                self.session_lock.close()
        return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Own one windowless research session.")
    parser.add_argument("--health-interval-seconds", type=int, default=900)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--max-restarts", type=int, default=3)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return ResearchSessionService(
        health_interval_seconds=args.health_interval_seconds,
        poll_seconds=args.poll_seconds,
        max_restarts=args.max_restarts,
    ).run()


if __name__ == "__main__":
    raise SystemExit(main())
