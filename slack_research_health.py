"""Health monitor for the research-only Slack/Notion communication path."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_team_chat import load_dotenv
from slack_agent_gateway import GatewayProcessLock


def gateway_running(lock_path: Path) -> bool:
    probe = GatewayProcessLock(lock_path)
    try:
        probe.acquire()
    except RuntimeError:
        return True
    else:
        probe.close()
        return False


def stale_claims(state_path: Path, *, max_age_minutes: int = 10) -> list[str]:
    if not state_path.exists():
        return []
    connection = sqlite3.connect(state_path)
    try:
        rows = connection.execute(
            "SELECT event_id FROM slack_events WHERE status='claimed' "
            "AND datetime(updated_at) < datetime('now', ?)",
            (f"-{max_age_minutes} minutes",),
        ).fetchall()
        return [str(row[0]) for row in rows]
    finally:
        connection.close()


def overdue_outbox(
    outbox: Path, acknowledgements: Path, *, max_age_minutes: int = 60
) -> list[str]:
    if not outbox.exists():
        return []
    now = datetime.now(timezone.utc).timestamp()
    overdue: list[str] = []
    for event in outbox.glob("*_research_conversation.json"):
        if (acknowledgements / event.name).exists():
            continue
        age_minutes = (now - event.stat().st_mtime) / 60
        if age_minutes >= max_age_minutes:
            overdue.append(event.name)
    return overdue


def slack_auth_ok() -> bool:
    import os

    token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    if not token.startswith("xoxb-"):
        return False
    try:
        from slack_sdk import WebClient  # type: ignore

        return bool(WebClient(token=token).auth_test().get("ok"))
    except Exception:
        return False


def report(args: argparse.Namespace) -> dict[str, Any]:
    running = gateway_running(Path(args.lock))
    slack_ok = slack_auth_ok()
    claims = stale_claims(Path(args.state), max_age_minutes=args.claim_minutes)
    backlog = overdue_outbox(
        Path(args.outbox), Path(args.acknowledgements), max_age_minutes=args.outbox_minutes
    )
    issues: list[str] = []
    if not running:
        issues.append("gateway_not_running")
    if not slack_ok:
        issues.append("slack_auth_failed")
    if claims:
        issues.append(f"stale_claimed_events:{len(claims)}")
    if backlog:
        issues.append(f"notion_projection_overdue:{len(backlog)}")
    return {
        "schema": "ecosystem.slack_research_health.v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "healthy" if not issues else "unhealthy",
        "gateway_running": running,
        "slack_auth_ok": slack_ok,
        "stale_claimed_events": claims,
        "overdue_notion_events": backlog,
        "issues": issues,
        "ollama_required": False,
    }


def maybe_notify(current: dict[str, Any], state_path: Path, channel_id: str) -> None:
    previous: dict[str, Any] = {}
    if state_path.exists():
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
    changed = previous.get("status") != current.get("status") or previous.get("issues") != current.get("issues")
    if changed and (current.get("status") == "unhealthy" or previous.get("status") == "unhealthy"):
        import os

        from slack_sdk import WebClient  # type: ignore

        token = os.getenv("SLACK_BOT_TOKEN", "").strip()
        if token.startswith("xoxb-"):
            if current["status"] == "healthy":
                text = "✅ Το Slack research gateway επανήλθε σε healthy κατάσταση."
            else:
                text = "⚠️ Slack research health alert: `" + ", ".join(current["issues"]) + "`. Καμία συναλλαγή δεν επηρεάστηκε."
            WebClient(token=token).chat_postMessage(channel=channel_id, text=text)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Check Slack research communication health.")
    value.add_argument("--env-file", default=".env")
    value.add_argument("--lock", default="data/slack_agent_gateway.lock")
    value.add_argument("--state", default="data/slack_agent_gateway.sqlite3")
    value.add_argument("--outbox", default="output/integration_outbox/research_conversations")
    value.add_argument("--acknowledgements", default="output/integration_outbox/research_conversations/sent")
    value.add_argument("--health-state", default="data/slack_research_health_state.json")
    value.add_argument("--channel-id", default="C0BU132535F")
    value.add_argument("--claim-minutes", type=int, default=10)
    value.add_argument("--outbox-minutes", type=int, default=60)
    value.add_argument("--notify", action="store_true")
    return value


def main() -> int:
    args = parser().parse_args()
    load_dotenv(Path(args.env_file))
    current = report(args)
    if args.notify:
        try:
            maybe_notify(current, Path(args.health_state), args.channel_id)
        except Exception:
            current["issues"].append("health_notification_failed")
            current["status"] = "unhealthy"
    print(json.dumps(current, ensure_ascii=False))
    return 0 if current["status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
