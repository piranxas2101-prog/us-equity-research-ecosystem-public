"""Research-only daily pipeline runner: a Python alternative to run_daily_dual_watchlist.ps1.

It runs the same scripts, with the same arguments and in the same order, as the
PowerShell orchestrator's research flow, and adds what that script lacks:

- ``plan`` shows every step without running anything;
- a state file per run (``output/pipeline_runs/<run_id>/pipeline_state.json``)
  records each step's status, duration, exit code, log and output hashes;
- ``--resume <run_id>`` reuses the steps that succeeded, retries the ones that
  failed (critical or warn-only), and refuses if reused outputs changed since;
- a lock stops two runs from overwriting each other's ``*_latest`` files.

It never submits paper or live orders: there is no trader-handoff step here.
Paper trading stays with the existing, explicit PowerShell launcher.

The PowerShell script remains the default. This runner exists so it can be
tried side by side; ``tests/test_daily_pipeline.py`` fails if the two drift.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from common import load_json

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
LOGS = ROOT / "logs"
# Runner state lives apart from output/runs/<run_id>/: run_manifest.py must
# create that folder itself (it refuses an existing one, so an archived run can
# never be written into twice).
STATE_DIR = OUTPUT / "pipeline_runs"
LOCK_PATH = OUTPUT / "daily_pipeline.lock"
STATE_SCHEMA = "ecosystem.daily_pipeline_state.v1"
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
TRADER_HANDOFF_STATUS = "research_only_default"
TRADER_HANDOFF_NOTE = ("Paper submission skipped. Research-only is the safe default; "
                       "use -SubmitPaper for an explicit paper-execution request.")

BABIS = "output/babis_shortlist_latest.json"
GIANNIS = "output/giannis_rule_based_latest.json"
PETROS = "output/petros_review_latest.json"
YANKOS = "output/yankos_strategy_latest.json"
PERIKLIS = "output/periklis_strategy_latest.json"
PACKET = "output/fundamental_research_packet_latest.json"
DEEP = "output/company_deep_research_latest.json"
BACKTEST = "output/strategy_backtest_latest.json"


@dataclass(frozen=True)
class Options:
    run_id: str
    full_market: bool = False
    full_market_limit: int = 0
    locked_watchlist: str = "output/watchlist_300_locked.json"
    rebuild_lock: bool = False
    fundamental_control_plane_limit: int = 5
    meeting_duration_minutes: int = 0
    python: str = sys.executable


@dataclass
class Context:
    options: Options
    timestamp: str
    warnings: list[str] = field(default_factory=list)
    baseline: dict[str, str | None] = field(default_factory=dict)
    dry_run: bool = False  # ``plan``: build commands without writing anything

    @property
    def baseline_dir(self) -> str:
        return f"output/baseline_compare_{self.timestamp}"

    @property
    def summary_path(self) -> str:
        return f"output/dual_watchlist_summary_{self.timestamp}.md"

    @property
    def handoff_log(self) -> str:
        return f"logs/dual_watchlist_trader_handoff_{self.timestamp}.log"


@dataclass(frozen=True)
class Stage:
    name: str
    critical: bool
    command: Callable[[Context], list[str]] | None = None
    action: Callable[[Context], None] | None = None
    skip_reason: Callable[[Context], str | None] | None = None
    outputs: tuple[str, ...] = ()


class PipelineFailed(RuntimeError):
    pass


class ResumeConflict(RuntimeError):
    pass


# ---------------------------------------------------------------- stage definitions


def _py(ctx: Context, *args: str) -> list[str]:
    return [ctx.options.python, *args]


def _backup_baseline(ctx: Context) -> None:
    """Copy the previous *_latest outputs aside so the compare step can diff them."""
    for key, path in (("babis", BABIS), ("giannis", GIANNIS), ("yankos", YANKOS),
                      ("periklis", PERIKLIS), ("petros", PETROS)):
        source = ROOT / path
        if source.exists():
            target = ROOT / ctx.baseline_dir / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            ctx.baseline[key] = f"{ctx.baseline_dir}/{source.name}"
        else:
            ctx.baseline[key] = None


def locked_watchlist_tickers(ctx: Context) -> list[str]:
    """Same rules as Build-/Load-LockedWatchlist in the PowerShell script."""
    path = ROOT / ctx.options.locked_watchlist
    if ctx.options.rebuild_lock or not path.exists():
        if ctx.dry_run:
            raise PipelineFailed(f"locked watchlist will be built from {BABIS} when the run starts")
        latest = load_json(ROOT / BABIS)
        tickers: list[str] = []
        for row in (latest.get("shortlist") or [])[:300]:
            ticker = str(row.get("ticker") or "").strip()
            if ticker and ticker not in tickers:
                tickers.append(ticker)
        if not tickers:
            raise PipelineFailed(f"Could not build locked watchlist tickers from {BABIS}.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"run_timestamp": datetime.now().astimezone().isoformat(),
                                    "source_shortlist": str(ROOT / BABIS), "count": len(tickers),
                                    "tickers": tickers}, indent=2), encoding="utf-8")
    tickers = [str(item) for item in load_json(path).get("tickers") or []]
    if not tickers:
        raise PipelineFailed(f"Locked watchlist has zero tickers: {path}")
    return tickers


def _babis(ctx: Context) -> list[str]:
    common_args = ["--stage2-limit", "0", "--stage2-llm-limit", "0", "--no-stage1-llm", "--no-stage2-llm"]
    if ctx.options.full_market:
        limit = ctx.options.full_market_limit if ctx.options.full_market_limit > 0 else 2147483647
        return _py(ctx, "us_equity_monitor.py", "--config", "scanner_config.json", "pipeline", "--full-universe",
                   "--stage1-limit", str(limit), *common_args)
    tickers = locked_watchlist_tickers(ctx)
    return _py(ctx, "us_equity_monitor.py", "--config", "scanner_config.json", "pipeline",
               "--stage1-limit", str(len(tickers)), *common_args, "--tickers", *tickers)


def _compare(ctx: Context) -> list[str]:
    b = ctx.baseline
    if not b:
        raise PipelineFailed("built from the previous outputs saved by the baseline_backup step")
    args = ["compare_agent_runs.py",
            "--old-babis", b["babis"], "--new-babis", BABIS,
            "--old-giannis", b["giannis"], "--new-giannis", GIANNIS,
            "--old-yankos", b["yankos"], "--new-yankos", YANKOS,
            "--old-petros", b["petros"], "--new-petros", PETROS,
            "--baseline-label", "previous_latest_before_refresh",
            "--current-label", "fresh_scan_with_current_logic",
            "--out-json", "output/scan_compare_latest.json", "--out-md", "output/scan_compare_latest.md"]
    if b.get("periklis") and (ROOT / PERIKLIS).exists():
        args += ["--old-periklis", b["periklis"], "--new-periklis", PERIKLIS]
    return _py(ctx, *[str(item) for item in args])


def _no_baseline(ctx: Context) -> str | None:
    missing = [key for key in ("babis", "giannis", "yankos", "petros") if not ctx.baseline.get(key)]
    return "No complete baseline available for compare step." if missing else None


def _meeting(ctx: Context) -> list[str]:
    return [POWERSHELL, "-ExecutionPolicy", "Bypass", "-File", ".\\run_babis_giannis_meeting.ps1",
            "-DurationMinutes", str(ctx.options.meeting_duration_minutes),
            "-BabisFile", ".\\" + BABIS.replace("/", "\\"), "-GiannisFile", ".\\" + GIANNIS.replace("/", "\\"),
            "-YankosFile", ".\\" + YANKOS.replace("/", "\\"), "-PeriklisFile", ".\\" + PERIKLIS.replace("/", "\\"),
            "-PetrosFile", ".\\" + PETROS.replace("/", "\\"), "-CompareFile", ".\\output\\scan_compare_latest.json",
            "-OutputDir", ".\\output", "-LogDir", ".\\logs"]


def _write_summary(ctx: Context) -> None:
    """Summary, finished marker and handoff log, as the PowerShell script writes them."""
    babis = load_json(ROOT / BABIS) if (ROOT / BABIS).exists() else {}
    warnings = ctx.warnings
    label = "none" if not warnings else f"{len(warnings)} warning(s)"
    mode = "full-market" if ctx.options.full_market else ctx.options.locked_watchlist
    lines = ["# Daily Dual Watchlist Summary", "",
             f"- Run timestamp: {datetime.now().astimezone().isoformat()}", f"- Run ID: {ctx.options.run_id}",
             f"- Watchlist mode: {mode}", f"- Symbol count: {babis.get('universe_total', 'unknown')}",
             f"- Shortlist count: {babis.get('shortlist_total', 'unknown')}", f"- Pipeline warnings: {label}",
             "- Runner: daily_pipeline.py (research only)",
             f"- Trader handoff status: {TRADER_HANDOFF_STATUS}",
             f"- Stage state: output/pipeline_runs/{ctx.options.run_id}/pipeline_state.json",
             "- Action report: output/today_action_report.md"]
    if warnings:
        lines += ["", "## Pipeline Warnings", ""] + [f"- {warning}" for warning in warnings]
    (ROOT / ctx.summary_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    (ROOT / "output" / "RUN_FINISHED_READ_ME.txt").write_text(
        "\n".join(["Research-only daily run completed (daily_pipeline.py).",
                   f"Completed at: {datetime.now().astimezone().isoformat()}", f"Mode: {mode}",
                   f"Warnings: {label}", f"Summary: {ROOT / ctx.summary_path}"]) + "\n", encoding="utf-8")
    handoff = ROOT / ctx.handoff_log
    handoff.parent.mkdir(parents=True, exist_ok=True)
    handoff.write_text(TRADER_HANDOFF_NOTE + "\n", encoding="utf-8")


def _coordination(ctx: Context) -> list[str]:
    args = ["ecosystem_coordination.py", "--run-id", ctx.options.run_id,
            "--generated-at", datetime.now().astimezone().isoformat(),
            "--trader-handoff-status", TRADER_HANDOFF_STATUS, "--summary-path", str(ROOT / ctx.summary_path)]
    for warning in ctx.warnings:
        args += ["--warning", warning]
    return _py(ctx, *args)


def _manifest(ctx: Context) -> list[str]:
    args = ["run_manifest.py", "--run-id", ctx.options.run_id]
    for config in ("scanner_config.json", "universe_eligibility_overrides.json", "trading_window_config.json"):
        args += ["--config", config]
    artifacts = [BABIS, "output/universe_eligibility_latest.json", "output/all_listed_universe_routes_latest.json",
                 "output/all_listed_instrument_observations_latest.json", GIANNIS, DEEP,
                 "output/information_ledger_latest.json", PACKET, "output/fundamental_control_plane_batch_latest.json",
                 YANKOS, PERIKLIS, "output/experiment_registry_latest.json", BACKTEST,
                 "output/macro_environment_latest.json", "output/periodic_results_latest.json", "output/earnings_journal_latest.json", "output/paper_learning/evaluation_latest.json", "output/sokratis_strategy_teacher_latest.json", PETROS, "output/petros_training_latest.md",
                 "output/team_decision_latest.json", "output/today_action_report.md", ctx.summary_path, ctx.handoff_log]
    audit = ROOT / "output/research_only_audit_latest.json"
    if ctx.options.full_market and audit.exists() and load_json(audit).get("run_id") == ctx.options.run_id:
        artifacts.append("output/research_only_audit_latest.json")
    outbox = f"output/integration_outbox/{ctx.options.run_id}_daily_research_digest.json"
    if (ROOT / outbox).exists():
        artifacts.append(outbox)
    for artifact in artifacts:
        args += ["--artifact", artifact]
    return _py(ctx, *args)


def _check_audit(ctx: Context) -> None:
    audit = ROOT / "output/research_only_audit_latest.json"
    if audit.exists():
        payload = load_json(audit)
        if payload.get("run_id") != ctx.options.run_id or payload.get("status") != "pass":
            ctx.warnings.append("Research-only agent audit requires review.")


def build_stages() -> list[Stage]:
    theory = "ecosystem_theory_contract.json"
    return [
        Stage("gate_unknown_data", True, lambda c: _py(c, "unknown_data.py", "verify", "--contract", "unknown_data_contract.json")),
        Stage("gate_personality", True, lambda c: _py(c, "agent_personality.py", "verify", "--registry",
                                                      "agent_personality_registry.json", "--contract", theory)),
        Stage("gate_methodology", True, lambda c: _py(c, "agent_methodology.py", "verify", "--registry",
                                                      "agent_methodology_registry.json", "--contract", theory)),
        Stage("gate_assimilation", True, lambda c: _py(c, "agent_assimilation.py", "verify", "--contract", theory,
                                                       "--reports-dir", "docs/agent_reports")),
        Stage("gate_personality_behavior", True, lambda c: _py(c, "agent_personality_behavior.py", "--output-dir",
                                                               "output/personality_behavior_assimilation_gate")),
        Stage("gate_playbook_assimilation", True, lambda c: _py(c, "trader_playbook_assimilation.py", "--output-dir",
                                                                "output/playbook_assimilation_gate")),
        Stage("baseline_backup", False, action=_backup_baseline),
        # SEC companyfacts for every scanned company (owner decision 2026-10-12); never stops the run
        Stage("sec_prefetch", False, lambda c: _py(c, "companyfacts_prefetch.py"),
              skip_reason=lambda c: None if c.options.full_market else "Prefetch runs only for full-market runs."),
        Stage("babis", True, _babis, outputs=(BABIS,)),
        Stage("giannis", True, lambda c: _py(c, "giannis_rule_based_from_shortlist.py", "--shortlist", BABIS,
                                             "--out-prefix", "output/giannis_rule_based"), outputs=(GIANNIS,)),
        Stage("information_ledger", True, lambda c: _py(c, "information_ledger.py", "--babis", BABIS, "--giannis", GIANNIS,
                                                        "--out", "output/information_ledger_latest.json"),
              outputs=("output/information_ledger_latest.json",)),
        Stage("control_plane_batch", False, lambda c: _py(
            c, "fundamental_control_plane_batch.py", "--babis", BABIS, "--run-id", c.options.run_id,
            "--limit", str(c.options.fundamental_control_plane_limit),
            "--output", "output/fundamental_control_plane_batch_latest.json"),
            outputs=("output/fundamental_control_plane_batch_latest.json",)),
        Stage("petros", True, lambda c: _py(c, "petros_agent.py", "review", "--babis", BABIS, "--giannis", GIANNIS,
                                            "--out-prefix", "output/petros_review", "--no-model"), outputs=(PETROS,)),
        Stage("deep_research", True, lambda c: _py(c, "company_deep_research.py", "--petros", PETROS, "--output", DEEP,
                                                   "--markdown", "output/company_deep_research_latest.md"), outputs=(DEEP,)),
        Stage("fundamental_packet", True, lambda c: _py(c, "fundamental_research_packet.py", "--babis", BABIS,
                                                        "--giannis", GIANNIS, "--petros", PETROS, "--deep-research", DEEP,
                                                        "--out-prefix", "output/fundamental_research_packet"),
              outputs=(PACKET,)),
        Stage("price_history", False, lambda c: _py(c, "price_history_store.py", "--shortlist", BABIS)),
        Stage("yankos", True, lambda c: _py(c, "yankos_strategy_agent.py", "review", "--babis", BABIS, "--giannis", GIANNIS,
                                            "--fundamental-packet", PACKET, "--out-prefix", "output/yankos_strategy",
                                            "--agent-name", "yankos"), outputs=(YANKOS,)),
        Stage("periklis", True, lambda c: _py(c, "periklis_strategy_agent.py", "review", "--babis", BABIS,
                                              "--giannis", GIANNIS, "--fundamental-packet", PACKET,
                                              "--out-prefix", "output/periklis_strategy", "--agent-name", "periklis"),
              outputs=(PERIKLIS,)),
        Stage("experiment_registry", True, lambda c: _py(c, "experiment_registry.py", "--giannis", GIANNIS,
                                                         "--strategy", YANKOS, "--strategy", PERIKLIS,
                                                         "--horizon-days", "20", "--out-prefix", "output/experiment_registry"),
              outputs=("output/experiment_registry_latest.json",)),
        Stage("outcome_record", False, lambda c: _py(c, "outcome_tracker.py", "record", "--giannis", GIANNIS)),
        Stage("outcome_refresh", False, lambda c: _py(c, "outcome_tracker.py", "refresh")),
        Stage("strategy_backtest", False, lambda c: _py(c, "strategy_backtest.py", "build", "--out-prefix",
                                                        "output/strategy_backtest"), outputs=(BACKTEST,)),
        Stage("macro_environment", False, lambda c: _py(c, "macro_environment.py", "--update-store"),
              outputs=("output/macro_environment_latest.json",)),
        Stage("periodic_results", False, lambda c: _py(c, "periodic_results.py"),
              outputs=("output/periodic_results_latest.json",)),
        Stage("press_release_reader", False, lambda c: _py(c, "press_release_reader.py")),
        Stage("earnings_journal", False, lambda c: _py(c, "earnings_journal.py"),
              outputs=("output/earnings_journal_latest.json",)),
        Stage("sokratis", False, lambda c: _py(c, "sokratis_strategy_teacher.py", "build", "--backtest", BACKTEST,
                                               "--yankos", YANKOS, "--periklis", PERIKLIS,
                                               "--out-prefix", "output/sokratis_strategy_teacher"),
              outputs=("output/sokratis_strategy_teacher_latest.json",)),
        Stage("petros_train", False, lambda c: _py(c, "petros_trainer.py", "--horizon-days", "20", "train")),
        Stage("compare", False, _compare, skip_reason=_no_baseline),
        Stage("meeting", False, _meeting),
        Stage("action_report", False, lambda c: _py(
            c, "today_action_report.py", "--babis", BABIS, "--giannis", GIANNIS,
            "--delta", "output/babis_shortlist_delta_latest.json", "--yankos", YANKOS, "--periklis", PERIKLIS,
            "--strategy-backtest", BACKTEST, "--team-decision", "output/team_decision_latest.json",
            "--output", "output/today_action_report.md"), outputs=("output/today_action_report.md",)),
        Stage("summary", False, action=_write_summary),
        Stage("coordination", False, _coordination),
        Stage("research_audit", False, lambda c: _py(c, "agent_run_audit.py", "--phase", "research_only",
                                                     "--run-id", c.options.run_id),
              skip_reason=lambda c: None if c.options.full_market else "Audit runs only for full-market runs."),
        Stage("research_audit_check", False, action=_check_audit,
              skip_reason=lambda c: None if c.options.full_market else "Audit runs only for full-market runs."),
        Stage("manifest", False, _manifest),
    ]


# ---------------------------------------------------------------- execution


def file_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def execute(argv: list[str], log_path: Path, env: dict[str, str]) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        return subprocess.run(argv, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=False).returncode


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def run_pipeline(stages: list[Stage], ctx: Context, *, state_path: Path,
                 executor: Callable[[list[str], Path, dict[str, str]], int] = execute,
                 previous: dict[str, Any] | None = None, root: Path = ROOT) -> dict[str, Any]:
    """Run stages in order, saving state after each one.

    With ``previous`` (resume), stages that finished in that run are reused as
    long as their outputs still hash the same; otherwise the resume is refused.
    """
    env = {**os.environ, "ECOSYSTEM_RUN_ID": ctx.options.run_id}
    # Steps that succeeded (or were legitimately skipped) are reused on resume;
    # failed steps, including warn-only ones, run again.
    done = {item["name"]: item for item in (previous or {}).get("stages", [])
            if item.get("status") in {"completed", "skipped"}}
    if previous:
        ctx.baseline.update(previous.get("baseline") or {})
        for item in previous.get("stages", []):
            if item["name"] in done:
                ctx.warnings.extend(item.get("warnings") or [])
    state: dict[str, Any] = {"schema": STATE_SCHEMA, "run_id": ctx.options.run_id, "timestamp": ctx.timestamp,
                             "research_only": True, "paper_submission": False, "status": "running",
                             "started_at": (previous or {}).get("started_at") or _now(), "stages": [],
                             "warnings": ctx.warnings, "baseline": ctx.baseline}
    _save_state(state_path, state)
    for stage in stages:
        if stage.name in done:
            recorded = done[stage.name]
            for path, digest in (recorded.get("outputs") or {}).items():
                if file_sha256(root / path) != digest:
                    raise ResumeConflict(f"{path} changed since stage '{stage.name}' finished in run "
                                         f"{ctx.options.run_id}; another run may have overwritten it. Start a new run.")
            state["stages"].append({**recorded, "reused_on_resume": True})
            continue
        entry: dict[str, Any] = {"name": stage.name, "critical": stage.critical, "started_at": _now()}
        warnings_before = len(ctx.warnings)
        reason = stage.skip_reason(ctx) if stage.skip_reason else None
        started = time.monotonic()
        if reason:
            entry.update(status="skipped", reason=reason)
        else:
            try:
                if stage.action:
                    stage.action(ctx)
                    code = 0
                else:
                    assert stage.command is not None
                    log = LOGS / f"daily_pipeline_{ctx.options.run_id}_{stage.name}.log"
                    entry["log"] = str(log)
                    code = executor(stage.command(ctx), log, env)
            except (OSError, ValueError, KeyError, PipelineFailed) as exc:
                code = 1
                entry["error"] = str(exc)[:500]
            entry["exit_code"] = code
            if code == 0:
                entry["status"] = "completed"
            elif stage.critical:
                entry["status"] = "failed"
            else:
                entry["status"] = "warning"
                ctx.warnings.append(f"Stage {stage.name} failed with exit code {code}. Log: {entry.get('log', '-')}")
        entry["seconds"] = round(time.monotonic() - started, 1)
        entry["finished_at"] = _now()
        entry["warnings"] = ctx.warnings[warnings_before:]
        entry["outputs"] = {path: file_sha256(root / path) for path in stage.outputs if (root / path).exists()}
        state["stages"].append(entry)
        print(f"[{entry['status']:>9}] {stage.name} ({entry['seconds']}s)", flush=True)
        if entry["status"] == "failed":
            state.update(status="failed", failed_stage=stage.name, finished_at=_now())
            _save_state(state_path, state)
            raise PipelineFailed(f"Critical stage '{stage.name}' failed. Later stages were not run. "
                                 f"Fix it, then continue with: daily_pipeline.py run --resume {ctx.options.run_id}")
        _save_state(state_path, state)
    state.update(status="completed_with_warnings" if ctx.warnings else "completed", finished_at=_now())
    _save_state(state_path, state)
    return state


class RunLock:
    """Exclusive OS-level lock; released automatically if the process dies."""

    def __init__(self, path: Path = LOCK_PATH) -> None:
        self.path = path
        self.handle: Any = None

    def __enter__(self) -> RunLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+")
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise PipelineFailed("Another daily_pipeline run is active (lock held). Wait for it to finish.") from exc
        return self

    def __exit__(self, *exc: object) -> None:
        if self.handle:
            if os.name == "nt":
                import msvcrt
                try:
                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            self.handle.close()


# ---------------------------------------------------------------- CLI


def _options(args: argparse.Namespace, run_id: str) -> Options:
    return Options(run_id=run_id, full_market=args.full_market, full_market_limit=args.full_market_limit,
                   locked_watchlist=args.locked_watchlist, rebuild_lock=args.rebuild_lock,
                   fundamental_control_plane_limit=args.fundamental_control_plane_limit,
                   meeting_duration_minutes=args.meeting_duration_minutes)


def describe(stages: list[Stage], ctx: Context) -> list[str]:
    lines = []
    for number, stage in enumerate(stages, start=1):
        kind = "critical" if stage.critical else "warn-only"
        if stage.action:
            detail = "(internal step)"
        else:
            try:
                argv = stage.command(ctx) if stage.command else []
                shown = [Path(argv[0]).name, *argv[1:]] if argv else []
                detail = " ".join(shown[:14]) + (" ..." if len(shown) > 14 else "")
            except (OSError, ValueError, KeyError, PipelineFailed) as exc:
                detail = f"(cannot build command yet: {exc})"
        lines.append(f"{number:>2}. {stage.name:<27} {kind:<9} {detail}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Research-only daily pipeline (Python alternative to the PowerShell runner).")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--full-market", action="store_true")
        cmd.add_argument("--full-market-limit", type=int, default=0)
        cmd.add_argument("--locked-watchlist", default="output/watchlist_300_locked.json")
        cmd.add_argument("--rebuild-lock", action="store_true")
        cmd.add_argument("--fundamental-control-plane-limit", type=int, default=5)
        cmd.add_argument("--meeting-duration-minutes", type=int, default=0)
        cmd.add_argument("--run-id", default="")
        if name == "run":
            cmd.add_argument("--resume", default="", help="Continue a failed run by its run id.")
    status_cmd = sub.add_parser("status")
    status_cmd.add_argument("run_id")
    args = parser.parse_args(argv)
    os.chdir(ROOT)

    if args.command == "status":
        state = load_json(STATE_DIR / args.run_id / "pipeline_state.json")
        print(f"{state['run_id']}: {state['status']}")
        for item in state["stages"]:
            print(f"  {item['status']:>9}  {item['name']:<27} {item.get('seconds', '')}s {item.get('reason', '')}")
        return 0

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    resume_id = getattr(args, "resume", "")
    run_id = resume_id or args.run_id or f"run_{timestamp}"
    state_path = STATE_DIR / run_id / "pipeline_state.json"
    previous = load_json(state_path) if resume_id else None
    if previous:
        timestamp = previous.get("timestamp") or timestamp
    ctx = Context(options=_options(args, run_id), timestamp=timestamp)
    stages = build_stages()

    if args.command == "plan":
        ctx.dry_run = True
        print(f"Plan for {run_id} (research only, nothing is run):")
        print("\n".join(describe(stages, ctx)))
        return 0
    try:
        with RunLock():
            state = run_pipeline(stages, ctx, state_path=state_path, previous=previous)
    except (PipelineFailed, ResumeConflict) as exc:
        print(f"[STOPPED] {exc}")
        return 1
    print(f"{run_id}: {state['status']} ({len(ctx.warnings)} warning(s)). State: {state_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
