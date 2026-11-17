"""Debate shadow: a compact local-LLM debate on every trade Koulis approves, in shadow (owner 2026-11-16).

Design: docs/superpowers/specs/2026-11-16-debate-shadow-design.md; rule: docs/RULES_REVIEW.md section 12.

In the style of TradingAgents (bull researcher, bear researcher, judge), made compact for the owner's CPU: one model
call per approved trade writes the bull case, the bear case, the main risk and a rating (support / neutral /
oppose), from the ecosystem's own evidence as it stood at the approval: the trader's rationale and invalidators, the
frozen packet's long / short case, summary and kill criteria, last night's market reading and the headlines already
seen. Every opinion is one immutable row; an unreadable or missing answer is ``unknown``, never neutral.

It never touches an order, approval, score, limit or position. Whether the opinion tells good approvals from bad is
measured by ``evaluate`` (run by Jarvis after the ledger); only if it passes may a Koulis veto be proposed, and turning
it on stays the owner's call.

    python debate_shadow.py run          # started in the background by the session launcher, after Koulis
    python debate_shadow.py evaluate     # outcomes, verdict, report
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, time as clock_time, timezone
import hashlib
import json
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
import time
from typing import Any, Callable
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DB = Path("data") / "debate_shadow.sqlite3"
KOULIS = Path("output") / "koulis_order_intents_latest.json"
PACKET = Path("output") / "fundamental_research_packet_latest.json"
MARKET = Path("output") / "market_state_latest.json"
NEWS = Path("output") / "news_watch_latest.json"
MILTOS = Path("output") / "miltos_entry_stalker_latest.json"
OUT = Path("output") / "paper_learning"
NEW_YORK = ZoneInfo("America/New_York")
PROMPT_VERSION = "debate.v1"
RATINGS = ("support", "neutral", "oppose")
MODEL = "qwen2.5:3b"
OLLAMA_HOST = "http://127.0.0.1:11434"
NUM_THREAD = 4
NUM_PREDICT = 220
CALL_TIMEOUT = 300
STOP_AT = clock_time(16, 0)  # New York
MILTOS_POLL_SECONDS = 300
LATE_TOLERANCE_SECONDS = 60
LATE_STRIKES = 2
MAX_NEWS = 3
SCHEMA = {"type": "object", "required": ["bull", "bear", "main_risk", "rating", "confidence"],
          "properties": {"bull": {"type": "string"}, "bear": {"type": "string"}, "main_risk": {"type": "string"},
                         "rating": {"type": "string", "enum": list(RATINGS)},
                         "confidence": {"type": "integer", "minimum": 1, "maximum": 3}}}
SYSTEM = ("You are a trading committee of three: a bull analyst, a bear analyst and a judge. Use only the evidence "
          "given. Write one sentence for the bull case, one for the bear case, one for the main risk, then the "
          "judge's rating of this trade as planned: support, neutral or oppose, and confidence 1-3. If the evidence "
          "is thin, say so and lower the confidence. Answer with JSON only.")


def _read(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------------------------------------------- inputs and prompt

def build_inputs(intent: dict[str, Any], thesis: dict[str, Any] | None, *, market: dict[str, Any] | None,
                 news: list[str]) -> dict[str, Any]:
    plan = intent.get("trade_plan") or {}
    horizon = plan.get("holding_horizon") or {}
    summary = (thesis or {}).get("fundamental_summary") or {}
    return {
        "ticker": intent.get("ticker"), "company": intent.get("company"), "agent": intent.get("agent"),
        "direction": intent.get("trade_direction"), "playbook": intent.get("playbook_name"),
        "signal_score": intent.get("signal_score"), "strategy_rank_pct": intent.get("strategy_rank_pct"),
        "market_fit": (intent.get("market_state_fit") or {}).get("fit"),
        "holding_sessions": horizon.get("sessions") or plan.get("catalog_holding_horizon"),
        "rationale": list(plan.get("initial_rationale") or []), "invalidators": list(plan.get("invalidators") or []),
        "thesis_known": thesis is not None,
        "giannis_tier": summary.get("giannis_tier"), "giannis_score": summary.get("giannis_score"),
        "readiness": (thesis or {}).get("readiness_status"),
        "long_case": ((thesis or {}).get("long_case") or {}).get("supporting_facts") or [],
        "short_case": ((thesis or {}).get("short_case") or {}).get("supporting_facts") or [],
        "kill_criteria": list((thesis or {}).get("kill_criteria") or []),
        "market": None if market is None else {k: market.get(k) for k in ("trend", "mood", "participation", "appetite")},
        "news": list(news)[:MAX_NEWS],
    }


def inputs_hash(inputs: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _line(items: list[Any]) -> str:
    return "; ".join(str(i) for i in items) if items else "none"


def build_prompt(inputs: dict[str, Any]) -> str:
    def known(value: Any) -> Any:
        return "unknown" if value in (None, "", []) else value

    market = inputs.get("market")
    lines = [
        f"Trade: {known(inputs['direction'])} {known(inputs['ticker'])} ({known(inputs['company'])}), strategy "
        f"{known(inputs['playbook'])} by {known(inputs['agent'])}, signal {known(inputs['signal_score'])}, "
        f"holding {known(inputs['holding_sessions'])} sessions, market fit {known(inputs['market_fit'])}.",
        f"Trader's reasons: {_line(inputs['rationale'])}.",
        f"Trader's invalidators: {_line(inputs['invalidators'])}.",
    ]
    if inputs.get("thesis_known"):
        lines += [f"Fundamentals: tier {known(inputs['giannis_tier'])}, score {known(inputs['giannis_score'])}, "
                  f"readiness {known(inputs['readiness'])}.",
                  f"Long case facts: {_line(inputs['long_case'])}.",
                  f"Short case facts: {_line(inputs['short_case'])}.",
                  f"Kill criteria: {_line(inputs['kill_criteria'])}."]
    else:
        lines.append("Fundamentals: unknown (no thesis in the packet).")
    lines.append("Market: unknown." if market is None else
                 f"Market: trend {known(market.get('trend'))}, mood {known(market.get('mood'))}, participation "
                 f"{known(market.get('participation'))}, appetite {known(market.get('appetite'))}.")
    lines.append(f"Recent headlines: {_line(inputs['news'])}.")
    return "\n".join(lines)


def parse_opinion(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text or "")
    except ValueError:
        return {"rating": "unknown", "reason": "the answer was not JSON"}
    if not isinstance(value, dict) or value.get("rating") not in RATINGS:
        return {"rating": "unknown", "reason": "no valid rating in the answer"}
    confidence = value.get("confidence")
    return {"rating": value["rating"], "confidence": confidence if confidence in (1, 2, 3) else None,
            "bull": str(value.get("bull") or ""), "bear": str(value.get("bear") or ""),
            "main_risk": str(value.get("main_risk") or "")}


# ----------------------------------------------------------------------------------------------- store

COLUMNS = ("session_date", "intent_id", "priority", "agent", "ticker", "playbook", "direction", "inputs_hash",
           "prompt_version", "model", "rating", "confidence", "bull", "bear", "main_risk", "reason", "written_at",
           "seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS opinions (session_date TEXT NOT NULL, intent_id TEXT NOT NULL,"
                 " priority INTEGER, agent TEXT, ticker TEXT, playbook TEXT, direction TEXT, inputs_hash TEXT,"
                 " prompt_version TEXT, model TEXT, rating TEXT NOT NULL, confidence INTEGER, bull TEXT, bear TEXT,"
                 " main_risk TEXT, reason TEXT, written_at TEXT, seconds REAL,"
                 " PRIMARY KEY (session_date, intent_id))")
    conn.execute("CREATE TABLE IF NOT EXISTS runs (session_date TEXT NOT NULL, started_at TEXT, ended_at TEXT,"
                 " judged INTEGER, stopped TEXT, detail TEXT)")
    conn.commit()
    return conn


def store(conn: sqlite3.Connection, row: dict[str, Any]) -> bool:
    """One row per (session, intent), never rewritten. True when written."""
    cursor = conn.execute(f"INSERT OR IGNORE INTO opinions ({', '.join(COLUMNS)}) VALUES "
                          f"({', '.join('?' for _ in COLUMNS)})", tuple(row.get(c) for c in COLUMNS))
    conn.commit()
    return cursor.rowcount == 1


# ----------------------------------------------------------------------------------------------- the model

class DebateClient:
    """Local Ollama only; asks for the JSON schema above."""

    def __init__(self, host: str = OLLAMA_HOST, model: str = MODEL) -> None:
        self.host, self.model = host.rstrip("/"), model

    def available(self) -> bool:
        try:
            with urllib.request.urlopen(self.host + "/api/tags", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, ValueError):
            return False
        return any(str(m.get("name")) == self.model for m in payload.get("models") or [])

    def generate(self, system: str, prompt: str) -> str:
        body = {"model": self.model, "system": system, "prompt": prompt, "stream": False, "format": SCHEMA,
                "keep_alive": "30m", "options": {"temperature": 0, "seed": 0, "num_ctx": 4096,
                                                 "num_predict": NUM_PREDICT, "num_thread": NUM_THREAD}}
        request = urllib.request.Request(self.host + "/api/generate", data=json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=CALL_TIMEOUT) as response:
            return str(json.loads(response.read().decode("utf-8")).get("response") or "")


# ----------------------------------------------------------------------------------------------- run

def load_theses(root: Path) -> dict[str, dict[str, Any]]:
    packet = _read(root / PACKET) or {}
    return {str(t.get("ticker")).upper(): t for t in packet.get("theses") or [] if isinstance(t, dict)}


def news_for(root: Path, ticker: str, until: str | None) -> list[str]:
    payload = _read(root / NEWS) or {}
    out = []
    for item in payload.get("material") or []:
        if str(item.get("symbol") or "").upper() != ticker.upper():
            continue
        if until and str(item.get("published_at") or "") > until:
            continue  # not yet seen at the approval
        out.append(str(item.get("title") or ""))
    return out


def run(root: Path, *, client: Any, session_date: str, deadline: Callable[[], bool], guard: Any,
        theses: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    koulis = _read(root / KOULIS) or {}
    if str(koulis.get("session_date")) != session_date:
        return {"status": "no_approvals_for_session", "judged": 0, "stopped": None}
    intents = [i for i in koulis.get("approved_intents") or [] if isinstance(i, dict) and i.get("intent_id")]
    if theses is None:
        theses = load_theses(root)
    market = _read(root / MARKET)
    approved_at = str(koulis.get("generated_at") or "") or None
    judged, stopped, started = 0, None, _now()
    with closing(connect(root / DB)) as conn:
        done = {r[0] for r in conn.execute("SELECT intent_id FROM opinions WHERE session_date=?", (session_date,))}
        for priority, intent in enumerate(intents):
            if intent["intent_id"] in done:
                continue
            if deadline():
                stopped = "deadline"
                break
            if guard is not None and guard.should_stop():
                stopped = f"guard: {guard.reason}"
                break
            ticker = str(intent.get("ticker") or "")
            inputs = build_inputs(intent, theses.get(ticker.upper()), market=market,
                                  news=news_for(root, ticker, approved_at))
            t0 = time.monotonic()
            try:
                opinion = parse_opinion(client.generate(SYSTEM, build_prompt(inputs)))
            except (OSError, urllib.error.URLError, ValueError) as exc:
                opinion = {"rating": "unknown", "reason": f"model call failed: {type(exc).__name__}"}
            store(conn, {"session_date": session_date, "intent_id": intent["intent_id"], "priority": priority,
                         "agent": intent.get("agent"), "ticker": ticker, "playbook": intent.get("playbook_name"),
                         "direction": intent.get("trade_direction"), "inputs_hash": inputs_hash(inputs),
                         "prompt_version": PROMPT_VERSION, "model": getattr(client, "model", MODEL),
                         "written_at": _now(), "seconds": round(time.monotonic() - t0, 1), **opinion})
            judged += 1
        conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)",
                     (session_date, started, _now(), judged, stopped, None))
        conn.commit()
    return {"status": "ok", "judged": judged, "stopped": stopped, "approvals": len(intents)}


# ----------------------------------------------------------------------------------------------- guard, Ollama, clock

def parse_time(text: str) -> datetime:
    value = datetime.fromisoformat(str(text))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def past_stop(now: datetime) -> bool:
    return now.astimezone(NEW_YORK).time() >= STOP_AT


class SessionGuard:
    """Stops the debate when the live session runs late: Miltos's cycle (``run_timestamp``) older than its poll
    interval plus a tolerance on two checks in a row. A Miltos file from before the debate started (yesterday's, or
    Miltos not started yet) is not lateness."""

    def __init__(self, root: Path, *, now: Callable[[], datetime] | None = None, started_at: datetime | None = None,
                 poll_seconds: int = MILTOS_POLL_SECONDS, tolerance: int = LATE_TOLERANCE_SECONDS) -> None:
        self.root = root
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.started_at = started_at or self.now()
        self.limit = poll_seconds + tolerance
        self.strikes = 0
        self.reason = ""

    def should_stop(self) -> bool:
        stamp = (_read(self.root / MILTOS) or {}).get("run_timestamp")
        try:
            last = parse_time(stamp) if stamp else None
        except ValueError:
            last = None
        if last is None or last < self.started_at:
            self.strikes = 0
            return False
        late = (self.now() - last).total_seconds()
        self.strikes = self.strikes + 1 if late > self.limit else 0
        if self.strikes >= LATE_STRIKES:
            self.reason = f"Miltos's cycle ran {int(late)} s after the last one (limit {self.limit} s), twice in a row"
            return True
        return False


def ensure_ollama(client: Any, *, starter: Callable[[], bool], wait_seconds: float = 30,
                  poll: Callable[[], None] = lambda: time.sleep(1)) -> bool:
    """Ollama up, starting the server once if it is down (owner 2026-11-16 allowed the debate to start it)."""
    if client.available():
        return True
    if not starter():
        return False
    end = time.monotonic() + wait_seconds
    while time.monotonic() < end:
        poll()
        if client.available():
            return True
    return False


def lower_ollama_priority(runner: Callable[..., Any] = subprocess.run) -> None:
    """Best effort: the Ollama processes run below normal priority, so the session's own programs come first."""
    command = ("Get-Process -Name 'ollama*' -ErrorAction SilentlyContinue | "
               "ForEach-Object { try { $_.PriorityClass = 'BelowNormal' } catch { } }")
    try:
        runner([r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "-NoProfile", "-Command", command],
               capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


# ----------------------------------------------------------------------------------------------- evaluate

MIN_OUTCOMES = 30
MIN_SESSIONS = 10
MARGIN_PCT = 0.5
BOOTSTRAP = 2000


def outcomes(root: Path, *, as_of: date) -> list[dict[str, Any]]:
    """Each opinion with its approval's outcome as excess over the session's SPY: a trade's result at the fill
    (mature only, owner's rule 2026-11-15) or a never-entered approval's counterfactual open-to-close."""
    import jarvis_learning

    db = root / DB
    if not db.exists():
        return []
    with closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)) as conn:
        cursor = conn.execute("SELECT * FROM opinions ORDER BY session_date, priority")
        names = [c[0] for c in cursor.description]
        opinions = [dict(zip(names, r)) for r in cursor.fetchall()]
    records: dict[tuple, dict[str, Any]] = {}
    counter: dict[str, Any] = {}
    spy: dict[str, float] = {}
    ledger_db = root / "data" / "paper_learning.db"
    if ledger_db.exists():
        with closing(sqlite3.connect(f"file:{ledger_db.as_posix()}?mode=ro", uri=True)) as conn:
            for record_id, day, kind, payload in conn.execute(
                    "SELECT record_id, session_date, kind, record_json FROM paper_trade_ledger"
                    " WHERE kind IN ('trade', 'approved_not_entered')"):
                r = json.loads(payload)
                key = (day, r.get("agent"), str(r.get("ticker")).upper(), r.get("playbook"), r.get("direction"))
                records[key] = {**r, "record_id": record_id, "kind": kind}
            try:
                rows = conn.execute("SELECT c.record_id, c.open_close_pct, c.spy_open_close_pct, l.session_date"
                                    " FROM paper_counterfactuals c JOIN paper_trade_ledger l USING(record_id)").fetchall()
            except sqlite3.Error:
                rows = []
            for record_id, oc, spy_pct, day in rows:
                counter[record_id] = oc
                if isinstance(spy_pct, (int, float)):
                    spy.setdefault(day, spy_pct)
    out = []
    for o in opinions:
        key = (o["session_date"], o["agent"], str(o["ticker"]).upper(), o["playbook"], o["direction"])
        record = records.get(key)
        outcome, entered_before = None, None
        if record and o["rating"] in RATINGS:
            if record["kind"] == "trade":
                if isinstance(record.get("result_pct"), (int, float)) and jarvis_learning.mature(record, as_of):
                    outcome = record["result_pct"]
                if record.get("entry_at") and o.get("written_at"):
                    entered_before = parse_time(record["entry_at"]) < parse_time(o["written_at"])
            else:
                oc = counter.get(record["record_id"])
                outcome = oc if isinstance(oc, (int, float)) else None
        day_spy = spy.get(o["session_date"])
        excess = None if outcome is None or day_spy is None else round(outcome - day_spy, 3)
        out.append({**o, "kind": (record or {}).get("kind"), "excess_pct": excess, "entered_before": entered_before})
    return out


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def judge(rows: list[dict[str, Any]], *, seed: int = 0) -> dict[str, Any]:
    """Owner-approved test (RULES_REVIEW section 12): >= 30 outcomes on >= 10 sessions; oppose worse than support by
    at least 0.5 points of excess, with the 95% day-bootstrap interval of (support - oppose) above zero."""
    known = [r for r in rows if r.get("excess_pct") is not None and r.get("rating") in ("support", "oppose")]
    days = sorted({r["session_date"] for r in known})
    support = [r["excess_pct"] for r in known if r["rating"] == "support"]
    oppose = [r["excess_pct"] for r in known if r["rating"] == "oppose"]
    result = {"outcomes": len(known), "sessions": len(days), "support": {"n": len(support), "mean": _mean(support)},
              "oppose": {"n": len(oppose), "mean": _mean(oppose)}, "gap": None, "ci_95": None}
    if len(known) < MIN_OUTCOMES or len(days) < MIN_SESSIONS or not support or not oppose:
        return {**result, "verdict": "insufficient_sample"}
    gap = round(result["support"]["mean"] - result["oppose"]["mean"], 3)
    by_day: dict[str, list[dict[str, Any]]] = {}
    for r in known:
        by_day.setdefault(r["session_date"], []).append(r)
    rng = random.Random(seed)
    gaps = []
    for _ in range(BOOTSTRAP):
        sample = [r for d in (rng.choice(days) for _ in days) for r in by_day[d]]
        s = [r["excess_pct"] for r in sample if r["rating"] == "support"]
        o = [r["excess_pct"] for r in sample if r["rating"] == "oppose"]
        if s and o:
            gaps.append(sum(s) / len(s) - sum(o) / len(o))
    gaps.sort()
    ci = [round(gaps[int(0.025 * len(gaps))], 3), round(gaps[int(0.975 * len(gaps)) - 1], 3)] if gaps else None
    passed = gap >= MARGIN_PCT and ci is not None and ci[0] > 0
    return {**result, "gap": gap, "ci_95": ci, "verdict": "veto_would_help" if passed else "no_clear_difference"}


