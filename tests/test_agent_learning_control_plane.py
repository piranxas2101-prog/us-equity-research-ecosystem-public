from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import agent_learning_control_plane as learning


class AgentLearningControlPlaneTests(unittest.TestCase):
    def connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        learning.init_schema(conn)
        return conn

    def test_episode_is_idempotent_and_immutable(self) -> None:
        conn = self.connection()
        self.assertGreaterEqual(learning.summary(conn)["feature_count"], 5)
        kwargs = dict(agent_id="babis", task_id="discovery", run_id="run_1",
                      subject_id="abc", observed_at="2026-09-23T00:00:00+00:00",
                      context={"available": 1}, decision={"rank": 1}, policy_version="v1")
        first, created = learning.record_episode(conn, **kwargs)
        second, created_again = learning.record_episode(conn, **kwargs)
        self.assertEqual(first, second)
        self.assertTrue(created)
        self.assertFalse(created_again)
        with self.assertRaises(ValueError):
            learning.record_episode(conn, **{**kwargs, "decision": {"rank": 2}})

    def test_model_requires_json_and_manual_state_progression(self) -> None:
        conn = self.connection()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "model.json"
            path.write_text(json.dumps({"coefficients": [1.0]}), encoding="utf-8")
            model_id = learning.register_model(
                conn, agent_id="petros", task_id="synthesis", model_version="v1",
                model_type="logistic", model_path=path, dataset_sha256="data",
                feature_schema_sha256="features", training_rows=100,
                metrics={"balanced_accuracy": .7}, gates={"minimum_rows": 100})
            with self.assertRaises(ValueError):
                learning.transition_model(conn, model_id, "champion", "skip shadow", {})
            learning.transition_model(conn, model_id, "shadow", "manual review", {})
            learning.transition_model(conn, model_id, "champion", "validated", {})
            state = conn.execute("SELECT state FROM agent_model_registry WHERE model_id=?", (model_id,)).fetchone()[0]
            self.assertEqual(state, "champion")

    def test_artifact_adapter_records_each_symbol(self) -> None:
        conn = self.connection()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "babis.json"
            path.write_text(json.dumps({"run_id": "r1", "run_timestamp": "2026-09-23T00:00:00Z",
                                        "shortlist": [{"ticker": "ABC"}, {"ticker": "XYZ"}]}), encoding="utf-8")
            result = learning.record_artifact(conn, path, "babis", "discovery", "shortlist", "v1")
            self.assertEqual(result["inserted"], 2)
            self.assertEqual(learning.summary(conn)["episode_count"], 2)

    def test_artifact_rerecord_accepts_old_lineage_context_when_decision_is_same(self) -> None:
        conn = self.connection()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "agent.json"
            payload = {"run_id": "r1", "run_timestamp": "2026-09-23T00:00:00Z",
                       "results": [{"ticker": "ABC", "score": 1}]}
            path.write_text(json.dumps(payload), encoding="utf-8")
            learning.record_episode(
                conn, agent_id="yankos", task_id="strategy", run_id="r1",
                subject_id="ABC", observed_at=payload["run_timestamp"],
                context={"source_artifact_sha256": "old"}, decision=payload["results"][0],
                policy_version="v1",
            )
            result = learning.record_artifact(conn, path, "yankos", "strategy", "results", "v1")
            self.assertEqual(result["existing"], 1)

    def test_artifact_conflict_is_atomic(self) -> None:
        conn = self.connection()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "agent.json"
            base = {"run_id": "r1", "run_timestamp": "2026-09-23T00:00:00Z",
                    "results": [{"ticker": "ABC", "score": 1}]}
            path.write_text(json.dumps(base), encoding="utf-8")
            learning.record_artifact(conn, path, "babis", "discovery", "results", "v1")
            changed = {**base, "results": [{"ticker": "NEW", "score": 1},
                                             {"ticker": "ABC", "score": 2}]}
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaises(ValueError):
                learning.record_artifact(conn, path, "babis", "discovery", "results", "v1")
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM agent_learning_episodes WHERE subject_id='NEW'"
            ).fetchone())

    def test_artifact_records_distinct_playbooks_for_the_same_ticker(self) -> None:
        conn = self.connection()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "agent.json"
            payload = {"run_id": "r1", "run_timestamp": "2026-09-23T00:00:00Z",
                       "results": [{"ticker": "ABC", "score": 1},
                                   {"ticker": "ABC", "score": 2}]}
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = learning.record_artifact(conn, path, "yankos", "strategy", "results", "v1")
            self.assertEqual(result["inserted"], 2)
            subjects = [row[0] for row in conn.execute(
                "SELECT subject_id FROM agent_learning_episodes ORDER BY subject_id"
            )]
            self.assertEqual(len(subjects), 2)
            self.assertTrue(all(value.startswith("ABC::VARIANT:") for value in subjects))

    def test_evaluation_and_memory_are_immutable_and_versioned(self) -> None:
        conn = self.connection()
        episode_id, _ = learning.record_episode(
            conn, agent_id="giannis", task_id="fundamental_quality", run_id="r1",
            subject_id="ABC", observed_at="2026-09-23T00:00:00Z",
            context={"available": True}, decision={"score_final": 4.0},
            policy_version="v1",
        )
        evaluation_id, created = learning.record_evaluation(
            conn, episode_id=episode_id, evaluator_id="independent",
            rubric_version="r1", verdict="partial", score=0.5,
            criteria=[{"criterion_id": "risk", "passed": False}],
            evidence={"run_id": "r1"},
        )
        self.assertTrue(created)
        repeated_id, repeated = learning.record_evaluation(
            conn, episode_id=episode_id, evaluator_id="independent",
            rubric_version="r1", verdict="partial", score=0.5,
            criteria=[{"criterion_id": "risk", "passed": False}],
            evidence={"run_id": "r1"},
        )
        self.assertEqual(evaluation_id, repeated_id)
        self.assertFalse(repeated)
        with self.assertRaises(ValueError):
            learning.record_evaluation(
                conn, episode_id=episode_id, evaluator_id="independent",
                rubric_version="r1", verdict="pass", score=1.0,
                criteria=[], evidence={"run_id": "r1"},
            )
        memory_id, first_version, first_created = learning.remember_version(
            conn, agent_id="giannis", category="evaluation", subject_id="ABC",
            memory_key="r1", content={"verdict": "partial"},
            evidence={"evaluation_id": evaluation_id}, created_by="independent",
        )
        _, repeated_version, repeated_created = learning.remember_version(
            conn, agent_id="giannis", category="evaluation", subject_id="ABC",
            memory_key="r1", content={"verdict": "partial"},
            evidence={"evaluation_id": evaluation_id}, created_by="independent",
        )
        _, second_version, second_created = learning.remember_version(
            conn, agent_id="giannis", category="evaluation", subject_id="ABC",
            memory_key="r1", content={"verdict": "pass"},
            evidence={"evaluation_id": "new"}, created_by="independent",
        )
        self.assertTrue(first_created)
        self.assertFalse(repeated_created)
        self.assertEqual(first_version, repeated_version)
        self.assertTrue(second_created)
        self.assertNotEqual(first_version, second_version)
        current = conn.execute(
            "SELECT current_version_id FROM agent_memory_items WHERE memory_id=?", (memory_id,)
        ).fetchone()[0]
        self.assertEqual(current, second_version)

    def test_lesson_candidate_never_promotes_itself(self) -> None:
        conn = self.connection()
        lesson_id, created = learning.upsert_lesson_candidate(
            conn, target_agent="petros", task_id="fundamental_synthesis",
            rubric_version="r1", criterion_id="rationale", support_count=20,
            proposal={"change": "require rationale"}, evidence={"run_id": "r1"},
        )
        self.assertTrue(created)
        _, created_again = learning.upsert_lesson_candidate(
            conn, target_agent="petros", task_id="fundamental_synthesis",
            rubric_version="r1", criterion_id="rationale", support_count=25,
            proposal={"change": "require rationale"}, evidence={"run_id": "r2"},
        )
        self.assertFalse(created_again)
        status, support = conn.execute(
            "SELECT status,support_count FROM agent_lesson_candidates WHERE lesson_id=?",
            (lesson_id,),
        ).fetchone()
        self.assertEqual(status, "candidate")
        self.assertEqual(support, 25)


if __name__ == "__main__":
    unittest.main()
