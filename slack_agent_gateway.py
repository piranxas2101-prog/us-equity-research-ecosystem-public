"""Real-time Socket Mode gateway for the research-agent Slack rooms.

Required environment variables are SLACK_BOT_TOKEN (xoxb) and
SLACK_APP_TOKEN (xapp). The gateway is research-only and has no broker import.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import Any

from research_team_chat import (
    ConversationOutbox,
    ResearchRequest,
    TeamOrchestrator,
    load_dotenv,
    split_slack_message,
)


DEFAULT_CONFIG = Path("slack_agent_gateway_config.json")
DEFAULT_STATE = Path("data/slack_agent_gateway.sqlite3")
DEFAULT_OUTBOX = Path("output/integration_outbox/research_conversations")
DEFAULT_LOCK = Path("data/slack_agent_gateway.lock")


class GatewayProcessLock:
    """OS-backed single-instance lock released automatically when the process exits."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError("Slack research gateway is already running") from exc
        self.handle = handle

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None


class EventStore:
    """Durable Slack event idempotency with safe retry after a failure."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS slack_events (
                    event_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    research_id TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    error TEXT
                )
                """
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def claim(self, event_id: str) -> bool:
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT status FROM slack_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if row and row[0] in {"claimed", "complete"}:
                return False
            if row:
                connection.execute(
                    "UPDATE slack_events SET status='claimed', error=NULL, updated_at=CURRENT_TIMESTAMP "
                    "WHERE event_id=?",
                    (event_id,),
                )
            else:
                connection.execute(
                    "INSERT INTO slack_events(event_id, status) VALUES (?, 'claimed')",
                    (event_id,),
                )
            connection.commit()
            return True

    def complete(self, event_id: str, research_id: str) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                "UPDATE slack_events SET status='complete', research_id=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE event_id=?",
                (research_id, event_id),
            )
            connection.commit()

    def fail(self, event_id: str, error: str) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                "UPDATE slack_events SET status='failed', error=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE event_id=?",
                (error[:500], event_id),
            )
            connection.commit()

    def reconcile_stale_claims(self, *, max_age_minutes: int = 30) -> int:
        """Release claims left behind by an interrupted older gateway process."""

        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute(
                "UPDATE slack_events SET status='failed', "
                "error='reconciled_stale_claim_after_restart', updated_at=CURRENT_TIMESTAMP "
                "WHERE status='claimed' AND datetime(updated_at) < datetime('now', ?)",
                (f"-{max_age_minutes} minutes",),
            )
            connection.commit()
            return int(cursor.rowcount)


def load_config(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    channels = payload.get("channels")
    if not isinstance(channels, dict) or not channels:
        raise ValueError("Gateway config requires a non-empty channels map")
    valid_teams = {"research", "traders", "strategy", "risk", "ecosystem"}
    for channel_id, team in channels.items():
        if not str(channel_id).startswith("C") or team not in valid_teams:
            raise ValueError(f"Invalid channel mapping: {channel_id!r} -> {team!r}")
    return payload


def normalize_user_text(text: str) -> str:
    """Remove Slack mention markup but preserve tickers and the user's thesis."""

    return re.sub(r"<@[A-Z0-9]+>", "", text or "").strip()


class SlackAgentGateway:
    def __init__(
        self,
        *,
        config: dict[str, Any],
        state: EventStore,
        orchestrator: TeamOrchestrator,
        workers: int = 2,
    ) -> None:
        self.config = config
        self.state = state
        self.orchestrator = orchestrator
        self.executor = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="agent-room")

    def team_for_channel(self, channel_id: str) -> str | None:
        value = self.config["channels"].get(channel_id)
        return str(value) if value else None

    def accept_event(
        self, body: dict[str, Any], event: dict[str, Any]
    ) -> tuple[ResearchRequest | None, str]:
        if event.get("bot_id") or event.get("subtype"):
            return None, "bot_or_subtype"
        channel_id = str(event.get("channel") or "")
        team = self.team_for_channel(channel_id)
        if not team:
            return None, "unconfigured_channel"
        text = normalize_user_text(str(event.get("text") or ""))
        if not text:
            return None, "empty_text"
        event_id = str(
            body.get("event_id") or event.get("client_msg_id") or event.get("ts") or ""
        )
        if not event_id:
            return None, "missing_event_id"
        if not self.state.claim(event_id):
            return None, "duplicate"
        request = ResearchRequest(
            team=team,
            text=text,
            channel_id=channel_id,
            thread_ts=str(event.get("thread_ts") or event.get("ts") or ""),
            user_id=str(event.get("user") or "unknown"),
            event_id=event_id,
        )
        return request, "accepted"

    def process(self, request: ResearchRequest, client: Any) -> None:
        try:
            result = self.orchestrator.run(request)
            intro = (
                f"*Research room:* `{result.team}` · *Research ID:* `{result.research_id}`\n"
                f"Tickers: `{', '.join(result.tickers) if result.tickers else 'none'}` · "
                "Mode: research-only / no trade execution"
            )
            self._post(client, request.channel_id, request.thread_ts, intro)
            for message in result.messages:
                self._post(client, request.channel_id, request.thread_ts, message)
            self.state.complete(request.event_id, result.research_id)
        except Exception as exc:
            self.state.fail(request.event_id, f"{type(exc).__name__}: {exc}")
            self._post(
                client,
                request.channel_id,
                request.thread_ts,
                "*Η ομαδική έρευνα απέτυχε χωρίς να εκτελεστεί καμία συναλλαγή.*\n"
                f"`{type(exc).__name__}` — έλεγξε το local gateway log και ξαναστείλε το μήνυμα.",
            )

    @staticmethod
    def _post(client: Any, channel_id: str, thread_ts: str, text: str) -> None:
        for chunk in split_slack_message(text):
            client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=chunk)


