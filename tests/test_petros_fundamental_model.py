"""Petros's fundamental-only model, in shadow (owner 2026-11-17).

The nightly model was trained on trader signals and price data, which Petros (fundamental-only) must not see, and it
is not used. This one is trained on fundamental fields only, keeps unknown apart from zero, is registered as its own
task, and Petros records its probability beside each decision without changing any score."""
from __future__ import annotations

from pathlib import Path
import unittest

import petros_features as pf
import petros_trainer as pt

TECHNICAL_OR_TRADER = ("rsi", "return_", "volume", "volatility", "price_position", "distance_from", "macd", "atr_",
                       "technical_timing", "smart_money", "darkpool", "yankos", "periklis", "share_price",
                       "sub_1usd", "feedback_count", "rank")


def decision(score: float, tier: str, flags: list[str] | None = None, **extra) -> dict:
    return {"giannis_score": score, "giannis_tier": tier, "risk_level": "Medium", "dilution_flags": flags or [],
            "catalyst_direction": "positive", "instrument_type": "common", "rsi_14": 70, "yankos_signal_count": 3,
            **extra}


class FeatureTests(unittest.TestCase):
    def test_no_technical_or_trader_feature(self):
        names = pf.feature_names_from_rows([decision(7, "Core Watchlist")], feature_set="fundamental_only")
        bad = [n for n in names if any(word in n for word in TECHNICAL_OR_TRADER)]
        self.assertEqual(bad, [])
        self.assertIn("giannis_score", names)
        self.assertIn("giannis_tier_core_watchlist", names)

    def test_unknown_stays_apart_from_zero(self):
        known = pf.build_features({"giannis_score": 0.0}, feature_set="fundamental_only")
        missing = pf.build_features({}, feature_set="fundamental_only")
        self.assertEqual((known["giannis_score_known"], missing["giannis_score_known"]), (1.0, 0.0))

    def test_legacy_features_are_unchanged(self):
        self.assertIn("rsi_14", pf.build_features(decision(7, "Core Watchlist")))


class TrainerTests(unittest.TestCase):
    def examples(self, n: int = 60) -> list[dict]:
        out = []
        for i in range(n):
            good = i % 2 == 0
            out.append({"ticker": f"T{i}", "source_run_timestamp": f"2026-10-{i % 28 + 1:02d}T00:00:00",
                        "label": 1 if good else 0,
                        "payload": decision(8 if good else 3, "Core Watchlist" if good else "Avoid For Now")})
        return out

    def test_fundamental_model_records_its_feature_set(self):
        model, status = pt.train_model(self.examples(), 20, 2.0, 20, feature_set="fundamental_only")
        self.assertIsNotNone(model, status)
        self.assertEqual(model["feature_set"], "fundamental_only")
        self.assertFalse([n for n in model["feature_names"] if any(w in n for w in TECHNICAL_OR_TRADER)])

    def test_registered_as_its_own_task_and_file(self):
        self.assertEqual(pt.FEATURE_SETS["fundamental_only"]["task_id"], "fundamental_only_synthesis")
        self.assertEqual(pt.FEATURE_SETS["fundamental_only"]["model"], Path("models/petros_model_fundamental_only.json"))
        self.assertEqual(pt.FEATURE_SETS["legacy"]["model"], Path("models/petros_model_latest.json"))


class ShadowScoringTests(unittest.TestCase):
    def test_shadow_probability_changes_no_score(self):
        import petros_agent

        model, _ = pt.train_model(TrainerTests().examples(), 20, 2.0, 20, feature_set="fundamental_only")
        row = decision(8, "Core Watchlist")
        before = dict(row)
        probability = petros_agent.shadow_probability(model, row)
        self.assertTrue(0.0 <= probability <= 1.0)
        self.assertEqual(row, before)

    def test_a_legacy_model_gives_no_shadow_probability(self):
        import petros_agent

        model, _ = pt.train_model(TrainerTests().examples(), 20, 2.0, 20)
        self.assertIsNone(petros_agent.shadow_probability(model, decision(8, "Core Watchlist")))


class ReviewShadowTests(unittest.TestCase):
    def test_review_attaches_the_shadow_probability_only_in_fundamental_mode(self):
        import petros_agent

        model, _ = pt.train_model(TrainerTests().examples(), 20, 2.0, 20, feature_set="fundamental_only")
        row = {**decision(8, "Core Watchlist"), "score": 7.1}
        petros_agent.attach_shadow(row, model)
        self.assertIsInstance(row["ml_shadow_probability"], float)
        self.assertEqual(row["score"], 7.1)
        empty = decision(8, "Core Watchlist")
        petros_agent.attach_shadow(empty, None)
        self.assertIsNone(empty["ml_shadow_probability"])


if __name__ == "__main__":
    unittest.main()


class LegacyTrainingStoppedTests(unittest.TestCase):
    """Owner 2026-11-17: the legacy mixed model's nightly training is stopped; only the fundamental-only one trains."""

    def test_runners_train_only_the_fundamental_model(self):
        root = Path(__file__).resolve().parents[1]
        ps1 = (root / "run_daily_dual_watchlist.ps1").read_text(encoding="utf-8-sig")
        calls = [line for line in ps1.splitlines() if "petros_trainer.py" in line and not line.strip().startswith("#")]
        self.assertTrue(calls)
        joined = "\n".join(ps1.splitlines())
        self.assertNotIn('"petros_trainer.py",\n    "--horizon-days", "20",\n    "train"\n)', joined)
        self.assertEqual(joined.count("petros_trainer.py"), 1)
        self.assertIn('"--feature-set", "fundamental_only"', joined)
        pipeline = (root / "daily_pipeline.py").read_text(encoding="utf-8")
        trainer_calls = [line for line in pipeline.splitlines() if '"petros_trainer.py"' in line]
        self.assertEqual(len(trainer_calls), 1)
        self.assertIn("fundamental_only", pipeline[pipeline.index('"petros_trainer.py"'):][:200])
        self.assertNotIn("output/petros_training_latest.md", ps1 + pipeline)
