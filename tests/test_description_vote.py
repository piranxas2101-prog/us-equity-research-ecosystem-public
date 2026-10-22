"""Tests for the local-LLM description vote (a fake model, no Ollama)."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from research_registry import description_vote as dv

NODES = {node: None for node in (
    "technology", "technology.software", "technology.software.applications", "consumer_discretionary",
    "consumer_discretionary.services_housing", "consumer_discretionary.goods_retail", "real_estate")}


class FakeModel:
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def models(self):
        return {"qwen2.5:3b": "digest123456789"}

    def generate(self, *, model, system, prompt, schema):
        self.prompts.append((prompt, schema))
        return json.dumps({"choice": self.answers.pop(0)})


class DescriptionVoteTests(unittest.TestCase):
    def test_walks_down_the_real_taxonomy_and_stops_at_max_depth(self):
        model = FakeModel(["consumer_discretionary", "services_housing"])
        node, trail = dv.descend(model, "m", "EDU Co", "Private tutoring and test prep.", NODES)
        self.assertEqual(node, "consumer_discretionary.services_housing")
        self.assertEqual(trail, ["consumer_discretionary", "services_housing"])
        self.assertEqual(model.prompts[1][1]["properties"]["choice"]["enum"], ["goods_retail", "services_housing", "unclear"])

    def test_unclear_or_invalid_answers_stop_at_the_last_sure_level(self):
        self.assertEqual(dv.descend(FakeModel(["technology", "unclear"]), "m", "X", "d", NODES)[0], "technology")
        self.assertEqual(dv.descend(FakeModel(["not_a_sector"]), "m", "X", "d", NODES)[0], None)

    def test_cache_is_bound_to_the_description_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "votes.sqlite3"
            cache = dv.open_cache(path)
            model = FakeModel(["technology", "software"])
            self.assertEqual(dv.vote_for(cache, model, "m", "digest123456789", "ABC", "ABC", "Makes software.", NODES),
                             "technology.software")
            self.assertEqual(dv.vote_for(cache, FakeModel([]), "m", "digest123456789", "ABC", "ABC", "Makes software.", NODES),
                             "technology.software")  # cached: no model call
            cache.close()
            votes = dv.cached_votes({"ABC": {"description": "Makes software."}}, path)
            self.assertEqual(votes["ABC"][0].source, "description")
            self.assertEqual(dv.cached_votes({"ABC": {"description": "Now sells houses."}}, path), {})


if __name__ == "__main__":
    unittest.main()
