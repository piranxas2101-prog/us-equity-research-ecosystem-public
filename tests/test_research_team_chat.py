import json
import tempfile
import unittest
from pathlib import Path

from research_team_chat import (
    ConversationOutbox,
    OUTBOX_SCHEMA,
    ResearchRequest,
    TeamOrchestrator,
    contains_execution_language,
    extract_tickers,
    split_slack_message,
)


class FakeProvider:
    def build(self, ticker):
        return {
            "ticker": ticker,
            "observed_at": "2026-09-01T00:00:00+00:00",
            "status": "fresh",
            "price": {"last": 100.0},
            "fundamentals": {"forward_pe": 20.0},
            "news": [],
            "sources": [
                {
                    "name": "test source",
                    "url": f"https://example.test/{ticker}",
                    "observed_at": "2026-09-01T00:00:00+00:00",
                }
            ],
            "warnings": [],
        }


class FakeEngine:
    def __init__(self):
        self.calls = []

    def generate(self, agent, prompt):
        self.calls.append((agent, prompt))
        return f"{agent} evidence-based view"


class FakePanelEngine:
    def __init__(self):
        self.calls = []

    def generate_panel(self, team, agents, prompt):
        self.calls.append((team, tuple(agents), prompt))
        return "ROUND 1 labeled views\nROUND 2 named disagreement\nFINAL synthesis"


class ResearchTeamChatTests(unittest.TestCase):
    def test_extracts_explicit_tickers_and_deduplicates(self):
        self.assertEqual(extract_tickers("Compare COST with NVDA and COST"), ["COST", "NVDA"])
        self.assertEqual(extract_tickers("$COST API-first smoke"), ["COST"])

    def test_execution_language_is_detected_but_not_executed(self):
        self.assertTrue(contains_execution_language("buy 100 COST"))
        self.assertTrue(contains_execution_language("εκτέλεσε την αγορά"))
        self.assertFalse(contains_execution_language("analyse the COST long thesis"))

    def test_team_conversation_has_two_rounds_and_synthesis(self):
        engine = FakeEngine()
        with tempfile.TemporaryDirectory() as temp:
            outbox = ConversationOutbox(Path(temp))
            orchestrator = TeamOrchestrator(
                provider=FakeProvider(), engine=engine, outbox=outbox
            )
            result = orchestrator.run(
                ResearchRequest(
                    team="research",
                    text="My thesis for $COST",
                    channel_id="C123",
                    thread_ts="123.456",
                    user_id="U123",
                    event_id="Ev123",
                )
            )
            self.assertEqual(result.tickers, ["COST"])
            self.assertEqual(result.event["schema"], OUTBOX_SCHEMA)
            self.assertFalse(result.event["execution_performed"])
            self.assertEqual(result.event["discussion"]["lead"], "petros")
            self.assertEqual(len(engine.calls), 5)  # 3 openings + moderated challenge + final
            self.assertTrue(result.outbox_path.exists())
            saved = json.loads(result.outbox_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["research_id"], result.research_id)
            self.assertEqual(saved["sources"][0]["url"], "https://example.test/COST")

    def test_outbox_is_idempotent_and_immutable(self):
        with tempfile.TemporaryDirectory() as temp:
            outbox = ConversationOutbox(Path(temp))
            event = {
                "schema": OUTBOX_SCHEMA,
                "research_id": "research-1",
                "event_sha256": "abc",
            }
            first = outbox.write(event)
            second = outbox.write(event)
            self.assertEqual(first, second)
            changed = dict(event, event_sha256="different")
            with self.assertRaises(FileExistsError):
                outbox.write(changed)

    def test_production_panel_mode_uses_one_model_call(self):
        engine = FakePanelEngine()
        orchestrator = TeamOrchestrator(provider=FakeProvider(), engine=engine)
        result = orchestrator.run(
            ResearchRequest(
                team="ecosystem",
                text="Discuss $NVDA",
                channel_id="C999",
                thread_ts="999.1",
                user_id="U999",
                event_id="Ev999",
            )
        )
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(len(engine.calls[0][1]), 9)
        self.assertIn("Ομαδική συζήτηση Agents", result.messages[0])
        self.assertFalse(result.event["execution_performed"])

    def test_panel_prompt_requires_greek_for_greek_user_text(self):
        engine = FakePanelEngine()
        orchestrator = TeamOrchestrator(provider=FakeProvider(), engine=engine)
        orchestrator.run(
            ResearchRequest(
                team="research",
                text="Ανάλυσε τη θέση μου για $COST",
                channel_id="C999",
                thread_ts="999.2",
                user_id="U999",
                event_id="EvGreek",
            )
        )
        self.assertIn('"reply_language":"Greek"', engine.calls[0][2])

    def test_slack_chunks_stay_within_limit(self):
        chunks = list(split_slack_message("a" * 9001, limit=4500))
        self.assertEqual(len(chunks), 3)
        self.assertTrue(all(len(chunk) <= 4500 for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