def build_slack_app(gateway: SlackAgentGateway) -> Any:
    try:
        from slack_bolt import App  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Missing slack-bolt. Install project requirements before starting the gateway."
        ) from exc

    token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    if not token.startswith("xoxb-"):
        raise RuntimeError("SLACK_BOT_TOKEN is missing or is not an xoxb token")
    app = App(token=token)

    @app.event("message")
    def on_message(body: dict[str, Any], event: dict[str, Any], client: Any) -> None:
        request, _reason = gateway.accept_event(body, event)
        if request is None:
            return
        client.chat_postMessage(
            channel=request.channel_id,
            thread_ts=request.thread_ts,
            text=(
                f"Έλαβα την ερώτηση για την ομάδα `{request.team}`. "
                "Συλλέγω στοιχεία και ξεκινώ τον διάλογο των Agents…"
            ),
        )
        gateway.executor.submit(gateway.process, request, client)

    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Research-only Slack gateway for the agent ecosystem."
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    parser.add_argument("--outbox", default=str(DEFAULT_OUTBOX))
    parser.add_argument("--lock", default=str(DEFAULT_LOCK))
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate configuration without connecting to Slack.",
    )
    parser.add_argument(
        "--check-live",
        action="store_true",
        help="Validate both Slack tokens without printing either credential.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    project_root = Path(__file__).resolve().parent
    os.chdir(project_root)
    load_dotenv(Path(args.env_file))
    config = load_config(Path(args.config))
    state = EventStore(Path(args.state))
    reconciled = state.reconcile_stale_claims()
    orchestrator = TeamOrchestrator(outbox=ConversationOutbox(Path(args.outbox)))
    gateway = SlackAgentGateway(
        config=config,
        state=state,
        orchestrator=orchestrator,
        workers=args.workers,
    )
    if args.check or args.check_live:
        print(f"[OK] configured_channels={len(config['channels'])}")
        print("[OK] execution_surface=absent")
        print(f"[OK] outbox={Path(args.outbox).resolve()}")
        print(f"[OK] reconciled_stale_claims={reconciled}")
        if args.check_live:
            bot_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
            app_token = os.getenv("SLACK_APP_TOKEN", "").strip()
            if not bot_token.startswith("xoxb-"):
                raise RuntimeError("SLACK_BOT_TOKEN is missing or invalid")
            if not app_token.startswith("xapp-"):
                raise RuntimeError("SLACK_APP_TOKEN is missing or invalid")
            from slack_sdk import WebClient  # type: ignore

            auth = WebClient(token=bot_token).auth_test()
            socket_probe = WebClient().apps_connections_open(app_token=app_token)
            if not auth.get("ok") or not socket_probe.get("ok"):
                raise RuntimeError("Slack live credential validation failed")
            print(f"[OK] slack_workspace={auth.get('team_id')}")
            print(f"[OK] slack_bot_user={auth.get('user_id')}")
            print("[OK] slack_app_token=valid")
        return 0

    process_lock = GatewayProcessLock(Path(args.lock))
    process_lock.acquire()
    app_token = os.getenv("SLACK_APP_TOKEN", "").strip()
    if not app_token.startswith("xapp-"):
        raise RuntimeError("SLACK_APP_TOKEN is missing or is not an xapp token")
    app = build_slack_app(gateway)
    try:
        from slack_bolt.adapter.socket_mode import SocketModeHandler  # type: ignore
    except ImportError as exc:
        raise RuntimeError("Missing slack-bolt Socket Mode adapter") from exc
    print("[START] Slack research gateway (Socket Mode, research-only)")
    try:
        SocketModeHandler(app, app_token).start()
    finally:
        process_lock.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
