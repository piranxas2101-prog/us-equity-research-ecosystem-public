"""Κέντρο Ελέγχου: a local control center for the research ecosystem.

Shows what the agents did (runs, symbols, sectors, agents, classification review)
and allows exactly two actions: an owner decision on a classification review item
(through Babis's governed, reversible batch) and starting a research-only full run.
There is no endpoint that reaches Alpaca, a trader launcher or any shell command.

Security model (the machine must not become reachable through this page):
- listens on 127.0.0.1 only; requests whose Host header is not this server are refused
  (DNS-rebinding protection);
- every action needs the per-start session token, a same-origin Origin header and a JSON
  body, so another website open in the browser cannot trigger one (CSRF protection);
- databases are opened read-only for every view; actions are appended to an audit log.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sqlite3
import subprocess
import threading
import time
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
import webbrowser

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "dashboard_assets" / "control_center"
HOST = "127.0.0.1"
DEFAULT_PORT = 8770
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}
TERMINAL_RUN_STATES = {"completed", "completed_review_required", "failed", "stopped_by_owner", "interrupted"}
MAX_BODY = 16 * 1024
LAUNCH_FLAGS = 0x00000200 | 0x08000000  # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _json_file(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def process_alive(pid: Any) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    kernel = getattr(ctypes, "windll", None)
    if kernel is None:  # non-Windows (tests on CI runners)
        try:
            import os
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    handle = kernel.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        kernel.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel.kernel32.CloseHandle(handle)


# ---------------------------------------------------------------- views


class Views:
    """Read-only data for the page. Heavy run data is cached per manifest."""

    def __init__(self, root: Path = ROOT):
        self.root = root
        self._lock = threading.Lock()
        self._run_cache: dict[str, Any] = {}
        self._taxonomy: tuple[dict, dict] | None = None
        self._lanes_cache: tuple[int, dict[str, Any]] | None = None

    # -- helpers
    def taxonomy(self) -> tuple[dict, dict]:
        if self._taxonomy is None:
            from research_registry.core import taxonomy
            self._taxonomy = taxonomy()
        return self._taxonomy

    def label(self, node: str | None) -> str | None:
        if not node:
            return None
        info = self.taxonomy()[1].get(node)
        return info[5] if info else node

    def runs(self) -> list[dict[str, Any]]:
        rows = []
        for manifest in (self.root / "output" / "runs").glob("*/manifest.json"):
            payload = _json_file(manifest) or {}
            rows.append({"run_id": manifest.parent.name, "captured_at": payload.get("captured_at"),
                         "artifacts": len(payload.get("artifacts") or []),
                         "full_market": manifest.parent.name.startswith("full_research_")})
        return sorted(rows, key=lambda row: str(row["captured_at"] or ""), reverse=True)

    def _default_run(self) -> str | None:
        runs = self.runs()
        full = [row for row in runs if row["full_market"]]
        return (full or runs or [{}])[0].get("run_id")

    def run_records(self, run_id: str | None) -> dict[str, Any]:
        """Verified per-symbol records of one archived run (hash-checked by the loader)."""
        from symbol_research_dashboard import LocalPacketReader, build_symbol_records, load_run

        run_id = run_id or self._default_run()
        if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id:
            raise ValueError("Unknown run")
        manifest = self.root / "output" / "runs" / run_id / "manifest.json"
        if not manifest.is_file():
            raise ValueError("Unknown run")
        key = f"{run_id}:{manifest.stat().st_mtime_ns}"
        with self._lock:
            if key in self._run_cache:
                return self._run_cache[key]
            _, artifacts, verified = load_run(manifest)
            reader = LocalPacketReader(self.root / "data" / "market_intel.db", run_id)
            try:
                records = build_symbol_records(artifacts, reader)
            finally:
                reader.close()
            data = {"run_id": run_id, "verified_artifacts": verified, "records": {row["ticker"]: row for row in records},
                    "audit_status": (artifacts.get("audit") or {}).get("status"),
                    "coverage": {"universe_total": artifacts["babis"].get("universe_total"),
                                 "routing": artifacts["babis"].get("universe_routing")}}
            self._run_cache = {key: data}  # keep one run in memory
            return data

    def registry_nodes(self) -> dict[str, dict[str, Any]]:
        path = self.root / "data" / "research_registry.db"
        if not path.exists():
            return {}
        conn = read_only(path)
        try:
            snapshot = conn.execute("SELECT snapshot_id FROM universe_snapshot ORDER BY retrieved_at DESC, rowid DESC LIMIT 1").fetchone()
            if not snapshot:
                return {}
            rows = conn.execute("SELECT ticker, venue_code, instrument_type, node_id, status FROM effective_listed_symbol "
                                "WHERE snapshot_id=?", (snapshot[0],)).fetchall()
            review = set()
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='classification_review_item'").fetchone():
                review = {row[0] for row in conn.execute("SELECT ticker FROM classification_review_item WHERE status='open'")}
        finally:
            conn.close()
        return {row["ticker"]: {"venue_code": row["venue_code"], "instrument_type": row["instrument_type"],
                                "node_id": row["node_id"], "status": row["status"], "in_review": row["ticker"] in review}
                for row in rows}

    # -- pages
    def status(self) -> dict[str, Any]:
        state = _json_file(self.root / "output" / "full_research_control_plane_background_latest.json") or {}
        alive = process_alive(state.get("pid")) if state.get("status") not in TERMINAL_RUN_STATES else False
        progress = self._progress(state) if state else None
        return {"generated_at": now(), "run": {**{k: state.get(k) for k in (
                    "run_id", "status", "started_at", "finished_at", "processed_symbols", "technical_errors",
                    "receipt_status", "agent_audit_status", "learning_status", "error")},
                    "process_alive": alive,
                    "stalled": bool(state) and state.get("status") not in TERMINAL_RUN_STATES and not alive},
                "progress": progress, "steps": self._steps(state), "runs": self.runs()[:12],
                "review_open": self._review_open_count()}

    def _progress(self, state: dict[str, Any]) -> dict[str, Any] | None:
        logs = sorted((self.root / "logs").glob("full_research_control_plane_*.out.log"), key=lambda p: p.stat().st_mtime)
        if not logs:
            return None
        log = logs[-1]
        raw = log.read_bytes()
        text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        scanned = sum(1 for line in lines if line.startswith(("[OK]", "[SKIP]", "[OBSERVE]", "[COVERAGE]", "[ERROR]", "[FAIL]")))
        deferred = sum(1 for line in lines if line.startswith("[DEFER]"))
        lanes = self.lanes()
        total = lanes["total"] or None
        to_scan = lanes["to_scan"] if total else None
        eta_minutes = None
        started = state.get("started_at")
        if to_scan and scanned and started and state.get("status") not in TERMINAL_RUN_STATES:
            try:
                elapsed = time.time() - datetime.fromisoformat(started).timestamp()
                if elapsed > 0 and scanned < to_scan:
                    eta_minutes = round((to_scan - scanned) / (scanned / elapsed) / 60)
            except ValueError:
                pass
        return {"log": log.name, "log_updated_at": datetime.fromtimestamp(log.stat().st_mtime, timezone.utc).isoformat(),
                "babis_processed": scanned + deferred, "babis_scanned": scanned, "babis_deferred": deferred,
                "universe_total": total, "babis_to_scan": to_scan, "lanes": lanes["counts"],
                "babis_eta_minutes": eta_minutes, "tail": [line[:200] for line in lines[-12:]]}

    def _steps(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """Per-step timing of the current run, written by the pipeline itself at every step boundary."""
        timings = _json_file(self.root / "output" / "pipeline_step_timings_latest.json")
        if not timings or not state.get("run_id") or timings.get("run_id") != state.get("run_id"):
            return None
        return {"current_step": timings.get("current_step"), "updated_at": timings.get("updated_at"),
                "steps": [{k: step.get(k) for k in ("key", "agent", "started_at", "finished_at", "seconds")}
                          for step in timings.get("steps") or [] if isinstance(step, dict)]}

    def reports(self, run_id: str | None) -> dict[str, Any]:
        """Daily agent reports (written by daily_agent_report.py at the end of every run)."""
        folder = self.root / "output" / "daily_reports"
        names = sorted((p.stem for p in folder.glob("*.md") if p.stem != "latest"),
                       key=lambda name: (folder / f"{name}.md").stat().st_mtime, reverse=True) if folder.is_dir() else []
        chosen = run_id if run_id in names else (names[0] if names else None)
        text = (folder / f"{chosen}.md").read_text(encoding="utf-8") if chosen else None
        return {"reports": names[:60], "run_id": chosen, "markdown": text}

    def lanes(self) -> dict[str, Any]:
        """Where each routed symbol goes: scanned by Babis, on its issuer's card, or in a separate lane."""
        path = self.root / "output" / "all_listed_universe_routes_latest.json"
        try:
            key = path.stat().st_mtime_ns
        except OSError:
            return {"total": 0, "to_scan": 0, "counts": {}, "by_ticker": {}}
        with self._lock:
            cached = self._lanes_cache
            if cached and cached[0] == key:
                return cached[1]
        rows = (_json_file(path) or {}).get("routes") or []
        by_ticker = {}
        for row in rows:
            if row.get("defer_market_scan"):
                by_ticker[str(row.get("ticker"))] = {"lane": row.get("separate_lane") or "issuer_card",
                                                     "issuer_ticker": row.get("issuer_ticker")}
        counts: dict[str, int] = {}
        for item in by_ticker.values():
            counts[item["lane"]] = counts.get(item["lane"], 0) + 1
        result = {"total": len(rows), "to_scan": len(rows) - len(by_ticker), "counts": counts, "by_ticker": by_ticker}
        with self._lock:
            self._lanes_cache = (key, result)
        return result

    def _review_open_count(self) -> int | None:
        path = self.root / "data" / "research_registry.db"
        if not path.exists():
            return None
        conn = read_only(path)
        try:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='classification_review_item'").fetchone():
                return 0
            return conn.execute("SELECT COUNT(*) FROM classification_review_item WHERE status='open'").fetchone()[0]
        finally:
            conn.close()

    def symbols(self, run_id: str | None) -> dict[str, Any]:
        run = self.run_records(run_id)
        registry = self.registry_nodes()
        rows = []
        for ticker, rec in run["records"].items():
            reg = registry.get(ticker) or {}
            node = reg.get("node_id")
            sector = node.split(".")[0] if node else None
            trader = lambda items: next((i.get("signal_verdict") for i in items if i), None)  # noqa: E731
            rows.append({
                "ticker": ticker, "company": rec.get("company"),
                "sector": sector, "sector_label": self.label(sector), "node_id": node, "node_label": self.label(node),
                "classification_status": reg.get("status"), "in_review": reg.get("in_review", False),
                "rank": rec["babis"].get("rank"), "attention": rec["babis"].get("fundamental_attention_score"),
                "giannis_tier": rec["giannis"].get("giannis_tier"), "giannis_score": rec["giannis"].get("score_final"),
                "risk": rec["giannis"].get("risk_level"),
                "petros_verdict": rec["petros"].get("petros_verdict"), "petros_score": rec["petros"].get("petros_score"),
                "yankos": trader(rec["other_agents"].get("yankos") or []),
                "periklis": trader(rec["other_agents"].get("periklis") or []),
            })
        return {"run_id": run["run_id"], "audit_status": run["audit_status"], "coverage": run["coverage"], "rows": rows}

    def symbol(self, ticker: str, run_id: str | None) -> dict[str, Any]:
        ticker = ticker.upper()
        run = self.run_records(run_id)
        record = run["records"].get(ticker)
        registry = self.registry_nodes().get(ticker) or {}
        node = registry.get("node_id")
        path = [node.rsplit(".", i)[0] for i in range(node.count("."), -1, -1)] if node else []
        return {"run_id": run["run_id"], "ticker": ticker, "record": record,
                "classification": {**registry, "path": [{"id": n, "label": self.label(n)} for n in path],
                                   "review": self._review_item(ticker)},
                "instruments": self._instrument_links(ticker),
                "lane": self._lane_with_mergers(ticker),
                "history": self._history(ticker)}

    def _lane_with_mergers(self, ticker: str) -> dict[str, Any] | None:
        lane = self.lanes()["by_ticker"].get(ticker)
        if lane and lane.get("lane") == "spac_lane":
            from datetime import date
            from spac_watch import merger_filings

            found = merger_filings([ticker], until=date.today(),
                                   sec_tickers=self.root / "cache" / "sec_company_tickers_exchange.json",
                                   registry_db=self.root / "data" / "research_registry.db")
            lane = {**lane, "merger_filings": found.get(ticker, [])}
        return lane

    def _instrument_links(self, ticker: str) -> dict[str, Any] | None:
        """Warrants/preferreds/units/rights of this company, or the company this instrument belongs to."""
        path = self.root / "data" / "research_registry.db"
        if not path.exists():
            return None
        from research_registry.instrument_links import links_for

        conn = read_only(path)
        try:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='instrument_parent_link'").fetchone():
                return None
            return links_for(conn, ticker)
        finally:
            conn.close()

    def _review_item(self, ticker: str) -> dict[str, Any] | None:
        path = self.root / "data" / "research_registry.db"
        if not path.exists():
            return None
        conn = read_only(path)
        try:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='classification_review_item'").fetchone():
                return None
            row = conn.execute("SELECT * FROM classification_review_item WHERE ticker=? ORDER BY status='open' DESC LIMIT 1",
                               (ticker,)).fetchone()
        finally:
            conn.close()
        return self._review_row(row) if row else None

    def _review_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["votes"] = json.loads(item.pop("votes_json") or "[]")
        item["advisory_votes"] = json.loads(item.pop("advisory_json") or "[]")
        for vote in item["votes"] + item["advisory_votes"]:
            vote["label"] = self.label(vote.get("node"))
        item["current_label"] = self.label(item.get("current_node"))
        return item

    def _history(self, ticker: str) -> dict[str, Any]:
        path = self.root / "data" / "market_intel.db"
        if not path.exists():
            return {}
        conn = read_only(path)
        try:
            setups = [dict(r) for r in conn.execute(
                "SELECT id, run_id, source_run_timestamp, observed_at, baseline_price, giannis_score, giannis_tier, risk_level "
                "FROM candidate_setups WHERE ticker=? ORDER BY observed_at DESC LIMIT 60", (ticker,))]
            outcomes = []
            if setups:
                marks = ",".join("?" for _ in setups)
                outcomes = [dict(r) for r in conn.execute(
                    f"SELECT setup_id, horizon_days, price_session_date, price, return_pct FROM price_outcomes "
                    f"WHERE setup_id IN ({marks}) ORDER BY price_session_date", [s["id"] for s in setups])]
            petros = [dict(r) for r in conn.execute(
                "SELECT source_run_timestamp, petros_score, petros_verdict, confidence FROM petros_decisions "
                "WHERE ticker=? ORDER BY source_run_timestamp DESC LIMIT 60", (ticker,))]
            signals = [dict(r) for r in conn.execute(
                "SELECT agent_name, run_id, source_run_timestamp, signal_verdict, trade_direction, signal_score "
                "FROM strategy_signals WHERE ticker=? ORDER BY source_run_timestamp DESC LIMIT 60", (ticker,))]
        except sqlite3.Error:
            return {}
        finally:
            conn.close()
        return {"setups": setups, "outcomes": outcomes, "petros": petros, "signals": signals}

    def sectors(self) -> dict[str, Any]:
        registry = self.registry_nodes()
        counts: dict[str, dict[str, int]] = {}
        for row in registry.values():
            if row["instrument_type"] not in ("equity_candidate", "adr_candidate"):
                continue
            node = row["node_id"]
            if not node:
                counts.setdefault("_unknown", {"total": 0, "approved": 0, "in_review": 0})["total"] += 1
                continue
            parts = node.split(".")
            for depth in range(1, len(parts) + 1):
                key = ".".join(parts[:depth])
                entry = counts.setdefault(key, {"total": 0, "approved": 0, "in_review": 0})
                entry["total"] += 1
                entry["approved"] += row["status"] == "approved_provisional"
                entry["in_review"] += bool(row["in_review"])
        tax_nodes = self.taxonomy()[1]
        tree = [{"id": node, "label": info[5], "parent": info[3], "level": info[4], **counts.get(node, {"total": 0, "approved": 0, "in_review": 0})}
                for node, info in sorted(tax_nodes.items())]
        return {"nodes": tree, "unknown": counts.get("_unknown", {}).get("total", 0)}

    def sector_members(self, node: str) -> dict[str, Any]:
        registry = self.registry_nodes()
        members = sorted(t for t, r in registry.items() if r["node_id"] and (r["node_id"] == node or r["node_id"].startswith(node + "."))
                         and r["instrument_type"] in ("equity_candidate", "adr_candidate"))
        return {"node": node, "label": self.label(node), "tickers": members}

    def agents(self) -> dict[str, Any]:
        result: dict[str, Any] = {"registered": [], "queues": [], "learning": [], "models": []}
        registry = self.root / "data" / "research_registry.db"
        if registry.exists():
            conn = read_only(registry)
            try:
                result["registered"] = [dict(r) for r in conn.execute(
                    "SELECT agent_id, agent_type, owned_sector, status, activated_at FROM ecosystem_agent_registration ORDER BY agent_type, agent_id")]
                result["queues"] = [dict(r) for r in conn.execute(
                    "SELECT agent_id, status, COUNT(*) AS items FROM sector_analysis_queue GROUP BY agent_id, status ORDER BY agent_id, items DESC")]
            except sqlite3.Error:
                pass
            finally:
                conn.close()
        intel = self.root / "data" / "market_intel.db"
        if intel.exists():
            conn = read_only(intel)
            try:
                result["learning"] = [dict(r) for r in conn.execute(
                    "SELECT e.agent_id, COUNT(*) AS episodes, COUNT(o.outcome_id) AS outcomes, MAX(e.recorded_at) AS last_recorded "
                    "FROM agent_learning_episodes e LEFT JOIN agent_learning_outcomes o ON o.episode_id=e.episode_id "
                    "GROUP BY e.agent_id ORDER BY episodes DESC")]
                result["models"] = [dict(r) for r in conn.execute(
                    "SELECT agent_id, task_id, model_version, state, training_rows, updated_at FROM agent_model_registry ORDER BY updated_at DESC")]
            except sqlite3.Error:
                pass
            finally:
                conn.close()
        audit = _json_file(self.root / "output" / "research_only_audit_latest.json") or {}
        result["audit"] = {k: audit.get(k) for k in ("run_id", "status", "generated_at")}
        return result

    def review(self) -> dict[str, Any]:
        path = self.root / "data" / "research_registry.db"
        if not path.exists():
            return {"items": []}
        conn = read_only(path)
        try:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='classification_review_item'").fetchone():
                return {"items": []}
            items = [self._review_row(r) for r in conn.execute(
                "SELECT * FROM classification_review_item WHERE status='open' ORDER BY reason, ticker")]
        finally:
            conn.close()
        return {"items": items}

    def taxonomy_nodes(self) -> dict[str, Any]:
        return {"nodes": [{"id": n, "label": info[5], "level": info[4]} for n, info in sorted(self.taxonomy()[1].items())]}