VERDICT_GR = {"veto_would_help": "το βέτο θα βοηθούσε", "no_clear_difference": "καμία καθαρή διαφορά",
              "insufficient_sample": "ανεπαρκές δείγμα"}


def _num(value: Any) -> str:
    return "άγνωστο" if value is None else str(value)


def render(result: dict[str, Any]) -> str:
    j = result["judgement"]
    lines = ["# Συζήτηση shadow (στο στυλ του TradingAgents)", "",
             f"Ετυμηγορία: **{VERDICT_GR[j['verdict']]}** ({j['outcomes']} αποτελέσματα σε {j['sessions']} "
             f"συνεδρίες, χρειάζονται {MIN_OUTCOMES} σε {MIN_SESSIONS}).",
             f"- υπέρ: {j['support']['n']} θέσεις, μέσο πάνω από το SPY {_num(j['support']['mean'])}%",
             f"- κατά: {j['oppose']['n']} θέσεις, μέσο πάνω από το SPY {_num(j['oppose']['mean'])}%",
             f"- διαφορά υπέρ μείον κατά: {_num(j['gap'])} (95%: {_num(j['ci_95'])})",
             "",
             f"Γνώμες συνολικά: {result['opinions']} (άγνωστες: {result['unknown']}). Γράφτηκαν πριν από την είσοδο: "
             f"{result['before_entry']} από {result['entered_known']} θέσεις που μπήκαν.",
             f"Στάσεις από την προστασία της συνεδρίας: {result['guard_stops']}.",
             "", "Καμία εντολή, όριο ή θέση δεν αλλάζει (shadow)."]
    return "\n".join(lines) + "\n"


