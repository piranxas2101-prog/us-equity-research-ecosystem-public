from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import agent_learning_control_plane as learning
import fundamental_learning_evaluator as evaluator
import market_intel_db
import outcome_tracker


class FundamentalLearningEvaluatorTests(unittest.TestCase):
    def test_evaluator_is_independent_and_does_not_change_models_or_sokratis(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "memory.db"
            output = Path(folder) / "evaluation.json"
            with closing(market_intel_db.connect(db_path)) as conn:
                learning.init_schema(conn)
                decisions = {
                    "babis": {"ticker": "ABC", "source_base": {}, "fundamental_coverage": 80.0,
                              "unknown_contract_sha256": "u", "classification_status": "classified"},
                    "giannis": {"ticker": "ABC", "score_final": 4.0, "giannis_tier": "watch",
                                "risk_level": "medium", "unknown_contract_sha256": "u"},
                    "petros": {"ticker": "ABC", "petros_verdict": "watch", "rationale": ["x"],
                               "research_readiness": "ready", "valuation_range": {"low": 1},
                               "unknown_contract_sha256": "u"},
                }
                for agent_id, decision in decisions.items():
                    learning.record_episode(
                        conn, agent_id=agent_id, task_id=evaluator.AGENT_TASKS[agent_id],
                        run_id="run-1", subject_id="ABC", observed_at="2026-09-23T00:00:00Z",
                        context={"source": "test"}, decision=decision, policy_version="v1",
                    )
                conn.commit()
                before_models = conn.execute("SELECT COUNT(*) FROM agent_model_registry").fetchone()[0]
            result = evaluator.run(run_id="run-1", db_path=db_path, output_path=output,
                                   minimum_lesson_support=1)
            self.assertEqual(result["evaluation_count"], 3)
            self.assertEqual(result["verdict_counts"], {"pass": 3})
            self.assertFalse(result["sokratis_role_changed"])
            self.assertFalse(result["active_decisions_changed"])
            with closing(market_intel_db.connect(db_path)) as conn:
                learning.init_schema(conn)
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM agent_model_registry"
                ).fetchone()[0], before_models)
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM agent_memory_versions"
                ).fetchone()[0], 3)
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM agent_learning_episodes WHERE agent_id='sokratis'"
                ).fetchone()[0], 0)

    def test_repeated_gap_creates_candidate_only(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "memory.db"
            with closing(market_intel_db.connect(db_path)) as conn:
                learning.init_schema(conn)
                for ticker in ("ABC", "XYZ"):
                    learning.record_episode(
                        conn, agent_id="petros", task_id="fundamental_synthesis",
                        run_id="run-1", subject_id=ticker,
                        observed_at="2026-09-23T00:00:00Z", context={"source": "test"},
                        decision={"ticker": ticker, "petros_verdict": "watch"},
                        policy_version="v1",
                    )
                conn.commit()
            result = evaluator.run(run_id="run-1", db_path=db_path,
                                   output_path=Path(folder) / "evaluation.json",
                                   minimum_lesson_support=2)
            self.assertTrue(result["lesson_candidates"])
            with closing(market_intel_db.connect(db_path)) as conn:
                statuses = {row[0] for row in conn.execute(
                    "SELECT status FROM agent_lesson_candidates"
                )}
            self.assertEqual(statuses, {"candidate"})

    def test_matured_price_outcome_is_linked_without_changing_decision(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "memory.db"
            with closing(market_intel_db.connect(db_path)) as conn:
                learning.init_schema(conn)
                outcome_tracker.init_schema(conn)
                episode_id, _ = learning.record_episode(
                    conn, agent_id="giannis", task_id="fundamental_quality",
                    run_id="run-1", subject_id="ABC", observed_at="2026-09-23T00:00:00Z",
                    context={"source": "test"}, decision={"ticker": "ABC"},
                    policy_version="v1",
                )
                conn.execute(
                    "INSERT INTO candidate_setups(source_file,source_run_timestamp,ticker,observed_at,"
                    "baseline_price,payload_json,run_id) VALUES(?,?,?,?,?,?,?)",
                    ("test", "2026-09-23T00:00:00Z", "ABC", "2026-09-23T00:00:00Z",
                     10.0, "{}", "run-1"),
                )
                setup_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO price_outcomes(setup_id,horizon_days,target_session_date,"
                    "price_session_date,price_source,price_adjustment,observed_at,price,return_pct,"
                    "payload_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (setup_id, 5, "2026-09-30", "2026-09-30", "test", "raw_close",
                     "2026-09-30T22:00:00Z", 11.0, 10.0, "{}"),
                )
                conn.commit()
                before = conn.execute(
                    "SELECT decision_sha256 FROM agent_learning_episodes WHERE episode_id=?",
                    (episode_id,),
                ).fetchone()[0]
                synced = evaluator.sync_matured_outcomes(conn)
                after = conn.execute(
                    "SELECT decision_sha256 FROM agent_learning_episodes WHERE episode_id=?",
                    (episode_id,),
                ).fetchone()[0]
                outcome = conn.execute(
                    "SELECT label,horizon_days FROM agent_learning_outcomes WHERE episode_id=?",
                    (episode_id,),
                ).fetchone()
            self.assertEqual(synced["inserted"], 1)
            self.assertEqual(before, after)
            self.assertEqual((outcome[0], outcome[1]), (10.0, 5))


if __name__ == "__main__":
    unittest.main()