# ---------------------------------------------------------------- actions


class Actions:
    """The only things the page can change. Each call is appended to the audit log."""

    def __init__(self, root: Path = ROOT, launcher: Any = None):
        self.root = root
        self.launcher = launcher or self._launch_detached
        self._lock = threading.Lock()

    def log(self, action: str, payload: dict[str, Any], result: dict[str, Any]) -> None:
        path = self.root / "data" / "control_center" / "actions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": now(), "action": action, "request": payload, "result": result},
                                    ensure_ascii=False) + "\n")

    def review_decision(self, payload: dict[str, Any]) -> dict[str, Any]:
        from research_registry.classification_reassessment import owner_decision
        from research_registry.core import connect

        venue, ticker = str(payload.get("venue_code") or ""), str(payload.get("ticker") or "").upper()
        choice = payload.get("decision")
        if choice not in {"choose", "confirm"} or not venue or not ticker:
            raise ValueError("decision must be 'choose' or 'confirm' with venue_code and ticker")
        node = str(payload.get("node") or "") if choice == "choose" else None
        with self._lock:
            db = connect(self.root / "data" / "research_registry.db")
            try:
                result = owner_decision(db, venue, ticker, node=node, note=str(payload.get("note") or ""),
                                        jobs_root=self.root / "data" / "babis_reassessment",
                                        batches_root=self.root / "data" / "classification_batches")
            finally:
                db.close()
        self.log("review_decision", payload, result)
        return result

    def start_research_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            state = _json_file(self.root / "output" / "full_research_control_plane_background_latest.json") or {}
            if state.get("status") and state.get("status") not in TERMINAL_RUN_STATES and process_alive(state.get("pid")):
                raise ValueError(f"A research run is already active: {state.get('run_id')}")
            launcher = self.root / "start_full_research_with_notify.ps1"
            if not launcher.is_file():
                raise ValueError("Launcher is missing")
            pid = self.launcher(launcher)
        result = {"status": "launched", "launcher_pid": pid, "research_only": True}
        self.log("start_research_run", payload, result)
        return result

    def _launch_detached(self, launcher: Path) -> int:
        # NEW_PROCESS_GROUP | NO_WINDOW. Not DETACHED_PROCESS: windowless PowerShell 5.1 started
        # detached with its standard streams closed exits at once with code 0 without running the
        # script (found 2026-10-01: the start button did nothing). NO_WINDOW gives it a hidden console.
        flags = LAUNCH_FLAGS
        process = subprocess.Popen(
            [r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(launcher)], cwd=str(self.root), creationflags=flags, close_fds=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return process.pid


# ---------------------------------------------------------------- HTTP


def make_handler(views: Views, actions: Actions, token: str, port: int, assets: Path = ASSETS):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://{host}" for host in allowed_hosts}
    get_routes = {
        "/api/status": lambda q: views.status(),
        "/api/runs": lambda q: {"runs": views.runs()},
        "/api/symbols": lambda q: views.symbols(q.get("run")),
        "/api/sectors": lambda q: views.sectors(),
        "/api/agents": lambda q: views.agents(),
        "/api/review": lambda q: views.review(),
        "/api/taxonomy": lambda q: views.taxonomy_nodes(),
        "/api/reports": lambda q: views.reports(q.get("run")),
    }
    post_routes = {
        "/api/actions/review-decision": actions.review_decision,
        "/api/actions/start-research-run": actions.start_research_run,
    }

    class Handler(BaseHTTPRequestHandler):
        server_version = "ControlCenter"
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:  # keep the console quiet
            return

        def _headers(self, status: int, content_type: str, length: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                             "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()

        def _json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self._headers(status, "application/json; charset=utf-8", len(body))
            self.wfile.write(body)

        def _host_ok(self) -> bool:
            if self.headers.get("Host") not in allowed_hosts:
                self._json(HTTPStatus.FORBIDDEN, {"error": "host not allowed"})
                return False
            return True

        def do_GET(self) -> None:  # noqa: N802
            if not self._host_ok():
                return
            parsed = urlparse(self.path)
            query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
            try:
                if parsed.path in STATIC:
                    name, content_type = STATIC[parsed.path]
                    body = (assets / name).read_bytes()
                    if name == "index.html":
                        body = body.replace(b"__SESSION_TOKEN__", token.encode("ascii"))
                    self._headers(HTTPStatus.OK, content_type, len(body))
                    self.wfile.write(body)
                elif parsed.path in get_routes:
                    self._json(HTTPStatus.OK, get_routes[parsed.path](query))
                elif parsed.path.startswith("/api/symbol/"):
                    self._json(HTTPStatus.OK, views.symbol(unquote(parsed.path[len("/api/symbol/"):]), query.get("run")))
                elif parsed.path.startswith("/api/sector/"):
                    self._json(HTTPStatus.OK, views.sector_members(unquote(parsed.path[len("/api/sector/"):])))
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            except ValueError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - shown to the local owner, never raised
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": type(exc).__name__ + ": " + str(exc)[:300]})

        def do_POST(self) -> None:  # noqa: N802
            if not self._host_ok():
                return
            parsed = urlparse(self.path)
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                self.close_connection = True
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body too large"})
                return
            raw = self.rfile.read(length)  # always drained, so a refusal never resets the socket
            if parsed.path not in post_routes:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            if self.headers.get("Origin") not in allowed_origins:
                self._json(HTTPStatus.FORBIDDEN, {"error": "origin not allowed"})
                return
            if not secrets.compare_digest(self.headers.get("X-Control-Token") or "", token):
                self._json(HTTPStatus.FORBIDDEN, {"error": "missing or wrong session token"})
                return
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "JSON body required"})
                return
            try:
                payload = json.loads(raw or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("JSON object required")
                self._json(HTTPStatus.OK, post_routes[parsed.path](payload))
            except ValueError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": type(exc).__name__ + ": " + str(exc)[:300]})

        def do_PUT(self) -> None:  # noqa: N802
            self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method not allowed"})

        do_DELETE = do_PATCH = do_OPTIONS = do_PUT  # no CORS preflight is ever answered

    return Handler


def serve(port: int = DEFAULT_PORT, *, open_browser: bool = True) -> None:
    token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer((HOST, port), make_handler(Views(), Actions(), token, port))
    url = f"http://127.0.0.1:{port}/"
    print(f"Κέντρο Ελέγχου: {url} (μόνο τοπικά, Ctrl+C για τερματισμό)")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    serve(args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
