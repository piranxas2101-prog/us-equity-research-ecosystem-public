"""Jarvis: the ecosystem's own operator (design: docs/superpowers/specs/2026-11-15-jarvis-design.md).

Called at the end of the existing scheduled work, never on its own:

    python jarvis.py after-nightly    # hibernate_after_run.ps1 / the fallback branch of nightly_research.ps1
    python jarvis.py after-session    # the three endings of start_trader_afternoon_session.ps1

Each pass reads what the run and the session left (health), runs the due checks of jarvis_checks.json, learns loss
rules from the paper ledger (shadow lessons only), turns verdicts into proposals for the owner's yes / no / later, and
writes the day's report (output/jarvis/report_<day>.md|.json). It never starts a run or a session, never talks to the
broker, never starts Ollama, and never changes a score, policy, limit, lesson status or order. Every part is wrapped:
a failing part becomes a line in the report and the pass goes on; the exit code is 0.

The day is New York's date. The afternoon pass reports the session of that day with the latest nightly. The nightly
pass belongs to the New York date its run started (it starts 01:30 Athens, the evening before in New York); it writes
that day's report only when none exists - a weekend, a holiday, or an afternoon pass that never ran.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

import jarvis_checks
import jarvis_health
import jarvis_learning
import jarvis_report
import jarvis_decisions
import jarvis_state
import jarvis_tiers

ROOT = Path(__file__).resolve().parent
NEW_YORK = ZoneInfo("America/New_York")
JARVIS_BUDGET_SECONDS = 600
KINDS = ("after-nightly", "after-session")


def ny_today(now: datetime | None = None) -> date:
    return (now or datetime.now(NEW_YORK)).astimezone(NEW_YORK).date()


def nightly_day_from_clock(now: datetime | None = None) -> date:
    """The New York date the nightly belongs to: it starts 01:30 Athens (18:30 New York the evening before) and ends
    before the next New York noon, so twelve hours back lands on the evening it started. Taken from the clock, not
    from the run pointer, which still names the previous run when tonight's did not start (review 2026-11-16)."""
    return ny_today((now or datetime.now(NEW_YORK)).astimezone(NEW_YORK) - timedelta(hours=12))


def expected_nightly_day(kind: str, day: date) -> date:
    """The New York date the nightly behind this pass should have started: the same evening for the nightly pass, the
    evening before for the session it feeds (the nightly runs every night)."""
    return day if kind == "after-nightly" else day - timedelta(days=1)


def keep_awake(on: bool) -> None:
    """Hold the computer awake while the pass runs (a wake-timer start is put back to sleep after a few unattended
    minutes); released at the end. Windows only; elsewhere nothing."""
    if sys.platform != "win32":
        return
    import ctypes

    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except (AttributeError, OSError):
        pass


def is_saturday_nightly(kind: str, day: date) -> bool:
    return kind == "after-nightly" and day.weekday() == 5


def sessions_since(root: Path) -> Callable[[str], int]:
    db = root / "data" / "paper_learning.db"
    days: list[str] = []
    if db.exists():
        try:
            with closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)) as conn:
                days = [d for (d,) in conn.execute("SELECT DISTINCT session_date FROM paper_trade_ledger")]
        except sqlite3.Error:
            days = []
    return lambda since: sum(1 for d in days if d > since)


def summary_line(data: dict[str, Any]) -> str:
    return f"JARVIS {int(data.get('open_proposals') or 0)} proposals, {int(data.get('problems') or 0)} problems"


