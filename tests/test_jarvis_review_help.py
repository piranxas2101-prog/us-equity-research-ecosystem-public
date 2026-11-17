"""Jarvis does the owner's homework (owner 2026-11-17, "do both"): of the trained models only the one worth a look,
and for each classification review a suggestion with its reason, so approving is one click. Nothing is decided here."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import jarvis_classify as jc
import jarvis_models as jmod


def registry(root: Path, rows: list[tuple]) -> None:
    path = root / "data" / "market_intel.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE agent_model_registry (model_id TEXT, agent_id TEXT, task_id TEXT, model_version TEXT,"
                     " state TEXT, training_rows INTEGER, metrics_json TEXT, created_at TEXT)")
        conn.executemany("INSERT INTO agent_model_registry VALUES (?,?,?,?,?,?,?,?)", rows)
        conn.commit()


def model(i: int, auc: float, state: str = "candidate", status: str = "walk_forward_validated",
          task: str = "fundamental_only_synthesis") -> tuple:
    return (f"m{i}", "petros", task, f"2026-11-{i:02d}", state, 25000 + i,
            json.dumps({"roc_auc": auc, "status": status}), f"2026-11-{i:02d}T03:00:00+00:00")


class ModelFilterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_legacy_mixed_model_is_never_worth_a_look(self):
        # owner 2026-11-17: it uses trader signals and prices, which Petros may not see, and Petros does not use it
        registry(self.root, [model(i, 0.615, task="fundamental_synthesis") for i in range(1, 25)])
        out = jmod.worth(self.root)
        self.assertEqual((out["worth"], out["unused"]), ([], 24))

    def test_a_shadow_model_waits_for_twenty_nights(self):
        registry(self.root, [model(i, 0.615) for i in range(1, 11)])
        out = jmod.worth(self.root)
        self.assertEqual((out["worth"], out["in_shadow"]), ([], 10))

    def test_without_a_champion_only_the_latest_validated_is_worth_a_look(self):
        registry(self.root, [model(i, 0.615) for i in range(1, 25)])
        out = jmod.worth(self.root)
        self.assertEqual((out["candidates"], [m["model_id"] for m in out["worth"]], out["superseded"]), (24, ["m24"], 23))
        self.assertIn("δεν υπάρχει champion", out["worth"][0]["reason"])

    def test_with_a_champion_a_candidate_must_beat_it(self):
        registry(self.root, [model(1, 0.62, "champion"), *[model(i, 0.615) for i in range(2, 22)], model(22, 0.625)])
        self.assertEqual(jmod.worth(self.root)["worth"], [])
        registry_root = Path(self.tmp.name) / "b"
        registry(registry_root, [model(1, 0.60, "champion"), *[model(i, 0.60) for i in range(2, 22)], model(22, 0.615)])
        out = jmod.worth(registry_root)
        self.assertEqual([m["model_id"] for m in out["worth"]], ["m22"])
        self.assertIn("+0.015", out["worth"][0]["reason"])

    def test_an_unvalidated_latest_is_not_proposed(self):
        registry(self.root, [*[model(i, 0.615) for i in range(1, 22)], model(22, 0.70, status="insufficient_rows")])
        self.assertEqual([m["model_id"] for m in jmod.worth(self.root)["worth"]], ["m21"])

    def test_no_registry_is_unknown(self):
        self.assertIsNone(jmod.worth(self.root))


def item(reason: str, votes: list[tuple], advisory: list[tuple] = (), current: str | None = None) -> dict:
    return {"ticker": "X", "venue_code": "Q", "reason": reason, "current_node": current,
            "votes": [{"source": s, "basis": b, "node": n} for s, b, n in votes],
            "advisory_votes": [{"source": s, "basis": b, "node": n} for s, b, n in advisory]}


class ClassificationSuggestionTests(unittest.TestCase):
    def test_sources_agreeing_on_the_sector_win(self):
        s = jc.suggest(item("no_two_sources_agree",
                            [("profile", "explicit_provider_alias", "industrials.business_services.commercial"),
                             ("sec", "sic_range_sector_only", "financials")],
                            [("description", "local_llm", "industrials.capital_goods")]))
        self.assertEqual((s["decision"], s["node"]), ("choose", "industrials.business_services.commercial"))
        self.assertIn("industrials", s["reason"])

    def test_suggesting_the_current_node_is_a_confirmation(self):
        s = jc.suggest(item("no_two_sources_agree",
                            [("directory", "explicit_internal_industry_crosswalk", "industrials.business_services.commercial"),
                             ("profile", "explicit_provider_alias", "industrials.business_services.commercial")],
                            current="industrials.business_services.commercial"))
        self.assertEqual((s["decision"], s["node"]), ("confirm", "industrials.business_services.commercial"))

    def test_a_tie_gets_no_suggestion(self):
        s = jc.suggest(item("no_two_sources_agree", [("profile", "explicit_provider_alias", "materials.x"),
                                                     ("directory", "explicit_internal_industry_crosswalk", "energy.y")]))
        self.assertIsNone(s["decision"])
        self.assertIn("δεν", s["reason"])

    def test_prior_approval_is_kept(self):
        s = jc.suggest(item("would_contradict_prior_approval", [("profile", "explicit_provider_alias", "a.b")],
                            current="c.d"))
        self.assertEqual((s["decision"], s["node"]), ("confirm", "c.d"))

    def test_spac_waits_for_a_second_signal(self):
        s = jc.suggest(item("probable_spac_needs_second_signal", [("profile", "explicit_provider_alias", "financials.spac")]))
        self.assertIsNone(s["decision"])

    def test_strength_is_reported(self):
        strong = jc.suggest(item("no_two_sources_agree", [("profile", "explicit_provider_alias", "tech.a"),
                                                          ("directory", "explicit_internal_industry_crosswalk", "tech.b")],
                                 [("description", "local_llm", "health.c")]))
        self.assertEqual(strong["confidence"], "strong")


if __name__ == "__main__":
    unittest.main()
