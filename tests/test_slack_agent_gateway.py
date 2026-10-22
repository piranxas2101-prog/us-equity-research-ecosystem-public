import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from slack_agent_gateway import EventStore, SlackAgentGateway, normalize_user_text


class StubOrchestrator:
    pass


class SlackGatewayTests(unittest.TestCase):
    def make_gateway(self, root):
        return SlackAgentGateway(
            config={"channels": {"C_RESEARCH": "research"}},
            state=EventStore(Path(root) / "events.sqlite3"),
            orchestrator=StubOrchestrator(),
            workers=1,
        )

    def test_routes_human_message_and_rejects_duplicate(self):
        with tempfile.TemporaryDirectory() as temp:
            gateway = self.make_gateway(temp)
            body = {"event_id": "Ev1"}
            event = {
                "channel": "C_RESEARCH",
                "ts": "100.200",
                "user": "U1",
                "text": "<@UBOT> analyse $COST",
            }
            request, reason = gateway.accept_event(body, event)
            self.assertEqual(reason, "accepted")
            self.assertEqual(request.team, "research")
            self.assertEqual(request.text, "analyse $COST")
            duplicate, reason = gateway.accept_event(body, event)
            self.assertIsNone(duplicate)
            self.assertEqual(reason, "duplicate")
            gateway.executor.shutdown(wait=False)

    def test_bot_and_unconfigured_channel_are_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            gateway = self.make_gateway(temp)
            request, reason = gateway.accept_event(
                {"event_id": "Ev2"},
                {"channel": "C_RESEARCH", "ts": "1", "text": "hello", "bot_id": "B1"},
            )
            self.assertIsNone(request)
            self.assertEqual(reason, "bot_or_subtype")
            request, reason = gateway.accept_event(
                {"event_id": "Ev3"},
                {"channel": "C_OTHER", "ts": "2", "text": "hello"},
            )
            self.assertIsNone(request)
            self.assertEqual(reason, "unconfigured_channel")
            gateway.executor.shutdown(wait=False)

    def test_failed_event_can_be_retried(self):
        with tempfile.TemporaryDirectory() as temp:
            store = EventStore(Path(temp) / "events.sqlite3")
            self.assertTrue(store.claim("Ev4"))
            store.fail("Ev4", "temporary")
            self.assertTrue(store.claim("Ev4"))
            store.complete("Ev4", "research-4")
            self.assertFalse(store.claim("Ev4"))

    def test_normalize_user_text(self):
        self.assertEqual(normalize_user_text(" <@U123>  $NVDA thesis "), "$NVDA thesis")

    def test_stale_claims_are_reconciled_for_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            store = EventStore(Path(temp) / "events.sqlite3")
            self.assertTrue(store.claim("EvOld"))
            with closing(store._connect()) as connection:
                connection.execute(
                    "UPDATE slack_events SET updated_at='2000-01-01 00:00:00' WHERE event_id='EvOld'"
                )
                connection.commit()
            self.assertEqual(store.reconcile_stale_claims(max_age_minutes=30), 1)
            self.assertTrue(store.claim("EvOld"))


if __name__ == "__main__":
    unittest.main()
