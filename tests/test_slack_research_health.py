import os
import tempfile
import unittest
from pathlib import Path

from slack_research_health import overdue_outbox, stale_claims
from slack_agent_gateway import EventStore


class SlackResearchHealthTests(unittest.TestCase):
    def test_acknowledged_outbox_is_not_overdue(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outbox = root / "outbox"
            sent = outbox / "sent"
            sent.mkdir(parents=True)
            event = outbox / "r_research_conversation.json"
            event.write_text("{}", encoding="utf-8")
            os.utime(event, (0, 0))
            self.assertEqual(overdue_outbox(outbox, sent, max_age_minutes=1), [event.name])
            (sent / event.name).write_text("{}", encoding="utf-8")
            self.assertEqual(overdue_outbox(outbox, sent, max_age_minutes=1), [])

    def test_completed_events_are_not_stale_claims(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "events.sqlite3"
            store = EventStore(state)
            store.claim("Ev1")
            store.complete("Ev1", "research-1")
            self.assertEqual(stale_claims(state, max_age_minutes=0), [])


if __name__ == "__main__":
    unittest.main()