def evaluate(root: Path, *, as_of: date) -> dict[str, Any]:
    rows = outcomes(root, as_of=as_of)
    judgement = judge(rows)
    guard_stops = 0
    if (root / DB).exists():
        with closing(sqlite3.connect(f"file:{(root / DB).as_posix()}?mode=ro", uri=True)) as conn:
            guard_stops = conn.execute("SELECT COUNT(*) FROM runs WHERE stopped LIKE 'guard%'").fetchone()[0]
    entered = [r for r in rows if r.get("entered_before") is not None]
    result = {"schema": "ecosystem.debate_shadow.v1", "generated_at": _now(), "as_of": as_of.isoformat(),
              "verdict": judgement["verdict"], "judgement": judgement, "opinions": len(rows),
              "unknown": sum(1 for r in rows if r.get("rating") == "unknown"),
              "before_entry": sum(1 for r in entered if not r["entered_before"]), "entered_known": len(entered),
              "guard_stops": guard_stops, "active_decisions_changed": False}
    out = root / OUT
    out.mkdir(parents=True, exist_ok=True)
    (out / "debate_shadow_latest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "debate_shadow_latest.md").write_text(render(result), encoding="utf-8")
    return result


# ----------------------------------------------------------------------------------------------- command line

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Debate shadow: opinions on approved trades, in shadow.")
    parser.add_argument("command", choices=("run", "evaluate"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--session-date", default=None, help="New York date (default: today)")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    today = datetime.now(NEW_YORK).date()
    if args.command == "evaluate":
        result = evaluate(root, as_of=today)
        print(f"DEBATE {result['verdict']} opinions={result['opinions']}")
        return 0
    import jarvis_chat

    client = DebateClient()
    if not ensure_ollama(client, starter=lambda: jarvis_chat.start_ollama_server(log_dir=root / "logs")):
        print("DEBATE Ollama is not available; nothing judged")
        return 0
    lower_ollama_priority()
    guard = SessionGuard(root)
    result = run(root, client=client, session_date=args.session_date or today.isoformat(),
                 deadline=lambda: past_stop(datetime.now(timezone.utc)), guard=guard)
    print(f"DEBATE {result['status']} judged={result['judged']} stopped={result['stopped']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
