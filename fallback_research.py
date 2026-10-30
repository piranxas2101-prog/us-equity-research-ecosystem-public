"""Fallback research (owner's decision 2026-10-30): when tonight's full research run did not complete - a check
before it started blocked it, it failed, or it was interrupted - and positions are held, a research-only
daily_pipeline.py run on the last good shortlist and the held positions stands in for it. The afternoon session
then places orders only on that run's traders' files and still manages the held positions.

    fallback_research.py ensure --trigger nightly --wait   nightly_research.ps1 / hibernate_after_run.ps1
    fallback_research.py ensure --trigger boot             fallback_research_on_boot.ps1 (the computer is opened)
    fallback_research.py status

Once a day: output/fallback_research_latest.json records the run; start_trader_afternoon_session.ps1 reads it and
the run's output/pipeline_runs/<run_id>/pipeline_state.json. Opening the computer starts it only between 07:00
Athens (before that the nightly run decides) and 14:00 New York (the last time the session waits for a run).
The run never submits orders. Switch: scanner_config.json -> paper_session.fallback_research ("on" / "off").
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
POINTER = OUTPUT / "full_research_control_plane_background_latest.json"
RECORD = OUTPUT / "fallback_research_latest.json"
POSITIONS = ROOT / "data" / "paper_positions.json"
RUNS = OUTPUT / "runs"
STATE_DIR = OUTPUT / "pipeline_runs"
CONFIG = ROOT / "scanner_config.json"
ATHENS = ZoneInfo("Europe/Athens")
NEW_YORK = ZoneInfo("America/New_York")
SCHEMA = "ecosystem.fallback_research.v1"
COMPLETED = {"completed", "completed_review_required"}
TERMINAL = COMPLETED | {"failed", "stopped_by_owner", "interrupted"}
BOOT_FROM_HOUR = 7       # Athens: before this the nightly run (00:30) is still the one that decides
LAST_WAIT_HOUR_NY = 14   # the afternoon session waits for a run until 14:00 New York


def load(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def process_alive(pid: Any) -> bool:
    """True while the process runs (Windows: OpenProcess + GetExitCodeProcess; never signals it)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    import ctypes

    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)


def nightly_state(pointer: dict[str, Any] | None, today: str, alive: Callable[[Any], bool]) -> str:
    """"completed" (finished today), "active" (its process still runs) or "not_completed"."""
    if not pointer:
        return "not_completed"
    status = str(pointer.get("status") or "")
    if status in COMPLETED:
        try:
            finished = datetime.fromisoformat(str(pointer.get("finished_at"))).astimezone(ATHENS).date().isoformat()
        except ValueError:
            finished = ""
        return "completed" if finished == today else "not_completed"
    if status not in TERMINAL and alive(pointer.get("pid")):
        return "active"
    return "not_completed"


def last_good_shortlist(runs_dir: Path = RUNS) -> tuple[str | None, list[str]]:
    """(run id, tickers) of the newest archived full run with a shortlist - never the *_latest file, which a run
    that stopped half way may have rewritten."""
    folders = sorted((p for p in runs_dir.glob("full_research_*") if p.is_dir()), key=lambda p: p.name, reverse=True)
    for folder in folders:
        payload = load(folder / "artifacts" / "babis_shortlist_latest.json") or {}
        tickers = list(dict.fromkeys(str(row.get("ticker") or "").strip().upper()
                                     for row in payload.get("shortlist") or [] if isinstance(row, dict)))
        tickers = [t for t in tickers if t]
        if tickers:
            return folder.name, tickers
    return None, []


def held_tickers(positions_path: Path = POSITIONS) -> list[str]:
    return sorted(str(t).upper() for t in (load(positions_path) or {}))


def watchlist(shortlist: list[str], held: list[str]) -> list[str]:
    return list(dict.fromkeys([*shortlist, *held]))


def switch_on(config: dict[str, Any] | None) -> bool:
    return str(((config or {}).get("paper_session") or {}).get("fallback_research", "on")).lower() != "off"


def decide(now: datetime, *, trigger: str, config: dict[str, Any] | None, pointer: dict[str, Any] | None,
           record: dict[str, Any] | None, held: list[str], alive: Callable[[Any], bool]) -> tuple[bool, str]:
    """(start?, why). ``now`` is aware; the session served is today's Athens date."""
    athens = now.astimezone(ATHENS)
    today = athens.date().isoformat()
    if not switch_on(config):
        return False, "switch paper_session.fallback_research is off"
    if record and record.get("date") == today:
        return False, f"fallback research already started today ({record.get('run_id')})"
    if athens.weekday() >= 5:
        return False, "weekend: no session to prepare"
    if trigger == "boot":
        if athens.hour < BOOT_FROM_HOUR:
            return False, f"before {BOOT_FROM_HOUR:02d}:00 Athens the nightly run decides"
        if now.astimezone(NEW_YORK).hour >= LAST_WAIT_HOUR_NY:
            return False, f"after {LAST_WAIT_HOUR_NY}:00 New York the session no longer waits for a run"
    state = nightly_state(pointer, today, alive)
    if state == "completed":
        return False, f"nightly run {pointer.get('run_id')} completed today"
    if state == "active":
        return False, f"nightly run {pointer.get('run_id')} is still running"
    if not held:
        return False, "no held positions"
    if pointer:
        return True, f"nightly run {pointer.get('run_id')} is '{pointer.get('status')}', not completed today"
    return True, "no nightly run pointer"