def _guard(part: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return part()
    except Exception as exc:  # noqa: BLE001 - one failing part must not stop the pass
        return {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}


def _checks(root: Path, *, kind: str, day: date, python: str, deadline: float,
            since: Callable[[str], int]) -> tuple[dict[str, Any], list[jarvis_state.Proposal]]:
    proposals: list[jarvis_state.Proposal] = []

    def part() -> dict[str, Any]:
        results = []
        for check in jarvis_checks.load_registry(root / "jarvis_checks.json"):
            if not jarvis_checks.is_due(check, today=day, is_saturday_nightly=is_saturday_nightly(kind, day),
                                        sessions_since=since):
                continue
            result = jarvis_checks.run_check(check, root=root, python=python, deadline=deadline)
            results.append(result)
            proposal = jarvis_checks.check_proposal(result, check)
            if proposal is not None:
                proposals.append(proposal)
        return {"results": results}

    return _guard(part), proposals


def _learning(root: Path, day: date) -> tuple[dict[str, Any], list[jarvis_state.Proposal]]:
    proposals: list[jarvis_state.Proposal] = []

    def part() -> dict[str, Any]:
        trades = jarvis_learning.load_trades(root / "data" / "paper_learning.db", as_of=day)
        found = jarvis_learning.find_rules(trades)
        intel = root / "data" / "market_intel.db"
        recorded = jarvis_learning.record_rules(intel, found["rules"])
        follow = jarvis_learning.follow(intel, trades)
        proposals.extend(p for p in map(jarvis_learning.follow_proposal, follow) if p is not None)
        return {**found, "recorded": recorded, "follow": follow}

    return _guard(part), proposals


def _guard_list(fn: Callable[[], list[Any]]) -> list[Any]:
    """A part that only adds proposals: on failure it adds none (the pass and its report go on)."""
    try:
        return fn()
    except Exception:  # noqa: BLE001 - one part never stops the pass
        return []


def resume_proposal(root: Path, nightly: dict[str, Any]) -> jarvis_state.Proposal | None:
    """A failed or interrupted run: the owner may resume it from the failed step (Jarvis never starts it)."""
    pointer = jarvis_health.read_json(root / jarvis_health.POINTER) or {}
    run_id, status = pointer.get("run_id"), pointer.get("status")
    steps = {s["name"]: s for s in nightly.get("steps") or []}
    if not run_id or status not in jarvis_health.FAILED_RUN or steps.get("run", {}).get("state") != "failed":
        return None
    step = steps["run"]
    return jarvis_state.Proposal(
        source="run", source_key=f"resume:{run_id}", title=f"Το run {run_id} σταμάτησε ({status}): συνέχεια;",
        evidence={"verdict": status, "run_id": run_id, "diagnosis": step.get("diagnosis"), "detail": step.get("detail")},
        change=(f"Συνέχεια από το βήμα που απέτυχε: .\\.venv-next\\Scripts\\python.exe daily_pipeline.py run --resume {run_id} "
                "(το ξεκινάς εσύ· ο Jarvis δεν ξεκινά run)"))


def paper_learning_proposals(root: Path) -> list[jarvis_state.Proposal]:
    """The paper-learning lessons whose out-of-sample follow reached a verdict (read from its JSON, not imported)."""
    evaluation = jarvis_health.read_json(root / "output" / "paper_learning" / "evaluation_latest.json") or {}
    in_period = jarvis_decisions.period_lesson_ids(root)  # judged by the period's pre-registered test instead
    out = []
    for item in evaluation.get("shadow_follow") or []:
        if item.get("verdict") not in {"confirmed", "not_confirmed"} or not item.get("lesson_id"):
            continue
        if str(item["lesson_id"]) in in_period:
            continue
        confirmed = item["verdict"] == "confirmed"
        evidence = {"verdict": item["verdict"], "sessions_after": item.get("sessions_after"),
                    "minimum_sessions": item.get("minimum_sessions"), "criterion_id": item.get("criterion_id"),
                    "file": "output/paper_learning/evaluation_latest.json"}
        if not confirmed:  # owner 2026-11-18: the yes is carried out by Jarvis
            evidence["action"] = jarvis_decisions.lesson_action([str(item["lesson_id"])], "archived")
        out.append(jarvis_state.Proposal(
            source="paper_learning", source_key=str(item["lesson_id"]), title=str(item.get("proposed_change") or ""),
            evidence=evidence,
            change=("Να γίνει ενεργό (με tests, μαζί με το Claude)" if confirmed
                    else "Αν πεις ναι: ο Jarvis το αποσύρει από το shadow στο επόμενο πέρασμα")))
    return out


def _withdrawals(root: Path, sections: dict[str, Any],
                 proposals: list[jarvis_state.Proposal]) -> list[tuple[str, set[str] | None, set[str]]]:
    """(source, keys the source reported on this pass, keys still ready) for every source that reported: an open
    proposal it reported on but no longer finds ready is withdrawn (review 2026-11-16)."""
    ready: dict[str, set[str]] = {}
    for p in proposals:
        ready.setdefault(p.source, set()).add(p.source_key)
    out: list[tuple[str, set[str] | None, set[str]]] = []
    checks = sections.get("checks") or {}
    if "results" in checks:
        seen = {r["id"] for r in checks["results"] if r["state"] in {"ready", "waiting"}}
        out.append(("check", seen, ready.get("check", set())))
    learning = sections.get("learning") or {}
    if "follow" in learning:
        out.append(("jarvis_learning", {f["lesson_id"] for f in learning["follow"]}, ready.get("jarvis_learning", set())))
    evaluation = jarvis_health.read_json(root / "output" / "paper_learning" / "evaluation_latest.json")
    if evaluation is not None:
        seen = {str(i["lesson_id"]) for i in evaluation.get("shadow_follow") or [] if i.get("lesson_id")}
        out.append(("paper_learning", seen, ready.get("paper_learning", set())))
    if "steps" in (sections.get("nightly") or {}):
        out.append(("run", None, ready.get("run", set())))
    return out


CLOSEST_LESSONS = 3


def ecosystem(root: Path) -> dict[str, Any]:
    """How the market reads and what the ecosystem is learning (owner 2026-11-16: Jarvis is told about everything in
    its work). None for a part whose file is missing - shown as unknown."""
    state = jarvis_health.read_json(root / "output" / "market_state_latest.json")
    market = None if state is None else {k: state.get(k) for k in ("session", "trend", "mood", "paragraph")}
    evaluation = jarvis_health.read_json(root / "output" / "paper_learning" / "evaluation_latest.json")
    lessons = None
    if evaluation is not None:
        follow = [i for i in evaluation.get("shadow_follow") or [] if isinstance(i, dict)]
        counts: dict[str, int] = {}
        for item in follow:
            counts[str(item.get("verdict"))] = counts.get(str(item.get("verdict")), 0) + 1

        def progress(item: dict[str, Any]) -> float:
            need = item.get("minimum_sessions") or 0
            return (item.get("sessions_after") or 0) / need if need else 0.0

        waiting = sorted((i for i in follow if i.get("verdict") == "collecting"), key=progress, reverse=True)
        lessons = {"by_verdict": counts, "closest": [{k: i.get(k) for k in (
            "lesson_id", "proposed_change", "sessions_after", "minimum_sessions")} for i in waiting[:CLOSEST_LESSONS]]}
    import agent_learning_control_plane

    # owner 2026-11-18: new lessons wait as candidates until the owner puts them in shadow for a measurement period
    waiting = agent_learning_control_plane.waiting_lessons(root / "data" / "market_intel.db")
    return {"market": market, "lessons": lessons, "waiting_lessons": waiting}


def refresh_search_index(root: Path, embedder: Any = None) -> dict[str, Any]:
    """Bring the chat's search index up to date after the session (owner 2026-11-17), only if Ollama already runs:
    the scheduled passes never start it. Changed files only, within five minutes."""
    import jarvis_search

    embedder = embedder or jarvis_search.OllamaEmbedder(num_thread=8)
    if not embedder.available():
        return {"available": False, "reason": "το Ollama ή το bge-m3 δεν τρέχει· το ευρετήριο ενημερώνεται στην επόμενη ερώτηση"}
    return jarvis_search.build(root, embedder, budget_seconds=300)


def morning_session_day(now: datetime | None = None) -> date:
    """The date of the session the morning prepares: today's in Athens, where it starts at 16:30. Not New York's
    date - the nightly ends around 06:30 Athens, when New York still has the day before."""
    athens = ZoneInfo("Europe/Athens")
    return (now or datetime.now(athens)).astimezone(athens).date()


def run_debate_morning(root: Path, day: date) -> None:
    """The debate on every trade the traders proposed for today, after the nightly run (owner 2026-11-18: prepare
    the day in the morning, never during the session). Its own failure stays its own."""
    import debate_shadow

    try:
        debate_shadow.main(["run", "--root", str(root), "--session-date", day.isoformat(), "--morning"])
    except Exception as exc:  # noqa: BLE001 - the Jarvis pass is already written
        print(f"DEBATE failed: {type(exc).__name__}")


def _chat_budget() -> int:
    try:
        return int(json.loads((ROOT / "jarvis_config.json").read_text(encoding="utf-8-sig"))["max_total_chars"])
    except (OSError, ValueError, KeyError):
        return 1800


def _write_report(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    markdown, data = jarvis_report.render(record)
    folder = root / "output" / "jarvis"
    folder.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    for name, text in ((f"report_{record['day']}.md", markdown), (f"report_{record['day']}.json", payload),
                       ("report_latest.md", markdown), ("report_latest.json", payload)):
        (folder / name).write_text(text, encoding="utf-8")
    return data


def _save_state(root: Path, record: dict[str, Any]) -> None:
    path = root / "data" / "jarvis" / "pass_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    state = jarvis_health.read_json(path) or {}
    state[record["kind"]] = {"day": record["day"], "finished_at": record["finished_at"],
                             "report_written": record["report_written"]}
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def run_pass(kind: str, *, root: Path, today: date, python: str, clock: Callable[[], float] = time.time,
             refresh: Callable[[Path], dict[str, Any]] | None = None) -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    start = clock()
    deadline = start + JARVIS_BUDGET_SECONDS
    day = today
    since = sessions_since(root)
    report_path = root / "output" / "jarvis" / f"report_{day.isoformat()}.md"
    write_report = kind == "after-session" or not report_path.exists()
    weekend = day.weekday() >= 5
    record: dict[str, Any] = {"schema": "ecosystem.jarvis_pass.v1", "kind": kind, "day": day.isoformat(),
                              "started_at": datetime.now().astimezone().isoformat(timespec="seconds"), "sections": {}}
    sections = record["sections"]
    # owner 2026-11-18: the owner's approvals go through Jarvis - a "yes" on a lesson is carried out here, once
    sections["applied"] = _guard(lambda: {"items": jarvis_decisions.apply_approved(root)})
    expected = expected_nightly_day(kind, day)
    sections["nightly"] = _guard(lambda: {"steps": [s.as_dict() for s in
                                                    jarvis_health.nightly_health(root, expected_day=expected)]})
    if weekend:
        sections["session"] = {"weekend": True, "steps": []}
    else:
        sections["session"] = _guard(lambda: {"steps": [s.as_dict() for s in
                                                        jarvis_health.session_health(root, day.isoformat())]})
    sections["checks"], proposals = _checks(root, kind=kind, day=day, python=python, deadline=deadline, since=since)
    sections["ecosystem"] = _guard(lambda: ecosystem(root))
    if kind == "after-session" and refresh is not None:  # the chat's search index (owner 2026-11-17)
        sections["search_index"] = _guard(lambda: refresh(root))
    if kind == "after-nightly":  # the AUTO tier: Jarvis's own chat settings (owner 2026-11-17)
        sections["tuning"] = _guard(lambda: {"changes": jarvis_tiers.tune(root, base=_chat_budget())})
    if write_report:  # learning once a day: after the session, or by the nightly on a day without one
        sections["learning"], learned = _learning(root, day)
        proposals += learned
    resume = _guard(lambda: {"proposal": resume_proposal(root, sections["nightly"])})
    if resume.get("proposal") is not None:
        proposals.append(resume["proposal"])
    proposals += paper_learning_proposals(root)
    proposals += _guard_list(lambda: jarvis_decisions.period_proposals(root))
    withdrawals = _withdrawals(root, sections, proposals)

    def store() -> dict[str, Any]:
        with closing(jarvis_state.connect(root / "data" / "jarvis" / "proposals.sqlite3")) as conn:
            actions = {}
            for proposal in proposals:
                pid, action = jarvis_state.upsert_proposal(conn, proposal, session_date=day.isoformat())
                actions[pid] = action
            for source, seen, ready in withdrawals:
                for pid in jarvis_state.withdraw_stale(conn, source=source, seen=seen, ready=ready,
                                                       session_date=day.isoformat()):
                    actions[pid] = "withdrawn"
            return {"actions": actions,
                    "open": jarvis_state.open_proposals(conn, session_date=day.isoformat(), sessions_since=since)}

    sections["proposals"] = _guard(store)

    def owner() -> dict[str, Any]:  # what waits for the owner, after the proposals are stored (owner 2026-11-17)
        import jarvis_owner

        result = jarvis_owner.pending(root)
        return {**result, "story": jarvis_owner.story(result)}

    sections["owner"] = _guard(owner)
    record["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    record["seconds"] = round(clock() - start, 1)
    record["report_written"] = write_report
    if write_report:
        data = _write_report(root, record)
        record["problems"], record["open_proposals"] = data["problems"], data["open_proposals"]
    else:
        _, data = jarvis_report.render(record)
        record["problems"], record["open_proposals"] = data["problems"], data["open_proposals"]
    _save_state(root, record)
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Jarvis: health, due checks, learning, proposals, daily report.")
    parser.add_argument("kind", choices=KINDS)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--today", type=date.fromisoformat, default=None, help="New York date (default: from the clock)")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.today is not None:
        day = args.today
    elif args.kind == "after-nightly":
        day = nightly_day_from_clock()
    else:
        day = ny_today()
    keep_awake(True)
    try:
        record = run_pass(args.kind, root=root, today=day, python=sys.executable, refresh=refresh_search_index)
        if args.kind == "after-nightly":
            session_day = morning_session_day()
            if session_day.weekday() < 5:  # a session is coming today
                run_debate_morning(root, session_day)
    except Exception as exc:  # noqa: BLE001 - never stop the launcher's rest step
        print(f"JARVIS failed: {type(exc).__name__}")
        return 0
    finally:
        keep_awake(False)
    print(summary_line(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