def prepare(now: datetime, *, trigger: str, reason: str, pointer: dict[str, Any] | None, held: list[str],
            runs_dir: Path = RUNS, output: Path = OUTPUT) -> dict[str, Any] | None:
    """Writes the watchlist and returns the record (None without a good shortlist and held positions)."""
    shortlist_run, shortlist = last_good_shortlist(runs_dir)
    tickers = watchlist(shortlist, held)
    if not tickers:
        return None
    athens = now.astimezone(ATHENS)
    run_id = f"run_{athens:%Y%m%d_%H%M%S}"
    path = output / f"fallback_watchlist_{athens:%Y%m%d}.json"
    path.write_text(json.dumps({"run_timestamp": athens.isoformat(), "source_shortlist": shortlist_run,
                                "count": len(tickers), "tickers": tickers}, indent=2), encoding="utf-8")
    return {"schema": SCHEMA, "date": athens.date().isoformat(), "run_id": run_id, "trigger": trigger,
            "reason": reason, "nightly_run_id": (pointer or {}).get("run_id"),
            "nightly_status": (pointer or {}).get("status"), "shortlist_run_id": shortlist_run,
            "shortlist_symbols": len(shortlist), "held_symbols": len(held),
            "held_not_on_shortlist": [t for t in held if t not in set(shortlist)], "symbols": len(tickers),
            "watchlist": str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path),
            "started_at": athens.isoformat(), "status": "started", "pid": None}


def write_record(record: dict[str, Any], path: Path = RECORD) -> None:
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def keep_awake(on: bool) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001 if on else 0x80000000)


def run_pipeline(record: dict[str, Any]) -> int:
    """Runs daily_pipeline.py on the record's watchlist in this process, keeping the computer awake."""
    record.update(status="running", pid=os.getpid())
    write_record(record)
    log = ROOT / "logs" / f"fallback_research_{record['run_id']}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    keep_awake(True)
    try:
        with log.open("a", encoding="utf-8") as handle:
            code = subprocess.run([sys.executable, "daily_pipeline.py", "run", "--locked-watchlist",
                                   record["watchlist"], "--run-id", record["run_id"]], cwd=ROOT, stdout=handle,
                                  stderr=subprocess.STDOUT).returncode
    finally:
        keep_awake(False)
    state = load(STATE_DIR / record["run_id"] / "pipeline_state.json") or {}
    record.update(status=state.get("status") or ("failed" if code else "unknown"), exit_code=code,
                  finished_at=datetime.now(ATHENS).isoformat(), log=str(log.relative_to(ROOT)))
    write_record(record)
    return code


def ensure(trigger: str, wait: bool) -> dict[str, Any]:
    now = datetime.now(ATHENS)
    pointer, held = load(POINTER), held_tickers()
    start, reason = decide(now, trigger=trigger, config=load(CONFIG), pointer=pointer, record=load(RECORD),
                           held=held, alive=process_alive)
    if not start:
        return {"started": False, "reason": reason}
    record = prepare(now, trigger=trigger, reason=reason, pointer=pointer, held=held)
    if record is None:
        return {"started": False, "reason": "no archived shortlist and no held positions"}
    write_record(record)
    if wait:
        run_pipeline(record)
    else:
        log = ROOT / "logs" / f"fallback_research_{record['run_id']}.launcher.log"
        # detached, new group, no window; out of the scheduled task's job when allowed, so the task ending
        # does not end the run
        flags = 0x00000008 | 0x00000200 | 0x08000000 if sys.platform == "win32" else 0
        command = [sys.executable, str(Path(__file__).resolve()), "run"]
        with log.open("a", encoding="utf-8") as handle:
            try:
                child = subprocess.Popen(command, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, creationflags=flags | (0x01000000 if flags else 0))
            except OSError:  # the job does not allow breaking away
                child = subprocess.Popen(command, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, creationflags=flags)
        record.update(pid=child.pid)
        write_record(record)
    return {"started": True, **record}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("ensure", help="start the fallback run if today needs it")
    one.add_argument("--trigger", choices=("nightly", "boot"), required=True)
    one.add_argument("--wait", action="store_true", help="run in this process until it ends")
    sub.add_parser("run", help="run the recorded fallback (started by ensure)")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    if args.command == "ensure":
        result = ensure(args.trigger, args.wait)
        print(("[FALLBACK] started " + result["run_id"] + ": " + result["reason"]) if result["started"]
              else "[FALLBACK] not started: " + result["reason"])
        return 0
    if args.command == "run":
        record = load(RECORD)
        if not record or record.get("status") not in {"started", "running"}:
            print("[FALLBACK] no fallback run waiting")
            return 1
        return run_pipeline(record)
    print(json.dumps(load(RECORD), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
